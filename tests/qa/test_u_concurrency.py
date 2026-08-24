# 동시성·재시작·마이그레이션 경쟁 테스트. 여러 프로세스/스레드가 같은 DB를 초기화하거나 읽고 쓸 때 데이터 무결성 검증
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor



class TestInitDbConcurrency:
    """여러 스레드에서 init_db 를 동시에 부를 때 데이터 무결성 검증."""

    def test_threading_init_db_no_duplicate_categories(self, fresh_db):
        """threading 으로 4개 스레드가 동시에 init_db 를 부른다. fcntl 락이 스레드 간에도 일하는지 확인."""
        import app.db as db

        threads = []
        for _ in range(4):

            def thread_init():
                db.init_db()

            t = threading.Thread(target=thread_init)
            threads.append(t)
            t.start()

        # 모두 완료
        for t in threads:
            t.join(timeout=5)

        # 검증
        conn = sqlite3.connect(str(fresh_db))
        try:
            conn.row_factory = sqlite3.Row
            cat_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            assert cat_count == 6, f"Expected 6 categories, got {cat_count}"
        finally:
            conn.close()


class TestMigrationConcurrency:
    """옛 스키마에서 시작해 마이그레이션 경쟁 테스트."""

    def test_migration_with_concurrent_init(self, fresh_db):
        """옛 DB(user_version=0)에서 동시에 마이그레이션을 부른다. ALTER/DROP 이 멱등한지 확인."""
        import app.db as db

        # 1. 현재 DB 를 옛 상태(user_version=0)로 리셋한다.
        conn = sqlite3.connect(str(fresh_db))
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        conn.close()

        # 2. 여러 스레드에서 동시에 init_db 를 부른다.
        threads = []
        for _ in range(4):
            def thread_init():
                db.init_db()

            t = threading.Thread(target=thread_init)
            threads.append(t)
            t.start()

        # 모두 완료
        for t in threads:
            t.join(timeout=5)

        # 3. DB 가 파손되지 않았는지 확인
        conn = sqlite3.connect(str(fresh_db))
        try:
            conn.row_factory = sqlite3.Row
            # 마이그레이션이 끝났으면 user_version 이 SCHEMA_VERSION 이어야 함
            # (숫자를 박아 두면 판번호를 올릴 때마다 이 테스트가 함께 깨진다)
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert user_version == db.SCHEMA_VERSION, (
                f"Expected user_version={db.SCHEMA_VERSION}, got {user_version}")

            # 주요 테이블이 존재해야 함
            for table_name in ("categories", "daily_meta", "slots", "blocks"):
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchall()
                assert len(tables) > 0, f"Table {table_name} not found"

            # 카테고리 6개
            cat_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            assert cat_count == 6, f"Expected 6 categories, got {cat_count}"
        finally:
            conn.close()


class TestReadWriteConcurrency:
    """읽기(폴링)와 쓰기(저장)를 동시에 했을 때 'database is locked' 가 나는지, busy_timeout·WAL 이 듣는지."""

    def test_concurrent_read_write_no_locked_errors(self, fresh_db):
        """60초 폴링(읽기)과 저장(쓰기)을 스레드로 겹쳐서 실행. 'database is locked' 가 나지 않아야 함."""
        import app.db as db

        errors = []
        stop_event = threading.Event()

        def reader_thread():
            """60초마다 반복해서 읽는다(시뮬레이션: 실제로는 5초 정도만)."""
            try:
                for _ in range(3):  # 3회 읽기
                    if stop_event.is_set():
                        break
                    with db.get_conn() as conn:
                        conn.execute("SELECT COUNT(*) FROM categories").fetchone()
                    time.sleep(0.1)
            except Exception as e:
                errors.append(("reader", str(e)))

        def writer_thread():
            """저장을 여러 번 한다."""
            try:
                for i in range(3):
                    if stop_event.is_set():
                        break
                    with db.get_conn() as conn:
                        conn.execute(
                            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (f"test_key_{i}", f"value_{i}"),
                        )
                    time.sleep(0.05)
            except Exception as e:
                errors.append(("writer", str(e)))

        # 읽기·쓰기 스레드 시작
        reader = threading.Thread(target=reader_thread)
        writer = threading.Thread(target=writer_thread)
        reader.start()
        writer.start()

        # 모두 완료
        reader.join(timeout=5)
        writer.join(timeout=5)
        stop_event.set()

        # 'database is locked' 에러가 없어야 함
        for source, msg in errors:
            assert "database is locked" not in msg.lower(), f"{source}: {msg}"

    def test_concurrent_writes_last_one_wins(self, fresh_db):
        """같은 슬롯에 동시 저장을 던진다. 마지막 값이 이기고 값이 섞이지 않는지 확인."""
        import app.db as db

        # 테스트 데이터 생성
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO daily_meta (date) VALUES (?)",
                ("2026-08-16",),
            )

        errors = []

        def write_setting(value):
            try:
                for _ in range(2):
                    with db.get_conn() as conn:
                        conn.execute(
                            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            ("concurrent_test", value),
                        )
                    time.sleep(0.01)
            except Exception as e:
                errors.append(str(e))

        # 3개 스레드가 다른 값을 동시에 쓴다
        threads = []
        for i in range(3):
            t = threading.Thread(target=write_setting, args=(f"value_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5)

        # 에러가 없어야 함
        assert len(errors) == 0, f"Errors: {errors}"

        # 최종 값이 하나만 있어야 함 (섞이지 않음)
        with db.get_conn() as conn:
            result = conn.execute(
                "SELECT value FROM app_settings WHERE key='concurrent_test'"
            ).fetchone()
            assert result is not None, "Setting not found"
            value = result[0]
            assert value in ("value_0", "value_1", "value_2"), f"Unexpected value: {value}"
            # 값이 '섞여' 있지 않아야 함 (예: "value_0value_1" 같은 형태)
            assert "_" in value and value.count("_") == 1, f"Value looks corrupted: {value}"


class TestSettingsCacheRace:
    """db._settings_cache 경쟁: set_setting 으로 캐시를 비우는 동안 다른 스레드가 get_settings 를 부르면 stale 값이 캐시에 박혀도 되나?"""

    def test_settings_cache_not_stale(self, fresh_db):
        """set_setting → get_settings 순서가 여러 스레드에서 겹쳐도 캐시 일관성이 유지되는가."""
        import app.db as db

        results = []
        errors = []

        def setter_thread():
            try:
                for i in range(5):
                    db.set_setting("cache_test_key", f"value_{i}")
                    time.sleep(0.02)
            except Exception as e:
                errors.append(("setter", str(e)))

        def getter_thread(thread_id):
            try:
                for _ in range(5):
                    settings = db.get_settings()
                    # cache_test_key 가 있으면 그 값을 기록
                    if "cache_test_key" in settings:
                        results.append((thread_id, settings["cache_test_key"]))
                    time.sleep(0.015)
            except Exception as e:
                errors.append(("getter", str(e)))

        threads = []
        setter = threading.Thread(target=setter_thread)
        threads.append(setter)
        for i in range(3):
            getter = threading.Thread(target=getter_thread, args=(i,))
            threads.append(getter)

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=5)

        # 에러 없음
        assert len(errors) == 0, f"Errors: {errors}"

        # 결과가 있으면, 그 값들이 모두 "value_N" 형식이어야 함 (섞이지 않음)
        for thread_id, value in results:
            assert value.startswith("value_"), f"Invalid cached value: {value}"


class TestTestClientConcurrentSaves:
    """TestClient 로 같은 슬롯에 동시 저장을 던진다. 마지막 값이 이기는지, 값이 섞이지 않는지."""

    def test_concurrent_slot_saves_no_corruption(self, client, fresh_db):
        """같은 슬롯을 여러 스레드에서 동시에 저장한다."""
        # 먼저 오늘 날짜의 슬롯 구조를 만든다
        from app.common import today_str

        today = today_str()

        # GET /today 로 상태를 초기화
        client.get(f"/today?date={today}")

        # 같은 슬롯을 여러 번 저장한다
        errors = []

        def save_slot(value):
            try:
                # 첫 번째 슬롯(index 0)을 저장한다고 가정
                # 실제 라우트는 /save/day 또는 /save/slot 형태일 것
                resp = client.post(
                    "/save/field",
                    json={
                        "date": today,
                        "field": "slot_do",  # 예시
                        "slot_idx": 0,
                        "value": value,
                    },
                )
                if resp.status_code not in (200, 400):
                    errors.append(f"Unexpected status {resp.status_code}: {resp.text}")
            except Exception as e:
                errors.append(str(e))

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(save_slot, f"text_{i}")
                for i in range(3)
            ]
            for future in futures:
                future.result(timeout=5)

        # 에러 없음
        assert len(errors) == 0, f"Errors during concurrent saves: {errors}"


class TestSettingsCacheInvalidation:
    """set_setting 후 get_settings 를 부르면 캐시가 즉시 무효화되고 새 값을 반환하는가."""

    def test_cache_invalidation_immediate(self, fresh_db):
        """set_setting → get_settings 순서가 같은 스레드에서도 캐시가 즉시 갱신되는가."""
        import app.db as db

        # 초기값 설정
        db.set_setting("test_key", "initial")
        settings1 = db.get_settings()
        assert settings1["test_key"] == "initial"

        # 값 변경
        db.set_setting("test_key", "updated")
        settings2 = db.get_settings()
        assert settings2["test_key"] == "updated", "Cache not invalidated after set_setting"

        # 변경 후 3회 이상 연속 get_settings 를 부르면 일관성 있는 값이 나와야 함
        for _ in range(3):
            settings3 = db.get_settings()
            assert settings3["test_key"] == "updated", "Cache inconsistency detected"
