# 연·분기·달 중점은 주간탭 목표 열에 안 선다. 그 아래 하위 기간에 할 일을 담기 때문이다.
from datetime import datetime

import pytest

from app.common import KST, is_period_span, week_lt_items, week_todos


# -- 어느 기간이 '중점' 인가 --------------------------------------------------


@pytest.mark.parametrize("start, end", [
    ("2026-01-01", "2026-12-31"),   # 한 해
    ("2026-01-01", "2026-03-31"),   # 1분기 — 해와 시작이 같다
    ("2026-04-01", "2026-06-30"),
    ("2026-07-01", "2026-09-30"),
    ("2026-10-01", "2026-12-31"),   # 4분기 — 해와 끝이 같다
    ("2026-08-01", "2026-08-31"),   # 한 달
    ("2026-02-01", "2026-02-28"),
    ("2024-02-01", "2024-02-29"),   # 윤년 2월
])
def test_달력_한_칸을_꽉_채우면_중점이다(start, end):
    assert is_period_span(start, end) is True


@pytest.mark.parametrize("start, end", [
    ("2026-08-13", "2026-08-23"),   # 주 안에서 시작해 주 안에서 끝난다
    ("2026-08-01", "2026-08-23"),   # 달 첫날에 시작했을 뿐이다
    ("2026-08-02", "2026-08-31"),   # 달 말일에 끝났을 뿐이다
    ("2026-08-01", "2026-09-30"),   # 두 달 — 분기가 아니다
    ("2026-02-01", "2026-04-30"),   # 석 달이지만 분기 경계가 아니다
    ("2026-01-01", "2027-12-31"),   # 두 해
    ("2024-02-01", "2024-02-28"),   # 윤년인데 하루 모자라다
    ("2026-08-31", "2026-08-01"),   # 뒤집힌 자료
    ("2026-08-24", "2026-08-24"),   # 하루짜리
])
def test_그_밖은_보통_항목이다(start, end):
    assert is_period_span(start, end) is False


@pytest.mark.parametrize("start, end", [
    ("엉망", "2026-08-31"), ("", ""), ("2026-13-01", "2026-13-31"),
    ("2026-02-01", "2026-02-29"), (None, None),
])
def test_못_읽는_날짜는_감추지_않는다(start, end):
    """판단이 안 서면 감추지 않는다 — 감춘 줄은 아무 데도 안 보인다"""
    assert is_period_span(start, end) is False


# -- 주간탭 목표 열 -----------------------------------------------------------


WEEK = "2026-08-24"          # 월요일
SUNDAY = "2026-08-30"


def _area(conn, name="영역"):
    return conn.execute(
        "INSERT INTO lt_area (name, is_active, display_order) VALUES (?, 1, 1)", (name,)
    ).lastrowid


def _item(conn, area_id, title, start, end, parent_id=None):
    now = datetime.now(KST).isoformat(timespec="seconds")
    return conn.execute(
        "INSERT INTO lt_item (title, area_id, parent_id, start_date, end_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (title, area_id, parent_id, start, end, now),
    ).lastrowid


def test_중점은_목표_열에_안_선다(conn):
    """하위가 아직 없어도 마찬가지다. 곧 만들 자리이기 때문이다"""
    a = _area(conn)
    _item(conn, a, "8월 중점", "2026-08-01", "2026-08-31")
    _item(conn, a, "이번 분기 중점", "2026-07-01", "2026-09-30")
    _item(conn, a, "올해 중점", "2026-01-01", "2026-12-31")
    _item(conn, a, "이번 주 할 일", WEEK, SUNDAY)
    assert [r["title"] for r in week_lt_items(conn, WEEK)] == ["이번 주 할 일"]


def test_중점_아래_할_일은_그대로_내려온다(conn):
    """중점은 빠지되 그 아래 할 일은 남고, 어느 상위에서 왔는지도 그대로 붙는다"""
    a = _area(conn)
    parent = _item(conn, a, "8월 중점", "2026-08-01", "2026-08-31")
    _item(conn, a, "4주차", WEEK, SUNDAY, parent_id=parent)
    got = week_lt_items(conn, WEEK)
    assert [(r["title"], r["parent_title"]) for r in got] == [("4주차", "8월 중점")]


def test_오늘_탭_주간계획_연결_목록에서도_빠진다(conn):
    """같은 목록을 쓰는 자리다. 한쪽에만 남으면 무엇이 이번 주 목표인지 어긋난다"""
    a = _area(conn)
    focus = _item(conn, a, "8월 중점", "2026-08-01", "2026-08-31")
    plain = _item(conn, a, "이번 주 할 일", WEEK, SUNDAY)
    keys = [r["key"] for r in week_todos(conn, WEEK)]
    assert f"lt:{plain}" in keys
    assert f"lt:{focus}" not in keys


def test_자유_란은_이_규칙과_상관없다(conn):
    """자유 란은 그 주에 적은 글이지 기간이 걸린 항목이 아니다"""
    conn.execute("INSERT INTO weekly_meta (week_start, weekly_goal) VALUES (?, ?)",
                 (WEEK, "첫 줄\n둘째 줄\n"))
    keys = [r["key"] for r in week_todos(conn, WEEK)]
    assert keys == ["wk:1", "wk:2"]


def test_장기_탭_간트에는_그대로_그린다(conn):
    """주간탭에서만 뺀다. 장기 탭에서 못 보면 중점을 고칠 데가 없어진다"""
    a = _area(conn)
    _item(conn, a, "8월 중점", "2026-08-01", "2026-08-31")
    still = conn.execute("SELECT title FROM lt_item WHERE masked = 0").fetchall()
    assert [r["title"] for r in still] == ["8월 중점"]
