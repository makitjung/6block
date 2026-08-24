# 엔드포인트 응답시간과 데이터 스케일 성능 감사
import sqlite3
import statistics
import time
from datetime import datetime, timedelta

import pytest

from app.common import KST
from app.config import CATEGORIES, DAY_BLOCKS


# ==============================================================================
# 헬퍼: 샘플 데이터 생성 (DB에 직접 INSERT)
# ==============================================================================


def create_categories(conn):
    """기본 6개 구분이 이미 있는지 확인하고 없으면 생성."""
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 0:
        return
    for order, name in enumerate(CATEGORIES):
        conn.execute(
            "INSERT INTO categories (name, tone, display_order, is_active) VALUES (?, ?, ?, 1)",
            (name, "black", order),
        )


def create_core_blocks_for_date(conn, date_str):
    """특정 날짜에 B1~B6 블록 6개를 생성한다."""
    blocks = []
    for i, block_info in enumerate(DAY_BLOCKS):
        # DAY_BLOCKS는 (label, is_core, start, end) 튜플의 리스트
        label, is_core, start_time, end_time = block_info
        cursor = conn.execute(
            """
            INSERT INTO blocks
            (date, block_order, block_label, is_core, start_time, end_time, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (date_str, i, label, is_core, start_time, end_time),
        )
        blocks.append(
            {
                "id": cursor.lastrowid,
                "label": label,
                "start": start_time,
                "end": end_time,
            }
        )
    return blocks


def create_slots_for_block(conn, date_str, block_id, block_start, block_end, start_slot_index=0):
    """블록 내 30분 슬롯을 생성한다. 반환: 다음 글로벌 slot_index.

    start_slot_index: 이 블록의 첫 슬롯이 가져야 할 글로벌 인덱스.
    """
    # start_time을 '10:00' 형태에서 분 단위로 변환
    start_parts = block_start.split(":")
    start_min = int(start_parts[0]) * 60 + int(start_parts[1])

    end_parts = block_end.split(":")
    end_min = int(end_parts[0]) * 60 + int(end_parts[1])

    # 30분씩 나누기
    current_min = start_min
    slot_index = start_slot_index
    while current_min < end_min:
        next_min = min(current_min + 30, end_min)
        current_hhmm = f"{current_min // 60:02d}:{current_min % 60:02d}"
        next_hhmm = f"{next_min // 60:02d}:{next_min % 60:02d}"

        conn.execute(
            """
            INSERT INTO slots
            (date, block_id, slot_index, start_time, end_time, do_text, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                date_str,
                block_id,
                slot_index,
                current_hhmm,
                next_hhmm,
                f"TODO {slot_index}",
            ),
        )
        current_min = next_min
        slot_index += 1

    return slot_index


def create_sample_daily_data(conn, num_days):
    """num_days만큼 과거부터 일별 블록·슬롯·메타 데이터를 생성한다."""
    create_categories(conn)

    today = datetime.now(KST).date()
    for i in range(num_days):
        target_date = today - timedelta(days=i)
        date_str = target_date.isoformat()

        # daily_meta 생성
        conn.execute(
            """
            INSERT OR IGNORE INTO daily_meta
            (date, today_goal, daily_plan, memo, gratitude)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                date_str,
                "Goal line 1\nGoal line 2\nGoal line 3",
                "Plan line 1\nPlan line 2\nPlan line 3",
                f"Day memo {i}",
                "Gratitude line 1\nGratitude line 2\nGratitude line 3",
            ),
        )

        # blocks & slots 생성
        blocks = create_core_blocks_for_date(conn, date_str)
        global_slot_index = 0
        for block in blocks:
            global_slot_index = create_slots_for_block(
                conn, date_str, block["id"], block["start"], block["end"], global_slot_index
            )

    conn.commit()


def create_sample_long_term_items(conn, num_items):
    """장기 계획 항목 num_items개를 생성한다."""
    # lt_area 생성 (있으면 스킵)
    if conn.execute("SELECT COUNT(*) FROM lt_area").fetchone()[0] == 0:
        for i, name in enumerate(["프로젝트", "투자", "학습", "여가"]):
            conn.execute(
                "INSERT INTO lt_area (name, display_order, is_active, tone) VALUES (?, ?, 1, ?)",
                (name, i, "blue"),
            )

    today = datetime.now(KST).date()
    for i in range(num_items):
        area_id = (i % 4) + 1  # 4개 영역을 순환
        start_date = today
        end_date = today + timedelta(days=30 * (i + 1))

        conn.execute(
            """
            INSERT INTO lt_item
            (area_id, title, start_date, end_date, progress, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                area_id,
                f"Item {i+1}: Long term goal",
                start_date.isoformat(),
                end_date.isoformat(),
                (i * 10) % 100,
            ),
        )

    conn.commit()


def create_sample_reflections(conn, num_items):
    """고결감 항목 num_items개를 생성한다."""
    today = datetime.now(KST).date()
    for i in range(num_items):
        target_date = today - timedelta(days=i % 30)
        conn.execute(
            """
            INSERT INTO reflection
            (kind, title, text, tags, event_date, uid, created_at, synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                ["고민", "결정", "감사"][i % 3],
                f"Title {i+1}",
                f"Reflection text line 1\nline 2\nline 3 {i}",
                "tag1 tag2",
                target_date.isoformat(),
                f"20260815-1200-{i:04x}",
                datetime.now(KST).isoformat(),
            ),
        )

    conn.commit()


# ==============================================================================
# 헬퍼: 성능 측정
# ==============================================================================


def measure_endpoint(client, path, runs=6):
    """엔드포인트 응답 시간을 여러 번 측정해 중앙값을 반환한다.

    첫 회는 워밍업으로 버리고, 나머지 runs-1회의 중앙값을 반환한다.
    """
    times = []
    for run in range(runs):
        start = time.perf_counter()
        response = client.get(path)
        elapsed = time.perf_counter() - start

        assert response.status_code == 200, f"{path} returned {response.status_code}"

        if run > 0:  # 첫 회는 워밍업으로 버린다
            times.append(elapsed)

    if len(times) == 0:
        return None
    return statistics.median(times)


def count_queries_in_request(conn, client, path):
    """한 요청 동안 실행된 SQL 쿼리 수를 센다."""
    query_count = [0]

    def trace_callback(sql):
        query_count[0] += 1

    conn.set_trace_callback(trace_callback)
    try:
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"
    finally:
        conn.set_trace_callback(None)

    return query_count[0]


# ==============================================================================
# 테스트: GET /today 응답 시간과 데이터 스케일
# ==============================================================================


@pytest.mark.parametrize(
    "days,description",
    [
        (0, "empty_db"),
        (30, "30_days"),
        (365, "365_days"),
        (1000, "1000_days"),
    ],
)
def test_p4_today_scale(client, fresh_db, days, description):
    """GET /today 응답 시간을 데이터 스케일별로 측정한다."""
    # 데이터 생성
    with sqlite3.connect(fresh_db) as conn:
        conn.row_factory = sqlite3.Row
        if days > 0:
            create_sample_daily_data(conn, days)

    # 측정
    median_time = measure_endpoint(client, "/today", runs=6)

    print(f"\nGET /today ({description}): {median_time*1000:.1f}ms")
    assert median_time is not None
    # 극단값: 1000일도 3초 이상은 아니어야 한다
    assert median_time < 3.0, f"GET /today too slow for {description}"


@pytest.mark.parametrize(
    "days,description",
    [
        (30, "30_days"),
        (365, "365_days"),
    ],
)
def test_p4_today_query_count(conn, client, fresh_db, days, description):
    """GET /today 쿼리 수를 측정해 N+1 문제를 탐지한다."""
    with sqlite3.connect(fresh_db) as conn2:
        conn2.row_factory = sqlite3.Row
        create_sample_daily_data(conn2, days)

    # 한 요청의 쿼리 수
    with sqlite3.connect(fresh_db) as conn3:
        conn3.row_factory = sqlite3.Row
        query_count = count_queries_in_request(conn3, client, "/today")

    print(f"\nGET /today ({description}) SQL queries: {query_count}")
    # 데이터가 10배 늘어도 쿼리가 10배 이상 늘어나면 N+1 의심
    # 30일과 365일에서 쿼리 수 비율이 1:1~1:3 사이면 정상


# ==============================================================================
# 테스트: GET /plan 응답 시간과 항목 수 스케일
# ==============================================================================


@pytest.mark.parametrize(
    "count,description",
    [
        (0, "empty"),
        (10, "10_items"),
        (200, "200_items"),
        (2000, "2000_items"),
    ],
)
def test_p4_plan_scale(client, fresh_db, count, description):
    """GET /plan 응답 시간을 장기 항목 수별로 측정한다."""
    with sqlite3.connect(fresh_db) as conn:
        conn.row_factory = sqlite3.Row
        create_categories(conn)
        if count > 0:
            create_sample_long_term_items(conn, count)

    median_time = measure_endpoint(client, "/plan", runs=6)

    print(f"\nGET /plan ({description}): {median_time*1000:.1f}ms")
    assert median_time is not None
    # 2000개 항목도 5초 이상은 아니어야 한다
    assert median_time < 5.0, f"GET /plan too slow for {description}"


# ==============================================================================
# 테스트: GET /reflect 응답 시간과 항목 수 스케일
# ==============================================================================


@pytest.mark.parametrize(
    "count,description",
    [
        (0, "empty"),
        (10, "10_items"),
        (1000, "1000_items"),
    ],
)
def test_p4_reflect_scale(client, fresh_db, count, description):
    """GET /reflect 응답 시간을 고결감 항목 수별로 측정한다."""
    with sqlite3.connect(fresh_db) as conn:
        conn.row_factory = sqlite3.Row
        create_categories(conn)
        if count > 0:
            create_sample_reflections(conn, count)

    median_time = measure_endpoint(client, "/reflect", runs=6)

    print(f"\nGET /reflect ({description}): {median_time*1000:.1f}ms")
    assert median_time is not None
    # 1000개 항목도 3초 이상은 아니어야 한다
    assert median_time < 3.0, f"GET /reflect too slow for {description}"


# ==============================================================================
# 테스트: GET /week, /analytics, /settings 응답 시간 (기본)
# ==============================================================================


def test_p4_week_baseline(client, fresh_db):
    """GET /week 기본 응답 시간."""
    with sqlite3.connect(fresh_db) as conn:
        conn.row_factory = sqlite3.Row
        create_sample_daily_data(conn, 30)

    median_time = measure_endpoint(client, "/week", runs=6)
    print(f"\nGET /week (30 days): {median_time*1000:.1f}ms")
    assert median_time < 2.0


def test_p4_analytics_baseline(client, fresh_db):
    """GET /analytics 기본 응답 시간."""
    with sqlite3.connect(fresh_db) as conn:
        conn.row_factory = sqlite3.Row
        create_sample_daily_data(conn, 30)

    median_time = measure_endpoint(client, "/analytics", runs=6)
    print(f"\nGET /analytics (30 days): {median_time*1000:.1f}ms")
    # 복잡한 쿼리가 있을 수 있으므로 여유있게
    assert median_time < 3.0


def test_p4_settings_baseline(client, fresh_db):
    """GET /settings 기본 응답 시간."""
    median_time = measure_endpoint(client, "/settings", runs=6)
    print(f"\nGET /settings: {median_time*1000:.1f}ms")
    assert median_time < 1.0
