# common.py 단위 테스트. 3칸 입력 처리, 날짜 파싱, 장기 항목 트리 정렬처럼 화면 전체가 기대는 순수 함수들.
import pytest

from app.common import (
    KO_WEEKDAYS,
    _join3,
    _like_pattern,
    _name_override,
    _parse_date,
    _pretty_date,
    _rule_distribute,
    _short_date,
    _split3,
    _weekday_of,
    asset_ver,
    lt_leaves,
    lt_tree_order,
    week_start,
    today_str,
)


# -- 3칸 입력 -----------------------------------------------------------------


def test_split3_은_항상_3칸():
    assert _split3("a\nb\nc") == ["a", "b", "c"]
    assert _split3("a") == ["a", "", ""]
    assert _split3("") == ["", "", ""]
    assert _split3(None) == ["", "", ""]
    assert _split3("a\nb\nc\nd") == ["a", "b", "c"]


def test_split3_은_빈칸_위치를_지킨다():
    """가운데가 빈 경우 칸이 밀리면 2번 목표가 1번으로 올라가 버린다."""
    assert _split3("\nb\n") == ["", "b", ""]


def test_join3_왕복():
    form = {"goal1": "첫째", "goal2": "둘째", "goal3": "셋째"}
    assert _split3(_join3(form, "goal")) == ["첫째", "둘째", "셋째"]


def test_join3_은_칸_안의_줄바꿈을_눌러_3칸_구조를_지킨다():
    form = {"g1": "한 줄\n두 줄", "g2": "", "g3": ""}
    assert _split3(_join3(form, "g")) == ["한 줄 두 줄", "", ""]


def test_join3_전부_비면_빈_문자열():
    assert _join3({"g1": "", "g2": "  ", "g3": "\n"}, "g") == ""


def test_join3_은_없는_키를_빈칸으로_본다():
    assert _join3({}, "g") == ""


# -- 날짜 --------------------------------------------------------------------


@pytest.mark.parametrize("good", ["2026-08-15", "2024-02-29", "2000-01-01"])
def test_parse_date_정상(good):
    assert _parse_date(good) is not None


@pytest.mark.parametrize("bad", [
    "", None, "2026-13-01", "2026-02-30", "26-1-1", "2026/08/15",
    "abc", "2026-08-15T10:00",
])
def test_parse_date_잘못된_값은_None(bad):
    assert _parse_date(bad) is None


def test_parse_date_는_연도_범위를_확인하지_않는다():
    """날짜칸에 '26' 을 치면 브라우저가 0026 으로 만든다. 서버는 그대로 받아 저장한다."""
    d = _parse_date("0026-08-15")
    assert d is not None and d.year == 26


def test_week_start_는_월요일():
    import datetime

    for offset in range(14):
        d = datetime.date(2026, 8, 10) + datetime.timedelta(days=offset)
        ws = week_start(d)
        assert ws.weekday() == 0
        assert 0 <= (d - ws).days <= 6


def test_weekday_of_와_한글요일이_맞다():
    assert _weekday_of("2026-08-10") == 0          # 2026-08-10 은 월요일
    assert KO_WEEKDAYS[_weekday_of("2026-08-10")] == "월"
    assert KO_WEEKDAYS[_weekday_of("2026-08-16")] == "일"


def test_오늘_문자열_형식():
    s = today_str()
    assert _parse_date(s) is not None


@pytest.mark.parametrize("fn", [_pretty_date, _short_date])
def test_날짜_필터는_잘못된_입력에_예외를_낸다(fn):
    """Jinja 필터라 예외가 나면 화면 전체가 500이 된다. 어디서 부르는지 확인이 필요하다."""
    with pytest.raises(ValueError):
        fn("2026-13-99")


# -- 장기 항목 트리 -----------------------------------------------------------


def _item(i, parent=None, title=None, has_children=0):
    return {"id": i, "parent_id": parent, "title": title or f"항목{i}",
            "has_children": has_children}


def test_lt_tree_order_는_상위_바로_아래_하위를_둔다():
    rows = [_item(1, None, has_children=1), _item(2, 1), _item(3, None)]
    out = lt_tree_order(rows)
    assert [r["id"] for r in out] == [1, 2, 3]
    assert [r["depth"] for r in out] == [0, 1, 0]


def test_lt_tree_order_는_상위가_빠진_줄도_잃지_않는다():
    rows = [_item(2, 99), _item(3, None)]
    assert {r["id"] for r in lt_tree_order(rows)} == {2, 3}


def test_lt_leaves_는_최하위만_남기고_상위제목을_붙인다():
    rows = [_item(1, None, "상위", has_children=1), _item(2, 1, "하위")]
    leaves = lt_leaves(rows)
    assert [r["id"] for r in leaves] == [2]
    assert leaves[0]["parent_title"] == "상위"


def test_lt_leaves_빈_입력():
    assert lt_leaves([]) == []


# -- 기타 --------------------------------------------------------------------


def test_like_패턴은_와일드카드를_글자로_바꾼다():
    assert _like_pattern("100%") == "%100\\%%"
    assert _like_pattern("a_b") == "%a\\_b%"
    assert _like_pattern("back\\slash") == "%back\\\\slash%"


def test_name_override():
    assert _name_override("", "주간이름") is None
    assert _name_override("주간이름", "주간이름") is None
    assert _name_override("  다른이름 ", "주간이름") == "다른이름"
    assert _name_override(None, "") is None


def test_rule_distribute():
    assert _rule_distribute("", 3) == ["", "", ""]
    assert _rule_distribute("한 줄", 3) == ["한 줄", "한 줄", "한 줄"]
    assert _rule_distribute("a\nb\nc\nd", 2) == ["a\nc", "b\nd"]


def test_asset_ver_는_숫자_문자열():
    v = asset_ver()
    assert v.isdigit(), v
