# 확인된 결함을 못 박아 두는 파일. 지금은 xfail(예상된 실패)로 통과하고, 고치는 순간
# strict=True 때문에 "XPASS" 로 실패한다. 그때 이 파일에서 해당 표시를 지우면 된다.
import pytest

from app.common import _parse_date, _rule_distribute, lt_tree_order

# ---------------------------------------------------------------------------
# 결함 1. 날짜 경로 매개변수를 검증하지 않아 화면이 500 이 된다. (실제로 도달 가능)
#   /day/2026-13-45 처럼 잘못된 날짜를 주소창에 넣거나, 옛 북마크·오타로 들어오면
#   그 탭이 통째로 안 열린다. datetime.strptime 이 ValueError 를 그대로 던진다.
# ---------------------------------------------------------------------------

BAD_DATES = ["invalid", "2026-13-45", "2026-02-30", "26-1-1", "0", "-1", "%20"]


@pytest.mark.xfail(strict=True, reason="날짜 형식 검증 없음 → ValueError 가 500 으로 나간다")
@pytest.mark.parametrize("path", ["/day/{}", "/api/day/{}", "/week/{}"])
def test_잘못된_날짜로_화면을_열면_500이_난다(client, path):
    codes = {client.get(path.format(bad)).status_code for bad in BAD_DATES}
    assert 500 not in codes, f"{path} → {sorted(codes)}"


@pytest.mark.xfail(strict=True, reason="날짜 형식 검증 없음 → ValueError 가 500 으로 나간다")
@pytest.mark.parametrize("path", ["/save/day/{}", "/week/save/{}"])
def test_잘못된_날짜로_저장하면_500이_난다(client, path):
    codes = {client.post(path.format(bad), data={}).status_code for bad in BAD_DATES}
    assert 500 not in codes, f"{path} → {sorted(codes)}"


# ---------------------------------------------------------------------------
# 결함 2. id 가 SQLite 정수 범위를 넘으면 500 이 된다. (화면에서는 도달 불가)
#   OverflowError: Python int too large to convert to SQLite INTEGER.
#   FastAPI 가 int 로는 잘 바꿔 주지만 SQLite 가 못 받는다.
# ---------------------------------------------------------------------------

HUGE = "99999999999999999999"


@pytest.mark.xfail(strict=True, reason="큰 정수 id 를 SQLite 가 못 받아 OverflowError 가 500 으로 나간다")
@pytest.mark.parametrize("path", [
    "/slot/done/{}", "/inbox/done/{}", "/inbox/delete/{}",
    "/reflect/sync/{}", "/reflect/update/{}", "/reflect/delete/{}",
    "/reflect/review-note/{}",
])
def test_아주_큰_id_로_부르면_500이_난다(client, path):
    assert client.post(path.format(HUGE), data={}).status_code != 500


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
