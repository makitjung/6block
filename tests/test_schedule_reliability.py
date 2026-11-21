import re

from app import common
from app.common import _split3
from app.config import DAY_BLOCKS
from app.db import get_conn, set_day_block_times


MONDAY = "2026-12-14"
TUESDAY = "2026-12-15"
WEDNESDAY = "2026-12-16"


def rows(sql, *args):
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(sql, args)]


def block(day, label="B1"):
    return rows("SELECT * FROM blocks WHERE date = ? AND block_label = ?", day, label)[0]


def slots(day, label="B1"):
    return rows(
        "SELECT s.* FROM slots s JOIN blocks b ON b.id = s.block_id "
        "WHERE s.date = ? AND b.block_label = ? ORDER BY s.slot_index", day, label,
    )


def post(client, path, **data):
    response = client.post(path, data=data)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"], payload
    return payload


def edit(client, entity, row_id, field, value, **extra):
    return client.post("/save/field", data={
        "entity": entity, "id": row_id, "field": field, "value": value, **extra,
    })


def time_template(client, **changes):
    template = post(client, "/settings/template/add", kind="S")["id"]
    form = {}
    for order, (_, _, start, end) in enumerate(DAY_BLOCKS):
        form[f"start_{order}"] = start
        form[f"end_{order}"] = end
    post(client, "/settings/template/times", template_id=template, **{**form, **changes})
    return template


def routine_template(client, *, start="08:00", weekday="0", span=1, text="Routine"):
    template = post(client, "/settings/template/add", kind="T")["id"]
    rule = post(client, "/settings/routine/add", template_id=template)["id"]
    post(client, "/settings/routine/save", id=rule, weekdays=weekday,
         start_time=start, span=span, do_text=text)
    return template


def apply_day(client, day, kind, template):
    return post(client, "/day/apply-template", date=day, kind=kind, template_id=template)


def weekly_template(client, **parts):
    template = post(client, "/settings/template/add", kind="W")["id"]
    for part, value in parts.items():
        post(client, "/settings/template/pick", id=template, part=part, value=value)
    return template


def identity(row):
    return {key: row[key] for key in ("date", "start_time", "end_time", "block_label") if key in row}


def version_field(response):
    assert response.status_code == 200
    field = re.search(r'name="schedule_version" value="([a-f0-9]{64})"', response.text)
    assert field
    return field[1]


def test_stale_tab_cannot_write_to_reused_or_missing_slots(client):
    old_version = version_field(client.get(f"/day/{MONDAY}"))
    old_block, old_slot = block(MONDAY), slots(MONDAY)[0]
    template = time_template(client, start_0="08:00")
    assert apply_day(client, MONDAY, "S", template)["days"] == 1
    assert slots(MONDAY)[0]["id"] == old_slot["id"]
    assert slots(MONDAY)[0]["start_time"] == "08:00"

    stale_edit = edit(client, "slot", old_slot["id"], "do_text", "07:30 draft", **identity(old_slot))
    assert stale_edit.status_code == 409
    assert stale_edit.json()["error"] == "stale-schedule"
    stale_done = client.post(f"/slot/done/{old_slot['id']}", data={"done": "1", **identity(old_slot)})
    assert stale_done.status_code == 409
    stale_block = edit(client, "block", old_block["id"], "plan_text", "old block", **identity(old_block))
    assert stale_block.status_code == 409
    full_save = client.post(f"/save/day/{MONDAY}", data={
        "schedule_version": old_version, f"do_{old_slot['id']}": "old form",
    })
    assert full_save.status_code == 409
    assert slots(MONDAY)[0]["do_text"] is None and slots(MONDAY)[0]["done"] == 0

    client.get(f"/day/{TUESDAY}").raise_for_status()
    missing = slots(TUESDAY)[0]
    client.get(f"/day/{WEDNESDAY}").raise_for_status()
    apply_day(client, TUESDAY, "S", template)
    assert edit(client, "slot", missing["id"], "did_text", "lost draft").status_code == 404
    assert client.post(f"/slot/done/{missing['id']}", data={"done": "1"}).status_code == 404


def test_full_forms_cannot_write_rows_from_another_day_or_obsolete_week(client):
    old_version = version_field(client.get(f"/week/{MONDAY}"))
    source = block(MONDAY)
    prev_week = "2026-12-07"
    client.get(f"/day/{prev_week}").raise_for_status()
    other = block(prev_week)
    template = time_template(client, start_0="08:00")
    apply_day(client, MONDAY, "S", template)
    assert client.post(f"/week/save/{MONDAY}", data={
        "schedule_version": old_version, f"bname_{source['id']}": "old week",
    }).status_code == 409
    assert client.post(f"/save/day/{MONDAY}", data={
        f"plan_{other['id']}": "another day",
    }, follow_redirects=False).status_code == 303
    assert block(prev_week)["plan_text"] is None


def test_slot_identity_checks_block_when_its_times_and_recycled_id_match(client):
    client.get(f"/day/{MONDAY}").raise_for_status()
    former = slots(MONDAY)[-1]
    assert former["start_time"] == "09:00"
    apply_day(client, MONDAY, "S", time_template(client, end_0="09:00", start_1="09:00"))
    current = slots(MONDAY, "B2")[0]
    assert current["id"] == former["id"] and identity(current) == identity(former)
    stale = edit(client, "slot", former["id"], "do_text", "Former B1", **identity(former), block_label="B1")
    assert stale.status_code == 409
    assert edit(client, "slot", current["id"], "do_text", "Current B2",
                **identity(current), block_label="B2", expected_value="").status_code == 200
    assert slots(MONDAY, "B2")[0]["do_text"] == "Current B2"


def test_schedule_rebuild_rechecks_newly_saved_slot_under_the_write_lock(client, monkeypatch):
    client.get(f"/day/{MONDAY}").raise_for_status()
    first = slots(MONDAY)[0]
    times = [{"start": start, "end": end} for _, _, start, end in DAY_BLOCKS]
    times[0]["start"] = "08:00"
    set_day_block_times(MONDAY, times)
    old_has_content = common._day_has_content

    def with_concurrent_save(conn, day):
        present = old_has_content(conn, day)
        if day == MONDAY and not conn.in_transaction:
            with get_conn() as writer:
                writer.execute("UPDATE slots SET do_text = ? WHERE id = ?", ("Concurrent plan", first["id"]))
        return present

    monkeypatch.setattr(common, "_day_has_content", with_concurrent_save)
    client.get(f"/day/{MONDAY}").raise_for_status()
    saved = slots(MONDAY)[0]
    assert (saved["id"], saved["start_time"], saved["do_text"]) == (first["id"], "07:30", "Concurrent plan")


def test_expected_value_merges_separate_goal_fields_and_recognizes_retries(client):
    assert edit(client, "meta", MONDAY, "goal1", "one", expected_value="").status_code == 200
    assert edit(client, "meta", MONDAY, "goal2", "two", expected_value="", goal1="old").status_code == 200
    assert edit(client, "meta", MONDAY, "goal1", " one ", expected_value="").status_code == 200
    conflict = edit(client, "meta", MONDAY, "goal1", "replacement", expected_value="")
    assert conflict.status_code == 409 and conflict.json()["current_value"] == "one"
    metas = rows("SELECT today_goal FROM daily_meta WHERE date = ?", MONDAY)
    assert _split3(metas[0]["today_goal"]) == ["one", "two", ""]
    assert edit(client, "wmeta", MONDAY, "wgoal1", "first", expected_value="").status_code == 200
    assert edit(client, "wmeta", MONDAY, "wgoal2", "second").status_code == 200
    weekly = rows("SELECT weekly_goal FROM weekly_meta WHERE week_start = ?", MONDAY)
    assert _split3(weekly[0]["weekly_goal"]) == ["first", "second", ""]


def test_expected_name_value_accepts_inheritance_and_legacy_theme_whitespace(client):
    client.get(f"/day/{MONDAY}").raise_for_status()
    assert edit(client, "theme", MONDAY, "theme", " Base ", label="B1").status_code == 200
    assert edit(client, "theme", MONDAY, "theme", "Inherited", label="B1",
                expected_value="Base").status_code == 200
    item = block(MONDAY)
    args = {**identity(item), "name_view": "override"}
    assert edit(client, "block", item["id"], "bname", "Inherited", expected_value="", **args).status_code == 200
    assert block(MONDAY)["name"] is None
    assert edit(client, "block", item["id"], "bname", "Explicit",
                expected_value="Inherited", **args).status_code == 200
    assert block(MONDAY)["name"] == "Explicit"


def test_saving_goal_of_deleted_plan_reports_missing_item(client):
    missing = edit(client, "ltgoal", 999, "ltgoal", "Keep draft", week_start=MONDAY, expected_value="")
    assert missing.status_code == 404 and missing.json()["error"] == "not-found"


def test_rollover_preview_reserves_exact_matches_and_undo_restores_overwritten_plan(client):
    client.get(f"/day/{MONDAY}").raise_for_status()
    template = time_template(client, start_0="08:00", end_0="10:00", start_1="10:00")
    apply_day(client, TUESDAY, "S", template)
    monday = block(MONDAY)
    first, second, *_ = slots(MONDAY)
    target = slots(TUESDAY)[0]
    assert edit(client, "slot", target["id"], "do_text", "Tuesday's plan").status_code == 200
    payload = {"block_id": monday["id"], **identity(monday), "plan": "Monday block",
               f"do_{first['id']}": "early", f"do_{second['id']}": "exact"}
    result = post(client, "/block/rollover", mode="preview", **payload)
    assert (result["moved"], result["skipped"]) == (1, 1)
    assert result["skipped_slots"] == [{"slot": "07:30", "text": "early"}]
    assert result["conflicts"][0]["current"] == "Tuesday's plan"
    assert slots(TUESDAY)[0]["do_text"] == "Tuesday's plan"

    applied = post(client, "/block/rollover", mode="apply", preview_token=result["preview_token"], **payload)
    assert [row["do_text"] for row in slots(TUESDAY)] == ["exact", None, None, None]
    assert block(TUESDAY)["plan_text"] == "Monday block"
    post(client, "/block/rollover/undo", undo_token=applied["undo_token"])
    assert slots(TUESDAY)[0]["do_text"] == "Tuesday's plan"
    assert block(TUESDAY)["plan_text"] is None


def test_rollover_confirmation_and_undo_protect_destination_edits(client):
    client.get(f"/day/{MONDAY}").raise_for_status()
    client.get(f"/day/{TUESDAY}").raise_for_status()
    first, *_ = slots(MONDAY)
    target = slots(TUESDAY)[0]
    payload = {"block_id": block(MONDAY)["id"], "plan": "Incoming plan", f"do_{first['id']}": "incoming"}
    preview = post(client, "/block/rollover", mode="preview", **payload)
    edit(client, "slot", target["id"], "do_text", "concurrent change").raise_for_status()
    stale = client.post("/block/rollover", data={
        "mode": "apply", "preview_token": preview["preview_token"], **payload,
    })
    assert stale.status_code == 409 and stale.json()["error"] == "rollover-changed"
    assert slots(TUESDAY)[0]["do_text"] == "concurrent change"

    preview = post(client, "/block/rollover", mode="preview", **payload)
    applied = post(client, "/block/rollover", mode="apply", preview_token=preview["preview_token"], **payload)
    edit(client, "slot", target["id"], "did_text", "new actual").raise_for_status()
    undo = client.post("/block/rollover/undo", data={"undo_token": applied["undo_token"]})
    assert undo.status_code == 409
    assert slots(TUESDAY)[0]["did_text"] == "new actual"
    assert block(TUESDAY)["plan_text"] == "Incoming plan"


def test_time_rebuild_reapplies_active_routine_and_blocks_unsafe_detach(client):
    routine = routine_template(client)
    assert apply_day(client, MONDAY, "T", routine)["filled"] == 1
    schedule = time_template(client, start_0="08:00")
    moved = apply_day(client, MONDAY, "S", schedule)
    assert (moved["days"], moved["filled"]) == (1, 1)
    slot = slots(MONDAY)[0]
    assert (slot["start_time"], slot["do_text"]) == ("08:00", "Routine")
    edit(client, "slot", slot["id"], "did_text", "Actual").raise_for_status()

    rejected = client.post("/day/apply-template", data={"date": MONDAY, "kind": "S", "template_id": ""})
    assert rejected.status_code == 409 and rejected.json()["error"] == "schedule-in-use"
    assert slots(MONDAY)[0]["did_text"] == "Actual"
    assert rows("SELECT kind, tpl_id FROM tpl_applied WHERE scope = 'day' AND key = ? AND kind = 'S'", MONDAY)


def test_manual_category_survives_time_change_after_routine_population(client):
    client.get(f"/day/{MONDAY}").raise_for_status()
    second = slots(MONDAY)[1]
    category = post(client, "/settings/category/add", name="Picked by user")["id"]
    edit(client, "slot", second["id"], "cat", str(category)).raise_for_status()
    routine = routine_template(client)
    assert apply_day(client, MONDAY, "T", routine)["filled"] == 1
    assert slots(MONDAY)[1]["is_routine"] == 1

    new_schedule = time_template(client, start_0="08:00")
    result = apply_day(client, MONDAY, "S", new_schedule)
    assert result["skipped_dates"] == [MONDAY] and not result["applied_template"]
    assert slots(MONDAY)[1]["category_id"] == category
    assert slots(MONDAY)[1]["do_text"] == "Routine"


def test_time_and_routine_precedence_respect_day_then_week_and_contiguous_slots(client):
    earlier = time_template(client, start_0="07:30", end_0="08:30")
    daily_routine = routine_template(client, start="08:00", span=2, text="Daily")
    apply_day(client, MONDAY, "S", earlier)
    result = apply_day(client, MONDAY, "T", daily_routine)
    assert (result["filled"], result["routine_skipped"]) == (1, 1)
    assert slots(MONDAY, "B1")[-1]["do_text"] == "Daily"
    assert slots(MONDAY, "B2")[0]["do_text"] is None

    weekly_routine = routine_template(client, start="08:00", text="Weekly")
    weekly_schedule = time_template(client, start_0="08:00")
    weekly = weekly_template(client, s=weekly_schedule, t=weekly_routine)
    applied = post(client, "/week/apply-template", week_start=MONDAY, template_id=weekly)
    assert applied["overridden_dates"] == [MONDAY]
    assert slots(MONDAY, "B1")[-1]["do_text"] == "Daily"
    assert apply_day(client, MONDAY, "T", "")["filled"] == 1
    assert slots(MONDAY, "B1")[-1]["do_text"] == "Weekly"
    assert apply_day(client, MONDAY, "S", "")["cleared"]
    assert block(MONDAY)["start_time"] == "08:00"
    assert slots(MONDAY, "B1")[0]["do_text"] == "Weekly"


def test_template_detach_restores_weekly_names_and_preserves_manual_field_edits(client):
    weekly_name = post(client, "/settings/template/add", kind="N")["id"]
    daily_name = post(client, "/settings/template/add", kind="N")["id"]
    for template, text in [(weekly_name, "Weekly"), (daily_name, "Daily")]:
        for label in ("B1", "B2"):
            post(client, "/settings/template/blockname", template_id=template, block_label=label, name=text)
    weekly = weekly_template(client, n=weekly_name)
    post(client, "/week/apply-template", week_start=MONDAY, template_id=weekly)
    apply_day(client, MONDAY, "N", daily_name)
    edited = block(MONDAY, "B2")
    edit(client, "block", edited["id"], "bname", "Manual").raise_for_status()
    assert apply_day(client, MONDAY, "N", "")["cleared"]
    rendered = client.get(f"/day/{MONDAY}").text
    assert f'name="bname_{block(MONDAY)["id"]}" value="Weekly"' in rendered
    assert f'name="bname_{edited["id"]}" value="Manual"' in rendered


def test_template_accepts_routine_on_custom_session_slot(client):
    schedule = time_template(client, start_0="07:10", end_0="09:10")
    routine = routine_template(client, start="07:10")
    apply_day(client, MONDAY, "S", schedule)
    assert apply_day(client, MONDAY, "T", routine)["filled"] == 1
    assert slots(MONDAY)[0]["do_text"] == "Routine"


def test_hidden_category_is_preserved_by_day_form_save(client):
    client.get(f"/day/{MONDAY}").raise_for_status()
    slot_id = slots(MONDAY)[0]["id"]
    category = post(client, "/settings/category/add", name="Original category")["id"]
    edit(client, "slot", slot_id, "cat", str(category)).raise_for_status()
    post(client, "/settings/category/delete", id=category)
    day = client.get(f"/day/{MONDAY}")
    field = re.search(rf'<select name="cat_{slot_id}".*?</select>', day.text, re.S)
    assert field and re.search(rf'<option value="{category}"[^>]*selected', field[0])
    assert client.post(f"/save/day/{MONDAY}", data={
        "schedule_version": version_field(day), f"cat_{slot_id}": str(category),
    }, follow_redirects=False).status_code == 303
    assert slots(MONDAY)[0]["category_id"] == category


def test_tomorrow_goal_only_clears_metadata_of_confirmed_replacement(client):
    for field, value in [("goal1", "Existing"), ("goaltag1", "#old"), ("goallink1", "wk:1"),
                         ("goal2", "Keep"), ("goaltag2", "#keep"), ("goallink2", "wk:2")]:
        edit(client, "meta", TUESDAY, field, value).raise_for_status()
    args = {"date": MONDAY, "text": "Replacement"}
    preview = post(client, "/meta/tomorrow-goal", mode="preview", **args)
    assert preview["current"] == "Existing"
    edit(client, "meta", TUESDAY, "goaltag1", "#concurrent").raise_for_status()
    assert client.post("/meta/tomorrow-goal", data={
        "mode": "apply", "preview_token": preview["preview_token"], **args,
    }).status_code == 409
    preview = post(client, "/meta/tomorrow-goal", mode="preview", **args)
    post(client, "/meta/tomorrow-goal", mode="apply", preview_token=preview["preview_token"], **args)
    values = rows("SELECT today_goal, goal_tags, goal_links FROM daily_meta WHERE date = ?", TUESDAY)[0]
    assert [_split3(value) for value in values.values()] == [
        ["Replacement", "Keep", ""], ["", "#keep", ""], ["", "wk:2", ""],
    ]
