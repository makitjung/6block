# 주간 화면(주간 목표·블록 테마·통계·7일 보기)과 그 저장을 담당하는 라우터
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.common import (
    CORE_LABELS,
    KO_WEEKDAYS,
    KST,
    SLOT_HAS_CONTENT,
    _ai_split,
    _day_has_content,
    _join3,
    _name_override,
    _off_loop,
    _parse_date,
    _rule_distribute,
    _short_date,
    _skeleton_matches_config,
    _split3,
    ensure_day_skeleton,
    int_id,
    opt_id,
    schedule_version,
    templates,
    today_str,
    tpl_title,
    week_lt_items,
    week_start,
)
from app.config import WEEK_CORE_BLOCKS, hhmm_to_min
from app.db import (
    _json_obj,
    get_conn,
    get_day_blocks,
    set_day_block_times,
    set_week_block_times,
    template_day_blocks,
    template_has_times,
)
from app.integrations import ai, gcal

router = APIRouter()


def _week_lt_rows(rows, goals, monday: date) -> list[dict]:
    """주간 목표 열에 세울 장기 항목을 상위별로 묶는다.

    같은 상위에서 내려온 것끼리 한 묶음이 되어, 그냥 늘어놓았을 때 안 보이던
    '이것들이 한 계획'이 드러난다. 묶음이 둘 이상일 때만 상위 제목을 머리줄로 세우고
    하나뿐이면 예전처럼 줄 앞에 붙인다(줄만 늘어나지 않게).

    그 주를 다 덮지 않고 일부만 걸치는 항목에는 걸친 요일과 날짜를 붙인다.
    """
    sunday = monday + timedelta(days=6)
    groups: list[dict] = []
    for r in rows:
        s = _parse_date(r["start_date"]) or monday
        e = _parse_date(r["end_date"]) or sunday
        vs, ve = max(s, monday), min(e, sunday)
        part = (s > monday or e < sunday) and vs <= ve
        blocks = [b for b in (r["block_label"] or "").split(",") if b]
        item = {
            "id": r["id"], "title": r["title"], "area_name": r["area_name"],
            "progress": r["progress"], "parent_title": r["parent_title"],
            "goal": goals.get(r["id"], ""),
            "range": f"{_short_date(r['start_date'])}~{_short_date(r['end_date'])}",
            # 장기 탭에서 정해 둔 코어블록. 첫 번째를 '블록으로'의 기본값으로 쓴다.
            "blocks": ",".join(blocks),
            "block": blocks[0] if blocks else "",
            "part": ("" if not part else
                     KO_WEEKDAYS[vs.weekday()] if vs == ve else
                     f"{KO_WEEKDAYS[vs.weekday()]}~{KO_WEEKDAYS[ve.weekday()]}"),
            "part_date": f"{vs.month}/{vs.day}~{ve.month}/{ve.day}" if part else "",
        }
        key = r["parent_id"] if r["parent_title"] else None
        if key is not None and groups and groups[-1]["key"] == key:
            groups[-1]["items"].append(item)
        else:
            groups.append({"key": key, "title": r["parent_title"] if key else "",
                           "items": [item]})
    for g in groups:
        g["grouped"] = bool(g["key"]) and len(g["items"]) > 1
    return groups


@router.get("/week")
def week_view(request: Request):
    return _week_view(request, week_start(datetime.now(KST).date()))


@router.get("/week/{date_str}")
def week_view_for(request: Request, date_str: str):
    # 날짜가 아닌 것이 들어오면 500 대신 이번 주로 보낸다(day.py 의 /day 와 같은 규칙).
    d = _parse_date(date_str)
    if d is None:
        return RedirectResponse(url="/week")
    return _week_view(request, week_start(d))


def _week_view(request: Request, monday: date):
    dates = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    placeholders = ",".join("?" * len(dates))
    week_start_str = monday.strftime("%Y-%m-%d")
    # 그 해의 몇 번째 주인지(ISO 기준, 월요일 시작이라 이 앱의 주 나누기와 같다)
    week_no = monday.isocalendar()[1]
    prev_week = (monday - timedelta(days=7)).strftime("%Y-%m-%d")
    next_week = (monday + timedelta(days=7)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        for ds in dates:
            ensure_day_skeleton(conn, ds)
        version = schedule_version(conn, dates)
        rows = conn.execute(
            f"SELECT id, date, block_label, block_order, is_core, plan_text, "
            f"       see_text, name, category_id, start_time, end_time FROM blocks "
            f"WHERE date IN ({placeholders}) ORDER BY date, block_order",
            dates,
        ).fetchall()
        categories = [
            dict(r)
            for r in conn.execute(
                "SELECT id, name, tone, is_active FROM categories ORDER BY display_order"
            )
        ]
        # 슬롯 구분이 비면(NULL) 그 슬롯이 속한 블록 구분을 따른다(블록→슬롯 상속).
        #
        # 세는 대상은 '내용이 있는 슬롯'이다. 계획(do_text)·한 일(did_text)·완료 체크 중
        # 하나라도 있어야 한다. 고정 할일이 채워 넣은 칸은 사람이 적은 것이 아니므로
        # do_text 만으로는 안 세고, 체크했거나 한 일을 적었을 때만 센다
        # (common._day_has_content 와 같은 기준).
        #
        # 그리고 categories 는 LEFT JOIN 이다. INNER JOIN 이던 시절에는 구분을 안 고른
        # 슬롯이 통째로 빠져, 코어 블록에 하루를 다 쓰고도 '기록된 시간'이 0 이었다.
        # 이제 구분이 없는 시간은 '미지정' 한 줄로 모아 보여 준다.
        week_total_hours = conn.execute(f"SELECT COUNT(*) FROM slots WHERE date IN ({placeholders})",
                                        dates).fetchone()[0] * 0.5
        cat_summary = conn.execute(
            f"""
            SELECT COALESCE(c.name, '미지정') AS name,
                   COALESCE(c.tone, 'gray') AS tone,
                   COUNT(s.id) AS slot_count
            FROM slots s
            JOIN blocks b ON b.id = s.block_id
            LEFT JOIN categories c ON c.id = COALESCE(s.category_id, b.category_id)
            WHERE s.date IN ({placeholders}) AND {SLOT_HAS_CONTENT}
            GROUP BY c.id
            ORDER BY slot_count DESC
            """,
            dates,
        ).fetchall()
        # 코어 칸과 PLAN → DO 달성률은 '계획(PLAN)을 적은 코어 블록'을 센다. 분모(total_core)가
        # 한 주 코어 블록 수(6×7=42)라 분자도 블록이어야 한다. 예전에는 슬롯을 세어 블록 하나가
        # 4로 잡혔고, 그래서 코어 칸이 정확히 4배로 부풀고 11블록만 채워도 42를 넘었다.
        # 판정 기준은 분석 탭의 PLAN→DO(analytics._exec_funnel 아래 pd_rows)와 같게 맞춘다.
        # 두 화면이 같은 이름의 지표를 서로 다르게 계산하던 것을 여기서 일원화한다.
        plan_total = conn.execute(
            f"""
            SELECT COUNT(*) FROM blocks b
            WHERE b.date IN ({placeholders}) AND b.is_core = 1
              AND TRIM(COALESCE(b.plan_text, '')) != ''
            """,
            dates,
        ).fetchone()[0]
        # 고정 할일이 채운 칸(is_routine=1)은 사람이 세운 계획이 아니므로 달성으로 세지 않는다.
        # 체크(done)는 사람이 한 행동이라 그대로 센다. 그 블록의 슬롯 중 하나라도 해당하면 달성.
        achieved = conn.execute(
            f"""
            SELECT COUNT(*) FROM blocks b
            WHERE b.date IN ({placeholders}) AND b.is_core = 1
              AND TRIM(COALESCE(b.plan_text, '')) != ''
              AND EXISTS (SELECT 1 FROM slots s WHERE s.block_id = b.id AND {SLOT_HAS_CONTENT})
            """,
            dates,
        ).fetchone()[0]
        wmeta = conn.execute(
            "SELECT * FROM weekly_meta WHERE week_start = ?", (week_start_str,)
        ).fetchone()
        theme_rows = conn.execute(
            "SELECT block_label, theme_text FROM weekly_block_themes "
            "WHERE week_start = ?",
            (week_start_str,),
        ).fetchall()
        # 머리의 고르는 칸은 주간(W)만, 날짜 머리의 ⋯ 창은 나머지 넷을 쓴다.
        tpl_rows = conn.execute(
            "SELECT id, kind, no, name FROM cat_template ORDER BY kind, no"
        ).fetchall()
        applied_rows = conn.execute(
            "SELECT scope, key, kind, tpl_id FROM tpl_applied "
            "WHERE (scope = 'week' AND key = ?) OR (scope = 'day' AND key BETWEEN ? AND ?)",
            (week_start_str, dates[0], dates[6]),
        ).fetchall()
        # 이번 주 장기 항목: 장기 탭 계획 막대 중 이 주에 걸친 것. 여기서 진척률을 고치고
        # 주간 목표·블록 테마로 바로 옮긴다(장기 ↔ 주간 연동 지점).
        wk_items = week_lt_items(conn, week_start_str)
        # 목표 열: 장기 항목마다 그 주 계획을 따로 적는다(항목 id로 묶어 저장).
        lt_goal_by_item = {
            r["item_id"]: (r["goal_text"] or "")
            for r in conn.execute(
                "SELECT item_id, goal_text FROM weekly_lt_goal WHERE week_start = ?",
                (week_start_str,),
            )
        }

    blocks_by_date: dict[str, list] = {d: [] for d in dates}
    for r in rows:
        blocks_by_date[r["date"]].append(r)

    # 블록 이름 카드에서 '요일별'을 펼쳤을 때 쓸 칸. 라벨(B1~B6)마다 그 주 7일치 블록을 모은다.
    # name 은 그 날만의 덮어쓰기값이라 비어 있으면 주간 이름을 따른다.
    name_cells: dict[str, list] = {}
    for ds in dates:
        for b in blocks_by_date[ds]:
            if b["is_core"]:
                name_cells.setdefault(b["block_label"], []).append(
                    {"id": b["id"], "date": ds, "name": (b["name"] or "").strip()}
                )

    # 주간 캘린더: 각 날짜 일정을 블록(block_order)에 매핑, 종일 일정은 따로.
    cal_by_date = gcal.events_for_range(monday, monday + timedelta(days=6))
    week_block_events: dict[str, dict[int, list]] = {}
    week_allday: dict[str, list] = {}
    for ds in dates:
        ranges = [
            (b["block_order"], hhmm_to_min(b["start_time"]), hhmm_to_min(b["end_time"]))
            for b in blocks_by_date[ds]
        ]
        by_order: dict[int, list] = {}
        allday: list = []
        for ev in cal_by_date.get(ds, []):
            if ev["all_day"] or ev["start_min"] is None:
                allday.append({"title": ev["title"], "color": ev["color"]})
                continue
            for order, s, e in ranges:
                if s <= ev["start_min"] < e:
                    by_order.setdefault(order, []).append(
                        {"time": ev["start"], "title": ev["title"], "color": ev["color"]}
                    )
                    break
        week_block_events[ds] = by_order
        week_allday[ds] = allday

    themes_by_label = {r["block_label"]: r["theme_text"] for r in theme_rows}
    # 최하위 항목만 내려오고, 어느 상위에서 나온 것인지 제목을 함께 붙인다.
    week_groups = _week_lt_rows(wk_items, lt_goal_by_item, monday)
    achieve_pct = round(achieved / plan_total * 100) if plan_total else 0
    used_core_total = WEEK_CORE_BLOCKS

    total_slots = sum(r["slot_count"] for r in cat_summary)
    cat_summary_pct = [
        {
            "name": r["name"],
            "tone": r["tone"],
            "slot_count": r["slot_count"],
            "hours": r["slot_count"] * 0.5,
            "pct": round(r["slot_count"] / total_slots * 100, 1) if total_slots else 0,
        }
        for r in cat_summary
    ]

    return templates.TemplateResponse(
        request,
        "week.html",
        {
            "week_start": week_start_str,
            "schedule_version": version,
            "week_no": week_no,
            "prev_week": prev_week,
            "next_week": next_week,
            "dates": dates,
            "blocks_by_date": blocks_by_date,
            "categories": categories,
            "cat_summary": cat_summary_pct,
            "used_core": plan_total,
            "total_core": used_core_total,
            "achieve_pct": achieve_pct,
            # 요일마다 블록 시간이 다를 수 있으므로 7일치 슬롯 수를 각각 세어 합친다.
            "week_total_hours": week_total_hours,
            "wmeta": wmeta,
            # 목표 열의 자유 란 3개(장기와 무관한 그 주만의 목표)
            "weekly_goals": _split3(wmeta["weekly_goal"] if wmeta else ""),
            "themes_by_label": themes_by_label,
            "name_cells": name_cells,
            "week_templates": [
                {"id": t["id"], "title": tpl_title(t["kind"], t["no"], t["name"])}
                for t in tpl_rows if t["kind"] == "W"
            ],
            # 일별 창이 고를 것. 종류 글자로 묶어 보낸다({'S': [...], 'T': [...]}).
            "day_templates": {
                k: [
                    {"id": t["id"], "title": tpl_title(t["kind"], t["no"], t["name"])}
                    for t in tpl_rows if t["kind"] == k
                ]
                for k in ("S", "T", "N", "C")
            },
            # 걸어 둔 것을 칩으로 보일 때 쓰는 이름표({12: 'S1(기본)'})
            "tpl_titles": {
                t["id"]: tpl_title(t["kind"], t["no"], t["name"])
                for t in tpl_rows if t["kind"] in ("S", "T", "N", "C")
            },
            "applied_week": next(
                (r["tpl_id"] for r in applied_rows
                 if r["scope"] == "week" and r["kind"] == "W"), None),
            # {'2026-09-08': {'S': 3, 'T': 5}} — 그 날 걸어 둔 것
            "applied_day": {
                ds: {r["kind"]: r["tpl_id"] for r in applied_rows
                     if r["scope"] == "day" and r["key"] == ds}
                for ds in dates
            },
            "week_groups": week_groups,
            "core_labels": CORE_LABELS,
            "week_block_events": week_block_events,
            "week_allday": week_allday,
            "cal_enabled": gcal.enabled(),
            "today": today_str(),
        },
    )


@router.post("/week/save/{week_start_str}")
async def save_week(week_start_str: str, request: Request):
    d0 = _parse_date(week_start_str)
    if d0 is None:
        return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
    form = await request.form()
    now = datetime.now(KST).isoformat(timespec="seconds")
    wk_dates = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if form.get("schedule_version") and form["schedule_version"] != schedule_version(conn, wk_dates):
            return JSONResponse({"ok": False, "error": "stale-schedule"}, status_code=409)
        conn.execute(
            """
            INSERT INTO weekly_meta (week_start, weekly_goal, appointments, vow, memo)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                weekly_goal = excluded.weekly_goal,
                appointments = excluded.appointments,
                vow = excluded.vow,
                memo = excluded.memo
            """,
            (
                week_start_str,
                _join3(form, "wgoal"),      # 목표 열의 자유 란 3개
                form.get("appointments", ""),
                form.get("vow", ""),
                form.get("memo", ""),
            ),
        )
        # 목표 열에서 장기 항목마다 적은 그 주 계획(항목 id로 묶어 저장)
        for key, val in form.multi_items():
            if not key.startswith("ltgoal_") or not key[7:].isdigit():
                continue
            conn.execute(
                """
                INSERT INTO weekly_lt_goal (week_start, item_id, goal_text, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(week_start, item_id) DO UPDATE SET
                    goal_text = excluded.goal_text,
                    updated_at = excluded.updated_at
                """,
                (week_start_str, int(key[7:]), (val or "").strip(), now),
            )
        for label in CORE_LABELS:
            key = f"theme_{label}"
            txt = form.get(key, "")
            conn.execute(
                """
                INSERT INTO weekly_block_themes (week_start, block_label,
                                                  theme_text, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(week_start, block_label) DO UPDATE SET
                    theme_text = excluded.theme_text,
                    updated_at = excluded.updated_at
                """,
                (week_start_str, label, txt, now),
            )
        # 7일보기에서 직접 편집한 블록 이름·구분 저장(이름이 비거나 주간 이름과 같으면 상속)
        ph = ",".join("?" * len(wk_dates))
        block_label_by_id = {
            r["id"]: r["block_label"]
            for r in conn.execute(
                f"SELECT id, block_label FROM blocks WHERE date IN ({ph})", wk_dates
            )
        }
        weekly_name = {
            lbl: (form.get(f"theme_{lbl}", "") or "").strip() for lbl in CORE_LABELS
        }
        for key, val in form.multi_items():
            prefix, _, suffix = key.partition("_")
            # 칸 이름 뒤 숫자가 행 id 다. 못 읽거나 범위를 벗어나면 맞는 행이 없으니 건너뛴다.
            sid = opt_id(suffix)
            if sid not in block_label_by_id:
                continue
            if prefix == "bname":
                label = block_label_by_id.get(sid, "")
                override = _name_override(val, weekly_name.get(label, ""))
                conn.execute(
                    "UPDATE blocks SET name = ?, updated_at = ? WHERE id = ?",
                    (override, now, sid),
                )
            elif prefix == "bcat":
                cid = opt_id(val)
                conn.execute(
                    "UPDATE blocks SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, sid),
                )
    return RedirectResponse(url=f"/week/{week_start_str}", status_code=303)


# -- 템플릿 적용 ---------------------------------------------------------------
# 다섯 종류 중 내용을 가진 넷(S 세션시간 · T 고정할일 · N 블록이름 · C 구분)은 아래
# 네 함수가 적용한다. 주간(W)은 넷을 골라 묶기만 하므로 그 넷을 차례로 부른다.
# 날짜 목록만 다르게 주면 한 주에도, 하루에만도 같은 길로 적용된다.


def _tpl(conn, tid: int, kind: str | None = None):
    """템플릿 한 줄. kind 를 주면 그 종류가 아닐 때 None(칸이 섞이지 않게)."""
    row = conn.execute(
        "SELECT id, kind, no, name, times_common, times_wd, block_names, "
        "s_id, t_id, n_id, c_id FROM cat_template WHERE id = ?",
        (tid,),
    ).fetchone()
    if row and kind and row["kind"] != kind:
        return None
    return row


def _mark_applied(conn, scope: str, key: str, kind: str, tid, now: str):
    """어느 주·어느 날에 무엇을 걸었는지 적어 둔다(화면이 골라 둔 채로 뜨게)."""
    if tid is None:
        conn.execute(
            "DELETE FROM tpl_applied WHERE scope = ? AND key = ? AND kind = ?",
            (scope, key, kind),
        )
        return
    conn.execute(
        "INSERT INTO tpl_applied (scope, key, kind, tpl_id, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(scope, key, kind) DO UPDATE SET "
        "tpl_id = excluded.tpl_id, updated_at = excluded.updated_at",
        (scope, key, kind, tid, now),
    )


def _applied_template(conn, scope, key, kind):
    row = conn.execute(
        "SELECT tpl_id FROM tpl_applied WHERE scope = ? AND key = ? AND kind = ?",
        (scope, key, kind),
    ).fetchone()
    return _tpl(conn, row["tpl_id"], kind) if row and row["tpl_id"] else None


def _weekly_part(conn, ws, kind):
    tracked = _applied_template(conn, "week", ws, kind)
    if tracked is not None:
        return tracked
    week = _applied_template(conn, "week", ws, "W")
    part_id = week[kind.lower() + "_id"] if week else None
    return _tpl(conn, part_id, kind) if part_id else None


_TEMPLATE_FIELDS = {
    "blocks": {"name", "category_id"},
    "slots": {"category_id", "do_text"},
    "weekly_block_themes": {"theme_text"},
}
_ROUTINE_UNTOUCHED = (
    "is_routine = 1 AND done = 0 AND TRIM(COALESCE(did_text, '')) = '' "
    "AND TRIM(COALESCE(wk_todo, '')) = ''"
)


def _restore_template_fields(conn, ds, kind, scope=None):
    """Remove copied values only if the user has not subsequently edited them."""
    params = [ds, kind]
    where = "date = ? AND kind = ?"
    if scope is not None:
        where += " AND scope = ?"
        params.append(scope)
    rows = conn.execute(f"SELECT * FROM template_values WHERE {where} ORDER BY field", params).fetchall()
    restored = 0
    for row in rows:
        table, field = row["table_name"], row["field"]
        if field not in _TEMPLATE_FIELDS.get(table, ()):
            continue
        extra_set = ", is_routine = 0" if field == "do_text" else ""
        guard = f" AND {_ROUTINE_UNTOUCHED}" if kind == "T" else ""
        updated = conn.execute(
            f"UPDATE {table} SET {field} = ?{extra_set} WHERE id = ? AND {field} IS ?{guard}",
            (row["previous_value"], row["row_id"], row["applied_value"]),
        )
        restored += updated.rowcount
    conn.execute(f"DELETE FROM template_values WHERE {where}", params)
    return restored


def _set_template_field(conn, ds, scope, kind, table, row, field, value, now):
    previous = row[field]
    if previous is not None and str(previous).strip():
        return False
    extra_set = ", is_routine = 1" if field == "do_text" else ""
    conn.execute(f"UPDATE {table} SET {field} = ?, updated_at = ?{extra_set} WHERE id = ?",
                 (value, now, row["id"]))
    conn.execute(
        "INSERT INTO template_values(date, scope, kind, table_name, row_id, field, "
        "previous_value, applied_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (table_name, row_id, field) DO UPDATE SET date = excluded.date, "
        "scope = excluded.scope, kind = excluded.kind, previous_value = excluded.previous_value, "
        "applied_value = excluded.applied_value",
        (ds, scope, kind, table, row["id"], field, previous, value),
    )
    return True


def _schedule_can_change(conn, ds, wanted):
    if _skeleton_matches_config(conn, ds, wanted):
        return True, False
    # Names and categories copied by templates can be rebuilt. Preserve all
    # current values and their source metadata if the day has explicit entries.
    conn.execute("SAVEPOINT check_schedule")
    _restore_template_fields(conn, ds, "N", "day")
    _restore_template_fields(conn, ds, "C")
    _restore_template_fields(conn, ds, "T")
    can_change = not _day_has_content(conn, ds)
    if not can_change:
        conn.execute("ROLLBACK TO check_schedule")
    conn.execute("RELEASE check_schedule")
    return can_change, can_change


def _template_times(tpl, weekday):
    return template_day_blocks(tpl["times_common"], tpl["times_wd"], weekday)


def _time_pairs(blocks):
    return [{"start": start, "end": end} for _label, _core, start, end in blocks]


def _apply_session_times(conn, tpl, dates, week_key, out):
    """Apply effective schedules, retaining the previous schedule on skipped days."""
    changed = set()
    if tpl and not template_has_times(tpl["times_common"], tpl["times_wd"]):
        return changed
    if week_key:
        saved = conn.execute("SELECT times FROM week_block_time WHERE week_start = ?",
                             (week_key,)).fetchone()
        times = _json_obj(saved["times"] if saved else None)
    for ds in dates:
        d = date.fromisoformat(ds)
        weekday = d.weekday()
        if week_key and conn.execute("SELECT 1 FROM day_block_time WHERE date = ?", (ds,)).fetchone():
            # The new weekly hours are available when this daily override is detached.
            times[str(weekday)] = _time_pairs(_template_times(tpl, weekday))
            out["overridden_dates"].append(ds)
            continue
        wanted = (_template_times(tpl, weekday) if tpl else
                  get_day_blocks(weekday, week_start(d).isoformat(), conn=conn))
        can_change, rebuild = _schedule_can_change(conn, ds, wanted)
        if not can_change:
            out["skipped_dates"].append(ds)
            continue
        if week_key:
            times[str(weekday)] = _time_pairs(wanted)
        else:
            set_day_block_times(ds, _time_pairs(wanted) if tpl else None, conn)
        if rebuild:
            changed.add(ds)
        out["days"] += 1
    if week_key and times:
        set_week_block_times(week_key, times, conn)
    out["skipped_days"] = len(out["skipped_dates"])
    return changed


def _apply_name_tpl(conn, tpl, key, scope, now):
    names = _json_obj(tpl["block_names"])
    changed = preserved = 0
    _restore_template_fields(conn, key, "N", scope)
    for label in CORE_LABELS:
        text = str(names.get(label) or "").strip()
        if not text:
            continue
        if scope == "week":
            conn.execute(
                "INSERT INTO weekly_block_themes(week_start, block_label, theme_text, updated_at) "
                "VALUES (?, ?, '', ?) ON CONFLICT(week_start, block_label) DO NOTHING",
                (key, label, now),
            )
            table, key_field, name_field = "weekly_block_themes", "week_start", "theme_text"
        else:
            table, key_field, name_field = "blocks", "date", "name"
        row = conn.execute(f"SELECT id, {name_field} FROM {table} "
                           f"WHERE {key_field} = ? AND block_label = ?", (key, label)).fetchone()
        if row and _set_template_field(conn, key, scope, "N", table, row, name_field, text, now):
            changed += 1
        elif row:
            preserved += 1
    return changed, preserved


def _apply_cat_tpl(conn, tpl, ds, scope, now):
    _restore_template_fields(conn, ds, "C")
    weekday = date.fromisoformat(ds).weekday()
    applied = slotted = preserved = 0
    for row in conn.execute(
        "SELECT b.id, b.category_id, c.category_id AS tpl_category "
        "FROM cat_template_cell c JOIN blocks b ON b.block_label = c.block_label "
        "WHERE c.template_id = ? AND c.weekday = ? AND b.date = ? AND b.is_core = 1 "
        "AND c.category_id IS NOT NULL", (tpl["id"], weekday, ds),
    ).fetchall():
        if _set_template_field(conn, ds, scope, "C", "blocks", row, "category_id", row["tpl_category"], now):
            applied += 1
        else:
            preserved += 1
    for cell in conn.execute(
        "SELECT block_label, p, category_id FROM cat_template_slot "
        "WHERE template_id = ? AND weekday = ? AND category_id IS NOT NULL",
        (tpl["id"], weekday),
    ).fetchall():
        row = conn.execute(
            "SELECT s.id, s.category_id FROM slots s JOIN blocks b ON b.id = s.block_id "
            "WHERE s.date = ? AND b.block_label = ? ORDER BY s.slot_index LIMIT 1 OFFSET ?",
            (ds, cell["block_label"], cell["p"] - 1),
        ).fetchone()
        if row and _set_template_field(conn, ds, scope, "C", "slots", row,
                                       "category_id", cell["category_id"], now):
            slotted += 1
        elif row:
            preserved += 1
    return applied, slotted, preserved


def _clear_routine(conn, ds):
    restored = _restore_template_fields(conn, ds, "T")
    # Older applications used only the flag. Preserve categories with unknown
    # provenance, and keep completed or annotated routine slots.
    legacy = conn.execute(
        f"UPDATE slots SET do_text = NULL, is_routine = 0 WHERE date = ? AND {_ROUTINE_UNTOUCHED}",
        (ds,),
    )
    return restored + legacy.rowcount


def _apply_rule_tpl(conn, tpl, ds, scope, now):
    _clear_routine(conn, ds)
    slots = {r["start_time"]: r["id"] for r in conn.execute(
        "SELECT id, start_time FROM slots WHERE date = ?", (ds,),
    )}
    weekday = str(date.fromisoformat(ds).weekday())
    filled = skipped = 0
    for rule in conn.execute(
        "SELECT weekdays, start_time, span, do_text, category_id "
        "FROM routine_rule WHERE template_id = ? ORDER BY display_order, id", (tpl["id"],),
    ).fetchall():
        text = (rule["do_text"] or "").strip()
        if not text or weekday not in rule["weekdays"].split(","):
            continue
        time = rule["start_time"]
        span = min(4, max(1, rule["span"] or 1))
        for step in range(span):
            row = conn.execute("SELECT * FROM slots WHERE id = ?", (slots.get(time),)).fetchone()
            if not row:
                skipped += span - step
                break
            time = row["end_time"]
            if row["done"] or (row["did_text"] or "").strip() or (row["wk_todo"] or "").strip():
                skipped += 1
                continue
            if not _set_template_field(conn, ds, scope, "T", "slots", row, "do_text", text, now):
                skipped += 1
                continue
            filled += 1
            if rule["category_id"] is not None:
                _set_template_field(conn, ds, scope, "T", "slots", row, "category_id",
                                    rule["category_id"], now)
    return filled, skipped


def _template_response():
    return {"days": 0, "skipped_days": 0, "skipped_dates": [], "overridden_dates": [],
            "names": 0, "applied": 0, "slots": 0, "filled": 0,
            "preserved_cells": 0, "routine_skipped": 0}


def _apply_choices(conn, parts, dates, week_key, now, *, detach_time=False):
    out = _template_response()
    rebuilt = set()
    if "S" in parts or detach_time:
        rebuilt = _apply_session_times(conn, parts.get("S"), dates, week_key, out)
    for ds in dates:
        ensure_day_skeleton(conn, ds, schedule_change=ds in rebuilt)
    if week_key and "N" in parts:
        out["names"], out["preserved_cells"] = _apply_name_tpl(
            conn, parts["N"], week_key, "week", now,
        )
    for ds in dates:
        ws = week_key or week_start(date.fromisoformat(ds)).isoformat()
        if (not week_key and "N" in parts) or ds in rebuilt:
            names = parts.get("N") if not week_key else None
            names = names or _applied_template(conn, "day", ds, "N")
            if names:
                changed, preserved = _apply_name_tpl(conn, names, ds, "day", now)
                out["names"] += changed
                out["preserved_cells"] += preserved
        for kind in ("C", "T"):
            day_choice = _applied_template(conn, "day", ds, kind)
            if not week_key and kind in parts:
                tpl, scope = parts[kind], "day"
            elif kind in parts and not day_choice:
                tpl, scope = parts[kind], "week"
            elif ds in rebuilt:
                tpl = day_choice or _weekly_part(conn, ws, kind)
                scope = "day" if day_choice else "week"
            else:
                continue
            if tpl and kind == "C":
                applied, slots, preserved = _apply_cat_tpl(conn, tpl, ds, scope, now)
                out["applied"] += applied
                out["slots"] += slots
                out["preserved_cells"] += preserved
            elif tpl:
                filled, skipped = _apply_rule_tpl(conn, tpl, ds, scope, now)
                out["filled"] += filled
                out["routine_skipped"] += skipped
    return out


@router.post("/week/apply-template")
async def week_apply_template(request: Request):
    """고른 주간(W) 템플릿을 그 주 7일에 적용한다. 그 안에서 고른 부분만 손댄다.

    주간은 세션시간·고정할일·블록이름·구분 중 고른 것을 묶기만 한다. 안 고른 부분은
    건드리지 않으므로, 넷을 다 고른 것도 하나만 고른 것도 같은 길로 적용된다.
    template_id 가 비면 이 주에 걸어 둔 주간 템플릿 표시만 지운다(이미 들어간 값은 그대로).
    """
    form = await request.form()
    ws = (form.get("week_start") or "").strip()
    monday = _parse_date(ws)
    if monday is None or monday.weekday() != 0:
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    ws = monday.isoformat()
    raw = (form.get("template_id") or "").strip()
    now = datetime.now(KST).isoformat(timespec="seconds")
    dates = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not raw:
            conn.execute("DELETE FROM tpl_applied WHERE scope = 'week' AND key = ?", (ws,))
            return JSONResponse({"ok": True, "cleared": True})
        try:
            tid = int_id(raw)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
        w = _tpl(conn, tid, "W")
        if not w:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        parts = {}
        for kind, col in (("S", "s_id"), ("T", "t_id"), ("N", "n_id"), ("C", "c_id")):
            if w[col]:
                row = _tpl(conn, w[col], kind)
                if row:
                    parts[kind] = row
        if not parts:
            return JSONResponse(
                {"ok": False, "error": "empty-template"}, status_code=400)
        # Keep previously selected components when another weekly template only
        # replaces a subset. Historic rows stored only the W choice.
        for kind in ("S", "T", "N", "C"):
            if kind not in parts and (prior := _weekly_part(conn, ws, kind)):
                _mark_applied(conn, "week", ws, kind, prior["id"], now)
        out = _apply_choices(conn, parts, dates, ws, now)
        for kind, part in parts.items():
            _mark_applied(conn, "week", ws, kind, part["id"], now)
        _mark_applied(conn, "week", ws, "W", tid, now)
    out["ok"] = True
    return JSONResponse(out)


@router.post("/day/apply-template")
async def day_apply_template(request: Request):
    """세션시간·고정할일·블록이름·구분 템플릿 하나를 그 날 하루에만 적용한다.

    주간 템플릿을 건 뒤에도 어느 하루만 다르게 갈 때 쓴다(주간 탭 날짜 머리의 ⋯).
    template_id 가 비면 그 날 걸어 둔 것을 뗀다. 세션시간이면 그 날 시간표도 지워
    다시 그 주·설정값을 따르게 한다.
    """
    form = await request.form()
    ds = (form.get("date") or "").strip()
    d = _parse_date(ds)
    if d is None:
        return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
    ds = d.isoformat()
    kind = (form.get("kind") or "").strip().upper()
    if kind not in ("S", "T", "N", "C"):
        return JSONResponse({"ok": False, "error": "bad-kind"}, status_code=400)
    raw = (form.get("template_id") or "").strip()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not raw:
            out = _template_response()
            active = _applied_template(conn, "day", ds, kind)
            ws = week_start(d).isoformat()
            if kind == "S":
                out = _apply_choices(conn, {}, [ds], None, now, detach_time=True)
                if out["skipped_days"]:
                    return JSONResponse({**out, "ok": False, "error": "schedule-in-use"}, status_code=409)
            elif active and kind in ("C", "N"):
                had_provenance = conn.execute(
                    "SELECT 1 FROM template_values WHERE date = ? AND scope = 'day' AND kind = ?",
                    (ds, kind),
                ).fetchone()
                _restore_template_fields(conn, ds, kind, "day")
                out["retained_untracked"] = not bool(had_provenance)
                if kind == "C" and (weekly := _weekly_part(conn, ws, "C")):
                    out["applied"], out["slots"], out["preserved_cells"] = _apply_cat_tpl(
                        conn, weekly, ds, "week", now,
                    )
            elif active and kind == "T":
                _clear_routine(conn, ds)
                if weekly := _weekly_part(conn, ws, "T"):
                    out["filled"], out["routine_skipped"] = _apply_rule_tpl(conn, weekly, ds, "week", now)
            _mark_applied(conn, "day", ds, kind, None, now)
            return JSONResponse({**out, "ok": True, "cleared": True})
        try:
            tid = int_id(raw)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
        row = _tpl(conn, tid, kind)
        if not row:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        conn.execute("SAVEPOINT day_template")
        out = _apply_choices(conn, {kind: row}, [ds], None, now)
        counts = {"S": out["days"], "N": out["names"], "C": out["applied"] + out["slots"],
                  "T": out["filled"]}
        out["applied_template"] = counts[kind] > 0
        if out["applied_template"]:
            _mark_applied(conn, "day", ds, kind, tid, now)
        else:
            conn.execute("ROLLBACK TO day_template")
        conn.execute("RELEASE day_template")
    out["ok"] = True
    return JSONResponse(out)


@router.post("/week/item-to-theme")
async def week_item_to_theme(request: Request):
    """이번 주 장기 항목 제목을 고른 블록(B1~B6)의 이번 주 이름으로 넣는다(장기 → 주간)."""
    form = await request.form()
    ws = (form.get("week_start") or "").strip()
    label = (form.get("label") or "").strip()
    try:
        item_id = int_id(form.get("item_id"))
        datetime.strptime(ws, "%Y-%m-%d")
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    if label not in CORE_LABELS:
        return JSONResponse({"ok": False, "error": "블록을 고르세요"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        it = conn.execute(
            "SELECT title FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not it:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        row = conn.execute(
            "SELECT theme_text FROM weekly_block_themes "
            "WHERE week_start = ? AND block_label = ?",
            (ws, label),
        ).fetchone()
        cur = ((row["theme_text"] if row else "") or "").strip()
        # 이미 이름이 있으면 덮지 않고 뒤에 붙인다(같은 내용이면 그대로 둔다).
        if cur == it["title"] or it["title"] in [p.strip() for p in cur.split("·")]:
            text = cur
        else:
            text = f"{cur} · {it['title']}" if cur else it["title"]
        conn.execute(
            "INSERT INTO weekly_block_themes (week_start, block_label, theme_text, "
            "updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(week_start, block_label) DO UPDATE SET "
            "theme_text = excluded.theme_text, updated_at = excluded.updated_at",
            (ws, label, text, now),
        )
    return JSONResponse({"ok": True, "label": label, "text": text})


@router.post("/week/decompose-themes")
async def week_decompose_themes(request: Request):
    """이번 주 계획(주간 목표 + 이 주 장기 항목)을 B1~B6 블록 테마로 나눈다. 빈 테마만 채운다."""
    form = await request.form()
    ws = (form.get("week_start") or "").strip()
    try:
        monday = datetime.strptime(ws, "%Y-%m-%d").date()
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    sunday = (monday + timedelta(days=6)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        wm = conn.execute(
            "SELECT weekly_goal FROM weekly_meta WHERE week_start = ?", (ws,)
        ).fetchone()
        goal = ((wm["weekly_goal"] if wm else "") or "").strip()
        # 장기 항목은 목표 열에 적어 둔 그 주 계획이 있으면 그것까지 함께 넘긴다.
        wk_plans = [
            f"{r['title']} · {r['goal_text']}" if (r["goal_text"] or "").strip()
            else r["title"]
            for r in conn.execute(
                "SELECT i.title, g.goal_text FROM lt_item i "
                "JOIN lt_area a ON a.id = i.area_id "
                "LEFT JOIN weekly_lt_goal g ON g.item_id = i.id AND g.week_start = ? "
                "WHERE i.start_date <= ? AND i.end_date >= ? AND a.is_active = 1 "
                "ORDER BY a.display_order, i.start_date, i.id",
                (ws, sunday, ws),
            )
        ]
        context = "\n".join([goal] + wk_plans).strip()
        if not context:
            return JSONResponse(
                {"ok": False, "error": "주간 목표나 이 주 장기 항목을 먼저 적어 주세요"},
                status_code=400,
            )
        existing = {
            r["block_label"]: (r["theme_text"] or "")
            for r in conn.execute(
                "SELECT block_label, theme_text FROM weekly_block_themes WHERE week_start=?",
                (ws,),
            )
        }
        contents = await _off_loop(_ai_split, context, CORE_LABELS, "", "주") if ai.enabled() else None
        used_ai = contents is not None
        if contents is None:
            contents = _rule_distribute(context, len(CORE_LABELS))
        filled = 0
        for label, gen in zip(CORE_LABELS, contents):
            if existing.get(label, "").strip():
                continue
            gen = (gen or "").strip()
            if not gen:
                continue
            conn.execute(
                "INSERT INTO weekly_block_themes (week_start, block_label, theme_text, "
                "updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(week_start, block_label) DO UPDATE SET "
                "theme_text = excluded.theme_text, updated_at = excluded.updated_at",
                (ws, label, gen, now),
            )
            filled += 1
    return JSONResponse({"ok": True, "filled": filled, "used_ai": used_ai})
