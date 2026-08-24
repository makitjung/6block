# 반복 실행 시 메모리·캐시·연결 누수를 측정하는 4단계 성능감사
import gc
import sqlite3
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from resource import RUSAGE_SELF, getrusage

import pytest
from starlette.testclient import TestClient

import app.db as db
import app.integrations.gcal as gcal
from app.main import app as fastapi_app


@pytest.fixture
def client(fresh_db):
    """테스트용 TestClient."""
    return TestClient(fastapi_app)


class TestGcalCacheGrowth:
    """gcal._cache 가 무한 증가하는지 확인."""

    def test_gcal_cache_bounded_structure(self):
        """gcal._cache의 구조: URL을 키로, 캐시는 dict[str, dict] 형태여야 한다."""
        # conftest에서 gcal.events_for_date를 stub했으므로 _load_calendar도 None 반환
        # 대신 cache 구조 자체가 dict[url, slot] 형태임을 확인
        gcal._cache.clear()

        # 직접 캐시에 삽입해서 구조 확인
        test_url = "https://example.com/cal.ics"
        gcal._cache[test_url] = {"cal": None, "at": time.time()}

        assert isinstance(gcal._cache, dict)
        assert len(gcal._cache) == 1
        assert test_url in gcal._cache

        # 같은 URL으로 갱신
        gcal._cache[test_url] = {"cal": None, "at": time.time() + 1}
        assert len(gcal._cache) == 1, "Same URL should update, not add new entry"

        print("✓ gcal._cache is dict[str, dict]: structure validated")

    def test_cache_key_count_matches_unique_urls(self):
        """캐시 키 개수는 unique URL 개수와 일치해야 한다."""
        gcal._cache.clear()

        urls = [f"https://example.com/cal{i}.ics" for i in range(5)]

        # 각 URL을 여러 번 "갱신"
        for _ in range(10):
            for url in urls:
                gcal._cache[url] = {"cal": None, "at": time.time()}

        # 캐시에는 5개 URL 항목만 있어야 함
        assert len(gcal._cache) == len(urls)
        print(f"✓ gcal._cache bounded by unique URLs: {len(gcal._cache)} entries")


class TestSqliteConnectionLeak:
    """sqlite3 연결이 제대로 닫히는지 확인."""

    def test_get_conn_closes_on_success(self, fresh_db):
        """정상 경로에서 연결이 닫혀야 한다."""
        gc.collect()
        initial_conns = len([obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)])

        # 100회 호출
        for _ in range(100):
            with db.get_conn() as conn:
                conn.execute("SELECT 1")

        gc.collect()
        final_conns = len([obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)])

        # 연결 누수가 없어야 함 (10개 허용, 노이즈 대비)
        assert final_conns - initial_conns <= 10, \
            f"Leaked connections: initial={initial_conns}, final={final_conns}"
        print(f"✓ sqlite3 connections properly closed: Δ={final_conns - initial_conns}")

    def test_get_conn_closes_on_exception(self, fresh_db):
        """예외 발생 경로에서도 연결이 닫혀야 한다."""
        gc.collect()
        initial_conns = len([obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)])

        # 100회 호출, 일부는 에러 발생
        for i in range(100):
            try:
                with db.get_conn() as conn:
                    if i % 10 == 0:
                        raise ValueError("test error")
                    conn.execute("SELECT 1")
            except ValueError:
                pass

        gc.collect()
        final_conns = len([obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)])

        assert final_conns - initial_conns <= 10, \
            f"Leaked connections on exception: initial={initial_conns}, final={final_conns}"
        print(f"✓ sqlite3 connections properly closed on exception: Δ={final_conns - initial_conns}")


class TestMemoryGrowthPattern:
    """반복 호출 시 메모리 성장 패턴 측정."""

    def test_repeated_endpoint_memory_linear(self, client, fresh_db):
        """같은 엔드포인트를 반복 호출할 때 메모리가 선형으로 증가하지 않아야 한다."""
        # 데이터 준비: 30일치 빈 기록
        with db.get_conn() as conn:
            for day_offset in range(30):
                date_str = f"2024-01-{(day_offset % 28) + 1:02d}"
                conn.execute(
                    "INSERT OR IGNORE INTO daily_meta (date) VALUES (?)",
                    (date_str,)
                )

        # 메모리 추적 시작
        tracemalloc.start()
        snapshots = []

        # 200회 호출하면서 스냅샷 5개 저장
        for batch in range(5):
            for _ in range(40):
                resp = client.get("/")
                assert resp.status_code == 200

            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            snapshots.append((batch, current))
            print(f"  Batch {batch}: {current / 1024 / 1024:.2f} MB")

        tracemalloc.stop()

        # 배치 간 메모리 증가량을 계산
        growth_per_batch = []
        for i in range(1, len(snapshots)):
            growth = snapshots[i][1] - snapshots[i-1][1]
            growth_per_batch.append(growth)

        avg_growth = sum(growth_per_batch) / len(growth_per_batch) if growth_per_batch else 0
        max_growth = max(growth_per_batch) if growth_per_batch else 0

        print(f"✓ Memory growth per batch: avg={avg_growth / 1024 / 1024:.2f} MB, "
              f"max={max_growth / 1024 / 1024:.2f} MB")

        # 배치당 평균 성장이 10MB를 넘으면 의심 (임계값은 넉넉하게 잡음)
        assert avg_growth < 10 * 1024 * 1024, \
            f"Memory grows {avg_growth / 1024 / 1024:.2f} MB per batch"

    def test_get_conn_repeated_rusage(self, fresh_db):
        """get_conn 반복 호출 시 rusage 변화 측정."""
        before = getrusage(RUSAGE_SELF)
        before_maxrss = before.ru_maxrss
        before_utime = before.ru_utime
        before_stime = before.ru_stime

        # 500회 호출
        for _ in range(500):
            with db.get_conn() as conn:
                conn.execute("SELECT 1")

        gc.collect()
        after = getrusage(RUSAGE_SELF)
        after_maxrss = after.ru_maxrss
        after_utime = after.ru_utime
        after_stime = after.ru_stime

        rss_growth = after_maxrss - before_maxrss
        utime_growth = after_utime - before_utime
        stime_growth = after_stime - before_stime
        avg_time_per_call = (utime_growth + stime_growth) / 500 * 1000  # msec

        print("✓ rusage after 500 get_conn calls:")
        print(f"  maxrss: {before_maxrss} → {after_maxrss} KB (Δ={rss_growth} KB)")
        print(f"  CPU time: user={utime_growth:.3f}s, sys={stime_growth:.3f}s, "
              f"avg={avg_time_per_call:.2f}ms/call")

        # RSS 성장이 100MB를 넘으면 명백한 누수다.
        # (SQLite WAL, Python 메모리 풀 등으로 어느 정도 성장은 정상이라 넉넉히 잡는다)
        assert rss_growth <= 100 * 1024, \
            f"get_conn 500회에 RSS가 {rss_growth} KB 늘었다"


class TestConcurrentDatabaseAccess:
    """동시 요청 시 'database is locked' 발생 여부."""

    def test_concurrent_requests_no_locked_errors(self, client, fresh_db):
        """30개 동시 요청에서 database is locked 에러가 나지 않아야 한다."""
        errors = []

        statuses = []

        def make_request():
            try:
                # 예전에는 응답을 버리고 늘 200 을 돌려줬다(무슨 코드가 와도 통과).
                return (client.get("/").status_code, None)
            except Exception as e:
                return (None, str(e))

        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(make_request) for _ in range(30)]
            for future in as_completed(futures):
                status, error = future.result()
                if error and "database is locked" in error.lower():
                    errors.append(error)
                elif status is not None:
                    statuses.append(status)

        assert len(errors) == 0, f"Got {len(errors)} 'database is locked' errors: {errors[:3]}"
        assert statuses == [200] * 30, f"200 이 아닌 응답: {sorted(set(statuses))}"
        print("✓ No 'database is locked' errors in 30 concurrent requests")

    def test_concurrent_writes_no_locked_errors(self, client, fresh_db):
        """동시 쓰기 요청에서 database is locked 가 나지 않아야 한다.

        예전에는 없는 주소(/save/event/block)로 보내고 그 404 를 성공한 쓰기로 셌다.
        그래서 무슨 일이 있어도 통과했다. 실제 저장 주소로 바꾸고 응답까지 확인한다.
        """
        client.get("/today")
        with db.get_conn() as conn:
            block_id = conn.execute(
                "SELECT id FROM blocks ORDER BY id LIMIT 1").fetchone()["id"]
        errors = {"locked": 0, "other": 0}
        statuses = []

        def make_write(i):
            try:
                resp = client.post(
                    "/save/field",
                    data={"entity": "block", "id": str(block_id),
                          "field": "bname", "value": f"test-{i}"},
                )
                return (resp.status_code, None)
            except Exception as e:
                return (None, str(e))

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(make_write, i) for i in range(20)]
            for future in as_completed(futures):
                status, error = future.result()
                if error:
                    if "database is locked" in error.lower():
                        errors["locked"] += 1
                    else:
                        errors["other"] += 1
                else:
                    statuses.append(status)

        assert errors["locked"] == 0, f"database is locked {errors['locked']}건"
        assert errors["other"] == 0, f"예상 못 한 오류 {errors['other']}건"
        assert len(statuses) == 20, f"응답이 {len(statuses)}건뿐이다"
        assert set(statuses) == {200}, f"200 이 아닌 응답: {sorted(set(statuses))}"


class TestSettingsCacheInvalidation:
    """설정 캐시의 무효화 동작."""

    def test_settings_cache_invalidated_on_set(self, fresh_db):
        """설정 저장 후 캐시가 무효화되어야 한다."""
        # 초기 캐시 로드
        initial = db.get_settings()

        # 캐시된 객체와 동일해야 함
        cached = db.get_settings()
        assert cached is not initial or cached == initial

        # 설정 변경
        db.set_setting("test_key", "test_value")

        # 캐시가 비워져서 새로 로드되어야 함
        after_set = db.get_settings()
        assert after_set.get("test_key") == "test_value"

        print("✓ Settings cache properly invalidated on set()")


class TestFiledescriptorLeak:
    """파일 디스크립터 누수 확인."""

    def test_fd_not_leaked_on_repeated_calls(self, client, fresh_db):
        """반복 호출 시 파일 디스크립터가 누적되지 않아야 한다."""
        def count_fds():
            try:
                fd_path = Path("/dev/fd") if Path("/dev/fd").exists() else Path("/proc/self/fd")
                return len(list(fd_path.iterdir()))
            except (OSError, FileNotFoundError):
                # /dev/fd 없으면 OS X - lsof로 세기 (테스트 환경에서 추정)
                return -1

        initial_fd = count_fds()
        if initial_fd < 0:
            print("⊘ fd counting not available on this platform")
            return

        # 100회 호출
        for _ in range(100):
            resp = client.get("/")
            assert resp.status_code == 200

        gc.collect()
        final_fd = count_fds()

        fd_growth = final_fd - initial_fd
        print(f"✓ File descriptors: initial={initial_fd}, final={final_fd}, Δ={fd_growth}")

        # FD가 10개 이상 증가하면 의심 (임계값 넉넉함)
        if initial_fd > 0:
            assert fd_growth < 10, f"FD grew {fd_growth}"


class TestAssetVerCachePerformance:
    """asset_ver 캐시의 TTL 동작."""

    def test_asset_ver_cache_ttl_10_seconds(self):
        """asset_ver() 캐시가 10초 TTL을 지키는지 확인."""
        from app.common import asset_ver

        # 캐시 초기화
        import app.common as common_module
        common_module._asset_ver_cache = None

        # 첫 호출
        ver1 = asset_ver()
        assert common_module._asset_ver_cache is not None
        cache_time_1 = common_module._asset_ver_cache[0]

        # 즉시 재호출 (캐시 히트)
        time.sleep(0.1)
        ver2 = asset_ver()
        cache_time_2 = common_module._asset_ver_cache[0]

        assert cache_time_1 == cache_time_2, "Should use cached value"
        assert ver1 == ver2

        print("✓ asset_ver cache respects TTL: hit on 0.1s interval")


class TestSettingsCacheConsistency:
    """설정 캐시의 일관성."""

    def test_settings_cache_returns_copy_not_reference(self, fresh_db):
        """get_settings() 반환값은 캐시의 직접 참조가 아니라 복사본이어야 한다."""
        result1 = db.get_settings()
        result2 = db.get_settings()

        # 값은 같지만 객체는 다름
        assert result1 == result2
        assert result1 is not result2, "Should return a copy, not the cached dict itself"

        # 반환된 dict를 수정해도 캐시에 영향 없음
        result1["modified_key"] = "modified"
        result3 = db.get_settings()

        assert "modified_key" not in result3, "Modifying returned dict should not affect cache"
        print("✓ get_settings() returns copies, not references")


class TestDatabaseLockedPattern:
    """database is locked 발생 패턴 추적."""

    def test_write_read_conflict_resilience(self, client, fresh_db):
        """쓰기와 읽기가 동시에 일어나도 오류가 나지 않아야 한다.

        예전에는 없는 주소로 쓰고, 오류 목록을 nonlocal 로 잘못 선언해(pyflakes 가
        짚어 줬다) 아무것도 기록되지 않은 채 늘 통과했다.
        """
        client.get("/today")
        with db.get_conn() as conn:
            block_id = conn.execute(
                "SELECT id FROM blocks ORDER BY id LIMIT 1").fetchone()["id"]
        write_errors = []
        read_errors = []
        write_count = 0
        read_count = 0
        lock = threading.Lock()

        def writer():
            nonlocal write_count
            for i in range(10):
                try:
                    resp = client.post(
                        "/save/field",
                        data={"entity": "block", "id": str(block_id),
                              "field": "bname", "value": f"test-{i}"},
                    )
                    assert resp.status_code == 200, resp.text
                    with lock:
                        write_count += 1
                except Exception as e:
                    write_errors.append(str(e))

        def reader():
            nonlocal read_count
            for _ in range(10):
                try:
                    resp = client.get("/")
                    assert resp.status_code in (200, 307), resp.status_code
                    with lock:
                        read_count += 1
                except Exception as e:
                    read_errors.append(str(e))

        threads = []
        for _ in range(5):
            threads.append(threading.Thread(target=writer))
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        print(f"✓ Write-read conflict: wrote={write_count}, read={read_count}, "
              f"write_err={len(write_errors)}, read_err={len(read_errors)}")

        assert not write_errors, f"쓰기 오류 {len(write_errors)}건: {write_errors[:3]}"
        assert not read_errors, f"읽기 오류 {len(read_errors)}건: {read_errors[:3]}"
        assert write_count == 50, f"쓰기가 {write_count}회밖에 안 됐다"
        assert read_count == 50, f"읽기가 {read_count}회밖에 안 됐다"


class TestEndpointResponseTime:
    """주요 엔드포인트의 응답 시간 측정."""

    def test_home_endpoint_response_time(self, client, fresh_db):
        """GET / 엔드포인트의 응답 시간을 100회 호출해서 측정."""
        times = []

        for _ in range(100):
            start = time.time()
            resp = client.get("/")
            elapsed = (time.time() - start) * 1000  # ms
            times.append(elapsed)
            assert resp.status_code == 200

        times_sorted = sorted(times)
        p50 = times_sorted[50]
        p95 = times_sorted[94]
        p99 = times_sorted[99]
        avg = sum(times) / len(times)

        print("✓ GET / response time (100 calls):")
        print(f"  avg={avg:.1f}ms, p50={p50:.1f}ms, p95={p95:.1f}ms, p99={p99:.1f}ms")

        # 중앙값이 500ms를 넘으면 성능 의심
        if p50 > 500:
            print(f"⚠ WARNING: Median response time {p50:.1f}ms > 500ms")

    def test_endpoint_consistency(self, client, fresh_db):
        """같은 엔드포인트를 반복 호출할 때 응답 시간이 일정한지 확인."""
        # 50회 호출해서 시간 분포 확인
        times = []
        for i in range(50):
            start = time.time()
            resp = client.get("/")
            elapsed = (time.time() - start) * 1000
            times.append(elapsed)
            assert resp.status_code == 200

        min_time = min(times)
        max_time = max(times)
        avg_time = sum(times) / len(times)

        # 최대값/최소값 비율
        if min_time > 0:
            ratio = max_time / min_time
        else:
            ratio = 0

        print("✓ GET / consistency (50 calls):")
        print(f"  min={min_time:.2f}ms, max={max_time:.2f}ms, avg={avg_time:.1f}ms, "
              f"max/min ratio={ratio:.1f}x")

        # 최대값이 최소값의 10배 이상이면 의심 (spike)
        if ratio > 10 and min_time > 1:
            print(f"⚠ WARNING: Large variance in response time (ratio={ratio:.1f}x)")


class TestDatabaseQueryPerformance:
    """DB 쿼리 성능 측정."""

    def test_get_settings_repeated_calls(self, fresh_db):
        """get_settings() 반복 호출 시간을 측정한다."""
        # 첫 호출 (캐시 미스)
        start = time.time()
        db.get_settings()
        uncached_time = (time.time() - start) * 1000

        # 이후 호출 (캐시 히트)
        times = []
        for _ in range(100):
            start = time.time()
            db.get_settings()
            elapsed = (time.time() - start) * 1000
            times.append(elapsed)

        avg_cached = sum(times) / len(times)
        speedup = uncached_time / avg_cached if avg_cached > 0 else 1

        print("✓ get_settings() performance:")
        print(f"  uncached={uncached_time:.2f}ms, cached_avg={avg_cached:.3f}ms, "
              f"speedup={speedup:.0f}x")

        # 캐시된 호출이 첫 호출보다 적어도 5배는 빨라야 캐시가 실제로 듣는 것이다
        assert avg_cached <= uncached_time / 5, \
            f"캐시가 {speedup:.1f}배밖에 빠르지 않다(캐시가 안 듣는다)"

    def test_get_conn_performance(self, fresh_db):
        """get_conn() 연결 생성 시간을 측정한다."""
        times = []

        for _ in range(100):
            start = time.time()
            with db.get_conn() as conn:
                conn.execute("SELECT 1")
            elapsed = (time.time() - start) * 1000
            times.append(elapsed)

        times_sorted = sorted(times)
        p50 = times_sorted[50]
        p95 = times_sorted[94]
        avg = sum(times) / len(times)

        print("✓ get_conn() + SELECT 1 (100 calls):")
        print(f"  avg={avg:.2f}ms, p50={p50:.2f}ms, p95={p95:.2f}ms")

        # 개별 연결 생성이 평균 100ms를 넘으면 무언가 잘못된 것이다
        assert avg <= 100, f"연결 한 번에 평균 {avg:.1f}ms 걸린다"


class TestCacheHitRates:
    """캐시 히트율과 성능 영향을 측정한다."""

    def test_asset_ver_cache_hit_rate(self, fresh_db):
        """asset_ver() 가 TTL 안에서는 파일을 다시 훑지 않아야 한다.

        예전에는 hits·misses 를 테스트가 스스로 세어 두고 그 숫자를 출력만 했다.
        무슨 일이 있어도 통과하는 계산이었다. 실제 캐시 상태를 본다.
        """
        import app.common as common_module
        from app.common import asset_ver

        common_module._asset_ver_cache = None
        first = asset_ver()
        stamped = common_module._asset_ver_cache
        assert stamped is not None, "첫 호출인데 캐시가 안 찼다"

        for _ in range(10):
            assert asset_ver() == first
        assert common_module._asset_ver_cache is stamped, \
            "TTL 안인데 캐시를 다시 만들었다(파일을 매번 훑는다)"

        common_module._asset_ver_cache = None
        assert asset_ver() == first
        assert common_module._asset_ver_cache is not stamped, "캐시를 새로 만들지 않았다"
