# 장기 웹 화면의 기간 격자, 계층 표시와 삭제 복원 회귀 테스트.
from datetime import date, timedelta
import re

import pytest

from app.db import get_conn


def _add_item(client, title, start="2026-02-03", end="2026-02-10", parent=None):
    with get_conn() as conn:
        area = conn.execute("SELECT id FROM lt_area ORDER BY id LIMIT 1").fetchone()["id"]
    data = {"area_id": area, "title": title, "start": start, "end": end, "block": "B1"}
    if parent:
        data["parent_id"] = parent
    response = client.post("/plan/item/add", data=data)
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.mark.parametrize(("level", "anchor", "days"), [
    ("week", "2026-02-10", [7, 7, 7, 7]),
    ("month", "2024-02-10", [31, 29, 31, 30]),
    ("quarter", "2024-04-10", [91, 91, 92, 92]),
    ("year", "2024-07-10", [365, 366, 365, 365, 365]),
])
def test_장기_열과_눈금은_같은_일수로_그린다(client, level, anchor, days):
    response = client.get("/plan", params={"level": level, "anchor": anchor})
    assert response.status_code == 200
    assert f'data-days="{sum(days)}"' in response.text
    starts = re.findall(r'class="gt-brkline"[^>]*style="--p: ([\d.]+)', response.text)
    assert [float(pos) for pos in starts] == pytest.approx(
        [sum(days[:i]) / sum(days) * 100 for i in range(1, len(days))]
    )
    assert " ".join(f"minmax(0, {n}fr)" for n in days) in response.text


@pytest.mark.parametrize("level", ["week", "month", "quarter", "year"])
@pytest.mark.parametrize("anchor", [date.min, date.max])
def test_장기_달력_최소와_최대_날짜도_기간에_포함한다(client, level, anchor):
    response = client.get("/plan", params={"level": level, "anchor": anchor.isoformat()})
    assert response.status_code == 200
    first = date.fromisoformat(re.search(r'data-start="([\d-]+)" data-anchor=', response.text)[1])
    span = int(re.search(r'data-days="(\d+)"', response.text)[1])
    assert first <= anchor <= first + timedelta(days=span - 1)


def test_기간을_나중으로_옮길_때_두_날짜가_함께_저장된다(client):
    item = _add_item(client, "기간 이동")
    page = client.get("/plan", params={"level": "week", "anchor": "2026-02-03"})
    assert 'class="ghost-btn gt-e-apply-period"' in page.text
    ok = client.post("/plan/item/update", data={
        "id": item, "start": "2027-01-03", "end": "2027-01-10",
    })
    assert ok.status_code == 200
    bad = client.post("/plan/item/update", data={
        "id": item, "start": "2027-02-10", "end": "2027-02-05",
    })
    assert bad.status_code == 400
    focused = client.get("/plan", params={
        "level": "week", "anchor": "2026-02-03", "focus": item,
    })
    assert 'data-anchor="2027-01-03"' in focused.text
    with get_conn() as conn:
        row = conn.execute("SELECT start_date, end_date FROM lt_item WHERE id = ?", (item,)).fetchone()
    assert tuple(row) == ("2027-01-03", "2027-01-10")


def test_숨김은_하위를_지우거나_상위_진척을_편집하도록_만들지_않는다(client):
    parent = _add_item(client, "상위")
    child = _add_item(client, "하위", parent=parent)
    assert client.post("/plan/item/update", data={"id": child, "progress": 60, "hidden": 1}).json()["ok"]
    params = {"anchor": "2026-02-03"}
    page = client.get("/plan", params=params)
    assert 'data-title="하위"' not in page.text
    assert 'class="gt-bar is-parent' in page.text
    assert 'class="gt-fill" style="width: 60%"' in page.text
    assert client.post("/plan/item/update", data={"id": parent, "progress": 90}).status_code == 409
    assert client.post("/plan/item/update", data={"id": child, "hidden": 0}).json()["ok"]
    assert client.post("/plan/item/update", data={"id": parent, "hidden": 1}).json()["ok"]
    page = client.get("/plan", params=params)
    assert "숨긴 항목 1개 보기" in page.text
    assert 'data-title="하위"' in page.text


def test_숨은_손자를_포함한_삭제_범위를_검사하고_주간_계획까지_복원한다(client):
    parent = _add_item(client, "상위")
    child = _add_item(client, "하위", parent=parent)
    hidden = _add_item(client, "숨길 손자", parent=child)
    assert client.post("/plan/item/update", data={"id": hidden, "hidden": 1}).json()["ok"]
    preview_url = f"/plan/item/delete-preview/{parent}"
    preview = client.get(preview_url).json()
    assert (preview["descendants"], preview["hidden_descendants"]) == (2, 1)

    assert client.post("/plan/item/update", data={"id": child, "title": "바뀐 하위"}).json()["ok"]
    stale = client.post("/plan/item/delete", data={"id": parent, "revision": preview["revision"]})
    assert stale.status_code == 409

    assert client.post("/save/field", data={
        "entity": "ltgoal", "id": child, "field": "ltgoal",
        "week_start": "2026-02-02", "value": "이번 주에 유지할 계획",
    }).json()["ok"]
    preview = client.get(preview_url).json()
    deleted = client.post("/plan/item/delete", data={"id": parent, "revision": preview["revision"]}).json()
    assert deleted["deleted"] == 3
    assert client.get(preview_url).status_code == 404
    assert _add_item(client, "새 항목") > hidden

    restored = client.post("/plan/item/restore", data={"token": deleted["undo_token"]}).json()
    assert restored == {"ok": True, "id": parent, "restored": 3}
    with get_conn() as conn:
        tree = conn.execute(
            "SELECT id, parent_id, hidden FROM lt_item WHERE id IN (?, ?, ?) ORDER BY id",
            (parent, child, hidden),
        ).fetchall()
        goal = conn.execute(
            "SELECT goal_text FROM weekly_lt_goal WHERE item_id = ? AND week_start = '2026-02-02'", (child,)
        ).fetchone()
    assert [tuple(row) for row in tree] == [(parent, None, 0), (child, parent, 0), (hidden, child, 1)]
    assert goal["goal_text"] == "이번 주에 유지할 계획"
    assert client.post("/plan/item/restore", data={"token": deleted["undo_token"]}).status_code == 404
    assert client.get("/plan/item/delete-preview/9223372036854775808").status_code == 422
