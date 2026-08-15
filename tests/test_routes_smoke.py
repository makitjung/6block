# 라우트 전수 점검. 79개 엔드포인트를 빠짐없이 두들겨 500(처리 안 된 예외)이 나는 곳을 찾는다.
# 화면이 500이 되면 그 탭은 통째로 안 열린다. 400/404/422 는 정상적인 거절이라 통과로 본다.
import re

import pytest
from starlette.routing import Route

from app.common import today_str, week_start
from app.main import app

# 응답을 재시작으로 만드는 라우트는 프로세스에 SIGTERM 을 보내므로 테스트에서 부르면 안 된다.
SKIP_PATHS = {"/settings/restart"}

# 경로 매개변수 자리에 넣을 정상값.
GOOD_PARAM = {
    "date_str": today_str(),
    "week_start_str": week_start(__import__("datetime").date.today()).strftime("%Y-%m-%d"),
    "item_id": "1",
    "slot_id": "1",
}

# 경로 매개변수 자리에 넣을 악성값. 어느 것도 500 을 만들면 안 된다.
BAD_PARAM_VALUES = [
    "invalid", "2026-13-45", "2026-02-30", "26-1-1", "2026/08/15",
    "0", "-1", "99999999999999999999", "%20", "..", "' OR 1=1--",
]

ALLOWED = {200, 204, 301, 302, 303, 307, 400, 403, 404, 405, 409, 422}


def _routes():
    for r in app.routes:
        if isinstance(r, Route) and r.path not in SKIP_PATHS:
            yield r


def _fill(path: str, values: dict) -> str:
    def sub(m):
        return values.get(m.group(1), "1")
    return re.sub(r"\{([a-zA-Z_]+)\}", sub, path)


def _ids(routes):
    return [f"{sorted(r.methods - {'HEAD'})[0]} {r.path}" for r in routes]


GET_ROUTES = [r for r in _routes() if "GET" in (r.methods or set())]
POST_ROUTES = [r for r in _routes() if "POST" in (r.methods or set())]


def test_라우트_수를_세어_둔다():
    """엔드포인트가 늘거나 줄면 이 테스트가 먼저 알려 준다(커버리지 기준선)."""
    assert len(GET_ROUTES) >= 22, len(GET_ROUTES)
    assert len(POST_ROUTES) >= 56, len(POST_ROUTES)


@pytest.mark.parametrize("route", GET_ROUTES, ids=_ids(GET_ROUTES))
def test_모든_GET_이_정상값으로_열린다(client, route):
    res = client.get(_fill(route.path, GOOD_PARAM))
    assert res.status_code in ALLOWED, f"{route.path} → {res.status_code}"
    assert res.status_code < 500


_PARAM_GETS = [r for r in GET_ROUTES if "{" in r.path]


@pytest.mark.parametrize("bad", BAD_PARAM_VALUES)
@pytest.mark.parametrize("route", _PARAM_GETS, ids=_ids(_PARAM_GETS))
def test_GET_경로_매개변수가_이상해도_500이_아니다(client, route, bad):
    path = _fill(route.path, {k: bad for k in GOOD_PARAM})
    res = client.get(path)
    assert res.status_code != 500, f"{path} → 500 (처리 안 된 예외)"


_PARAM_POSTS = [r for r in POST_ROUTES if "{" in r.path]


@pytest.mark.parametrize("bad", BAD_PARAM_VALUES)
@pytest.mark.parametrize("route", _PARAM_POSTS, ids=_ids(_PARAM_POSTS))
def test_POST_경로_매개변수가_이상해도_500이_아니다(client, route, bad):
    path = _fill(route.path, {k: bad for k in GOOD_PARAM})
    res = client.post(path, data={})
    assert res.status_code != 500, f"{path} → 500 (처리 안 된 예외)"


@pytest.mark.parametrize("route", POST_ROUTES, ids=_ids(POST_ROUTES))
def test_모든_POST_가_빈_폼에_500을_내지_않는다(client, route):
    """필수 값이 없으면 400/422 로 거절해야 한다. 500 이면 예외를 안 잡은 것이다."""
    res = client.post(_fill(route.path, GOOD_PARAM), data={})
    assert res.status_code != 500, f"{route.path} → 500 (빈 폼)"


@pytest.mark.parametrize("route", POST_ROUTES, ids=_ids(POST_ROUTES))
def test_모든_POST_가_쓰레기_값에_500을_내지_않는다(client, route):
    junk = {
        "id": "abc", "text": "x" * 5000, "value": "-1", "date": "2026-13-45",
        "start": "", "end": "9999-99-99", "parent_id": "0", "area_id": "-5",
        "scope": "9", "weekday": "77", "span": "-3", "progress": "500",
        "category_id": "999999", "block_label": "B9", "title": "\x00\t\n",
        "week_start": "not-a-date", "item_id": "1e999", "days": "-100000",
    }
    res = client.post(_fill(route.path, GOOD_PARAM), data=junk)
    assert res.status_code != 500, f"{route.path} → 500 (쓰레기 값)"


# 폼으로 오는 id 이름을 모두 모았다. 라우트는 자기가 쓰는 것만 form.get 으로 꺼내므로
# 한꺼번에 보내도 된다. 나머지 칸은 검증을 통과할 만한 값으로 채워 쿼리까지 닿게 한다.
ID_FIELDS = ("id", "item_id", "block_id", "peer", "parent_id", "area_id",
             "template_id", "category_id", "slot_id")
OUT_OF_RANGE = "9223372036854775808"        # SQLite 최대값 + 1


@pytest.mark.parametrize("route", POST_ROUTES, ids=_ids(POST_ROUTES))
def test_폼의_id가_SQLite_범위를_넘어도_500이_아니다(client, route):
    """큰 수를 그대로 쿼리에 넣으면 sqlite3 가 OverflowError 를 내 화면이 500 이 된다.

    경로로 오는 id 는 RowId 가 막지만, 폼으로 오는 id 는 라우트마다 int() 로 읽는다.
    한 곳만 빠져도 그 버튼이 500 이 되므로 전 라우트를 함께 확인한다.
    """
    data = {
        "title": "테스트", "name": "테스트", "text": "테스트", "value": "1",
        "start": "2026-08-01", "end": "2026-08-31", "date": "2026-08-15",
        "week_start": "2026-08-10", "days": "1", "edge": "end", "place": "before",
        "dir": "up", "tone": "blue", "block": "B1", "block_label": "B1",
        "weekday": "0", "span": "1", "do_text": "x", "weekdays": "0",
        "start_time": "07:30", "progress": "10", "entity": "slot",
        "field": "do_text", "label": "B1", "note": "x", "kind": "고민",
        "done": "1", "content": "K=V\n", "rng": "7", "scope": "",
    }
    data.update({k: OUT_OF_RANGE for k in ID_FIELDS})
    res = client.post(_fill(route.path, GOOD_PARAM), data=data)
    assert res.status_code != 500, f"{route.path} → 500 (범위 밖 id)"


@pytest.mark.parametrize("route", POST_ROUTES, ids=_ids(POST_ROUTES))
def test_모든_POST_가_다른_출처에서_오면_403(client, route):
    """CSRF 가드는 라우트마다가 아니라 미들웨어라, 하나라도 새면 전부 새는 구조다."""
    res = client.post(_fill(route.path, GOOD_PARAM), data={},
                      headers={"Origin": "http://evil.example"})
    assert res.status_code == 403, f"{route.path} → {res.status_code} (막혀야 한다)"


def test_재시작_라우트는_존재하되_테스트에서_부르지_않는다():
    paths = {r.path for r in app.routes if isinstance(r, Route)}
    assert "/settings/restart" in paths


def test_라우트_커버리지가_100퍼센트다():
    """이 파일이 실제로 모든 엔드포인트를 두들기는지 집합으로 확인한다.

    빠진 라우트가 있으면 그 자리는 아무도 안 본 채로 남는다. 새 라우트를 추가하면
    자동으로 목록에 들어오므로 따로 손볼 것이 없다.
    """
    전체 = {r.path for r in app.routes if isinstance(r, Route)}
    두들긴것 = {r.path for r in GET_ROUTES} | {r.path for r in POST_ROUTES}
    빠진것 = 전체 - 두들긴것 - SKIP_PATHS
    assert not 빠진것, f"테스트가 한 번도 부르지 않는 라우트: {sorted(빠진것)}"
    assert len(두들긴것) >= 70, len(두들긴것)
