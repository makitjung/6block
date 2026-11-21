from datetime import datetime, timedelta

from app.common import KST
from app.db import get_conn
from app.routes.analytics import _analytics_data


def _record_actual_only(client, date):
    assert client.get(f"/day/{date}").status_code == 200
    with get_conn() as conn:
        slot = conn.execute(
            "SELECT s.id FROM slots s JOIN blocks b ON b.id = s.block_id "
            "WHERE s.date = ? AND b.is_core = 1 ORDER BY s.slot_index LIMIT 1",
            (date,),
        ).fetchone()
    response = client.post("/save/field", data={
        "entity": "slot", "id": slot["id"], "field": "did_text",
        "value": "계획 없이 실행한 기록",
    })
    assert response.json()["ok"]


def test_opening_week_does_not_change_analytics(client):
    date = (datetime.now(KST).date() - timedelta(days=2)).isoformat()
    _record_actual_only(client, date)
    before = _analytics_data("all")

    assert client.get(f"/week/{date}").status_code == 200
    after = _analytics_data("all")

    assert before["start"] == after["start"] == date
    assert before["funnel"] == after["funnel"]
    assert after["funnel"]["done_slots"] == 1
    assert after["funnel"]["core_slots"] == 24
    assert after["summary"]["avg_done"] == 100


def test_export_defaults_include_actual_only_record(client):
    date = (datetime.now(KST).date() - timedelta(days=8)).isoformat()
    _record_actual_only(client, date)
    data = client.get("/data")
    assert data.status_code == 200
    assert f'id="set-csv-start" value="{date}"' in data.text
    assert "<strong>1</strong>개 슬롯" in data.text

    csv = client.get("/settings/export.csv", params={"start": date, "end": date})
    assert csv.status_code == 200
    assert "계획 없이 실행한 기록" in csv.text
