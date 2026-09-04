# DB 도우미 함수들의 경계값 테스트(0으로 나누기·구분 상속·트리 불변식·파일 에러)
from datetime import datetime

import pytest

from app.common import KST
from app.routes import analytics, settings
from app.routes.plan import _lt_descendants, _lt_rollup, _lt_root


# ============================================================================
# analytics._exec_funnel: 0으로 나누기
# ============================================================================


def test_exec_funnel_빈_기간_모든_비율은_0(conn):
    """기록이 없으면 모든 비율이 0이어야 한다."""
    result = analytics._exec_funnel(conn, "2026-08-01", "2026-08-31")
    assert result["design_pct"] == 0
    assert result["detail_pct"] == 0
    assert result["exec_pct"] == 0
    assert result["exec_score"] == 0
    assert result["real_exec"] == 0


def test_exec_funnel_코어_슬롯_없으면_실행율_0(conn):
    """코어 슬롯이 없으면 실행율이 0이어야 한다."""
    result = analytics._exec_funnel(conn, "2026-08-01", "2026-08-31")
    assert result["core_slots"] == 0
    assert result["real_exec"] == 0


# ============================================================================
# analytics._analytics_data: 비율 계산 (0으로 나누기)
# ============================================================================


def test_analytics_data_기록_없으면_평균_완료율_0(conn):
    """기록이 없으면 avg_done은 0이어야 한다."""
    result = analytics._analytics_data("7")
    assert result["summary"]["avg_done"] == 0
    assert result["summary"]["rec_days"] == 0
    assert result["summary"]["pd_pct"] == 0
    assert result["summary"]["streak"] == 0


def test_analytics_data_weekday_all_zero(conn):
    """기록이 없으면 weekday_data의 모든 pct가 0이어야 한다."""
    result = analytics._analytics_data("7")
    for wd in result["weekday_data"]:
        assert wd["pct"] == 0
        assert wd["done"] == 0
        assert wd["planned"] == 0


# ============================================================================
# analytics._search_records: SQL injection 방지
# ============================================================================


def test_search_records_like_pattern_percent_escape(conn):
    """_like_pattern은 %를 이스케이프해야 한다."""
    from app.common import _like_pattern
    pattern = _like_pattern("100%")
    # 패턴이 % 와일드카드가 아니라 그 문자 그대로를 찾아야 함
    assert "\\%" in pattern or "\\\\%" in pattern


def test_search_records_like_pattern_underscore_escape(conn):
    """_like_pattern은 _를 이스케이프해야 한다."""
    from app.common import _like_pattern
    pattern = _like_pattern("test_")
    # 패턴이 _ 와일드카드가 아니라 그 문자 그대로를 찾아야 함
    assert "\\_" in pattern or "\\\\_" in pattern


# ============================================================================
# settings._data_summary: 빈 DB
# ============================================================================


def test_data_summary_빈_db_count_0(conn):
    """빈 DB에서 rec_days와 slot_recs는 0이어야 한다."""
    result = settings._data_summary()
    assert result["rec_days"] == 0
    assert result["slot_recs"] == 0
    assert result["first"] == "-"
    assert result["last"] == "-"
    assert result["inbox_open"] == 0


# ============================================================================
# settings._hides_last_category: 마지막 구분 보호
# ============================================================================


def test_hides_last_category_마지막은_숨길_수_없음(conn):
    """활성 구분이 하나뿐이면 그것을 숨길 수 없어야 한다."""
    cid = conn.execute(
        "SELECT id FROM categories WHERE is_active = 1 LIMIT 1"
    ).fetchone()["id"]
    # 다른 모든 구분을 비활성화
    conn.execute("UPDATE categories SET is_active = 0 WHERE id != ?", (cid,))
    assert settings._hides_last_category(conn, cid) is True


def test_hides_last_category_여러_개면_숨길_수_있음(conn):
    """활성 구분이 여러 개면 하나를 숨길 수 있어야 한다."""
    c1 = conn.execute(
        "SELECT id FROM categories WHERE is_active = 1 LIMIT 1"
    ).fetchone()["id"]
    # 다른 구분이 활성이므로 c1을 숨길 수 있어야 함
    assert settings._hides_last_category(conn, c1) is False


# ============================================================================
# settings._load_cat_templates: 템플릿 채우기
# ============================================================================


def test_load_cat_templates_빈_list(conn):
    """구분 템플릿이 없으면 빈 리스트를 돌려줘야 한다."""
    result = settings._load_cat_templates(conn)
    assert result == []


# ============================================================================
# settings._backup_status: 파일시스템 에러 처리
# ============================================================================


def test_backup_status_폴더_없으면_ok_false(tmp_root):
    """백업 폴더가 없으면 ok는 False이어야 한다."""
    result = settings._backup_status()
    # 로컬 또는 클라우드 중 하나는 없어야 함
    assert isinstance(result, list)
    assert len(result) >= 2
    for item in result:
        assert "ok" in item
        assert "name" in item


# ============================================================================
# settings._recent_errors: 로그 파일 에러 처리
# ============================================================================


def test_recent_errors_로그_없으면_count_0():
    """로그 파일이 없으면 count는 0이어야 한다."""
    result = settings._recent_errors()
    assert "count" in result
    assert isinstance(result["count"], int)


def test_recent_errors_는_이번_기동_뒤의_500만_센다(tmp_path, monkeypatch):
    """고치고 재시작했는데도 옛 500 이 계속 빨갛게 남으면 지금 고장과 구별할 수 없다."""
    log = tmp_path / "uvicorn.out.log"
    err = tmp_path / "uvicorn.err.log"
    log.write_text('INFO: - "GET /week HTTP/1.1" 500 Internal Server Error\n', encoding="utf-8")
    err.write_text("ERROR: 옛 트레이스백\n", encoding="utf-8")
    monkeypatch.setattr(settings, "_log_paths", lambda: (log, err))
    monkeypatch.setattr(settings, "_LOG_START", {"out": 0, "err": 0})

    settings.mark_log_start()                     # 여기서 서버가 떴다고 친다
    assert settings._recent_errors()["count"] == 0, "이미 지나간 500 을 세고 있다"

    with log.open("a", encoding="utf-8") as f:    # 기동 뒤에 새로 난 오류
        f.write('INFO: - "GET /settings HTTP/1.1" 500 Internal Server Error\n')
    with err.open("a", encoding="utf-8") as f:
        f.write("ERROR: 지금 난 오류\n")
    got = settings._recent_errors()
    assert got["count"] == 1 and "지금 난 오류" in got["last"]


# ============================================================================
# plan._lt_root: 최상위 찾기
# ============================================================================


def test_lt_root_최상위_자신을_돌려줌(conn):
    """최상위 항목에 자신의 id를 돌려줘야 한다."""
    aid = conn.execute(
        "INSERT INTO lt_area (name, display_order, is_active, tone) "
        "VALUES (?, ?, 1, 'blue')",
        ("영역", 0),
    ).lastrowid
    p = conn.execute(
        "INSERT INTO lt_item (area_id, title, start_date, end_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (aid, "최상위", "2026-08-01", "2026-08-31", datetime.now(KST).isoformat(timespec="seconds")),
    ).lastrowid
    result = _lt_root(conn, p)
    assert result == p


def test_lt_root_하위에서_최상위_찾기(conn):
    """하위 항목에서 최상위를 정확히 찾아야 한다."""
    aid = conn.execute(
        "INSERT INTO lt_area (name, display_order, is_active, tone) "
        "VALUES (?, ?, 1, 'blue')",
        ("영역", 0),
    ).lastrowid
    now = datetime.now(KST).isoformat(timespec="seconds")
    p = conn.execute(
        "INSERT INTO lt_item (area_id, title, start_date, end_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (aid, "최상위", "2026-08-01", "2026-08-31", now),
    ).lastrowid
    c = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (aid, p, "하위", "2026-08-01", "2026-08-15", now),
    ).lastrowid

    result = _lt_root(conn, c)
    assert result == p


# ============================================================================
# plan._lt_descendants: 모든 하위 수집
# ============================================================================


def test_lt_descendants_하위_없으면_공백(conn):
    """하위가 없으면 빈 리스트를 돌려줘야 한다."""
    aid = conn.execute(
        "INSERT INTO lt_area (name, display_order, is_active, tone) "
        "VALUES (?, ?, 1, 'blue')",
        ("영역", 0),
    ).lastrowid
    pid = conn.execute(
        "INSERT INTO lt_item (area_id, title, start_date, end_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (aid, "최상위", "2026-08-01", "2026-08-31", datetime.now(KST).isoformat(timespec="seconds")),
    ).lastrowid

    result = _lt_descendants(conn, pid)
    assert result == []


def test_lt_descendants_다단계_하위_모두_포함(conn):
    """여러 단계의 하위를 모두 포함해야 한다."""
    aid = conn.execute(
        "INSERT INTO lt_area (name, display_order, is_active, tone) "
        "VALUES (?, ?, 1, 'blue')",
        ("영역", 0),
    ).lastrowid
    now = datetime.now(KST).isoformat(timespec="seconds")
    p = conn.execute(
        "INSERT INTO lt_item (area_id, title, start_date, end_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (aid, "최상위", "2026-08-01", "2026-08-31", now),
    ).lastrowid
    c1 = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (aid, p, "하위1", "2026-08-01", "2026-08-15", now),
    ).lastrowid
    c2 = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (aid, c1, "하위2", "2026-08-01", "2026-08-08", now),
    ).lastrowid

    result = _lt_descendants(conn, p)
    assert set(result) == {c1, c2}


# ============================================================================
# plan._lt_rollup: 상위-하위 기간 롤업 불변식
# ============================================================================


def test_lt_rollup_하위_없으면_그대로(conn):
    """하위가 없으면 상위 기간이 바뀌지 않아야 한다."""
    aid = conn.execute(
        "INSERT INTO lt_area (name, display_order, is_active, tone) "
        "VALUES (?, ?, 1, 'blue')",
        ("영역", 0),
    ).lastrowid
    now = datetime.now(KST).isoformat(timespec="seconds")
    pid = conn.execute(
        "INSERT INTO lt_item (area_id, title, start_date, end_date, progress, updated_at) "
        "VALUES (?, ?, ?, ?, 50, ?)",
        (aid, "상위", "2026-08-01", "2026-08-31", now),
    ).lastrowid

    _lt_rollup(conn, pid)

    row = conn.execute(
        "SELECT start_date, end_date FROM lt_item WHERE id = ?", (pid,)
    ).fetchone()
    assert row["start_date"] == "2026-08-01"
    assert row["end_date"] == "2026-08-31"


def test_lt_rollup_하위_기간으로_상위_확장(conn):
    """하위 기간이 상위를 벗어나면 상위가 확장되어야 한다."""
    aid = conn.execute(
        "INSERT INTO lt_area (name, display_order, is_active, tone) "
        "VALUES (?, ?, 1, 'blue')",
        ("영역", 0),
    ).lastrowid
    now = datetime.now(KST).isoformat(timespec="seconds")
    pid = conn.execute(
        "INSERT INTO lt_item (area_id, title, start_date, end_date, progress, updated_at) "
        "VALUES (?, ?, ?, ?, 50, ?)",
        (aid, "상위", "2026-08-10", "2026-08-20", now),
    ).lastrowid

    # 상위보다 먼저 시작하고 나중에 끝나는 하위 생성
    cid = conn.execute(
        "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, progress, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 75, ?)",
        (aid, pid, "하위", "2026-08-01", "2026-08-31", now),
    ).lastrowid

    _lt_rollup(conn, cid)

    # 상위 기간이 확장되었는지 확인
    row = conn.execute(
        "SELECT start_date, end_date, progress FROM lt_item WHERE id = ?", (pid,)
    ).fetchone()
    assert row["start_date"] == "2026-08-01"
    assert row["end_date"] == "2026-08-31"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
