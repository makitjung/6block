# 확인된 결함을 못 박아 두는 파일. 지금은 xfail(예상된 실패)로 통과하고, 고치는 순간
# strict=True 때문에 "XPASS" 로 실패한다. 그때 이 파일에서 해당 표시를 지우면 된다.
import pytest

from app.common import _parse_date, _rule_distribute, lt_tree_order

# ---------------------------------------------------------------------------
# 결함 3. 순수 함수의 잠재 결함. 지금은 호출부가 막고 있어 화면에서는 도달할 수 없다.
#   막고 있는 곳을 손댈 때 여기가 먼저 알려 준다.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="parent_id 가 순환이면 그 항목이 목록에서 사라진다")
def test_장기_항목이_순환하면_목록에서_사라진다():
    """/plan/item/reparent 가 자기·하위를 막고 있어 현재는 만들어지지 않는다."""
    rows = [{"id": 1, "parent_id": 2, "title": "a", "has_children": 1},
            {"id": 2, "parent_id": 1, "title": "b", "has_children": 1},
            {"id": 3, "parent_id": None, "title": "c", "has_children": 0}]
    assert {r["id"] for r in lt_tree_order(rows)} == {1, 2, 3}


@pytest.mark.xfail(strict=True, reason="n=0 이면 ZeroDivisionError")
def test_세분화_대상이_0개면_터진다():
    """호출부가 항상 코어블록 6개를 넘겨서 지금은 도달하지 않는다."""
    assert _rule_distribute("a\nb", 0) == []


@pytest.mark.xfail(strict=True, reason="AttributeError 는 except 절에 없어 그대로 올라간다")
def test_parse_date_에_문자열이_아닌_값이_오면_터진다():
    """폼 값은 항상 문자열이라 HTTP 로는 도달하지 않는다."""
    assert _parse_date(20260815) is None


# ---------------------------------------------------------------------------
# 결함 5. Things3 할일 제목에 탭이 들어가면 제목이 잘린다. (영향 작음)
#   Today 목록을 '이름<TAB>태그<TAB>id' 로 직렬화해 읽는데, 제목 자체에 탭이 있으면
#   그 뒤가 태그·id 자리로 밀려 화면에 제목 앞부분만 나온다.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="탭이 필드 구분자라 제목 안의 탭에서 잘린다")
def test_할일_제목에_탭이_있으면_제목이_잘린다(monkeypatch, real_integrations):
    import app.integrations.things as things

    monkeypatch.setattr(things, "_run", lambda *a, **k: (0, "앞\t뒤\t태그A\tID123\n"))
    assert things._today_names()[0]["name"] == "앞\t뒤"
