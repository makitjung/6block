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
    _join3,
    _name_override,
    _off_loop,
    _parse_date,
    _rule_distribute,
    _short_date,
    _split3,
    ensure_day_skeleton,
    int_id,
    opt_id,
    templates,
    today_str,
    week_lt_items,
    week_start,
)
from app.config import WEEK_CORE_BLOCKS, hhmm_to_min, slots_for_day
from app.db import get_conn, get_day_blocks
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
        rows = conn.execute(
            f"SELECT id, date, block_label, block_order, is_core, plan_text, "
            f"       see_text, name, category_id, start_time, end_time FROM blocks "
            f"WHERE date IN ({placeholders}) ORDER BY date, block_order",
            dates,
        ).fetchall()
        categories = [
            {"id": r["id"], "name": r["name"], "tone": r["tone"]}
            for r in conn.execute(
                "SELECT id, name, tone FROM categories "
                "WHERE is_active = 1 ORDER BY display_order"
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
              AND EXISTS (SELECT 1 FROM slots s WHERE s.block_id = b.id
                          AND (s.done = 1 OR (TRIM(COALESCE(s.do_text, '')) != ''
                                              AND s.is_routine = 0)))
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
        wk_templates = conn.execute(
            "SELECT id, name FROM cat_template ORDER BY display_order, id"
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
            "week_total_hours": sum(
                len(slots_for_day(get_day_blocks(i))) for i in range(7)
            ) * 0.5,
            "wmeta": wmeta,
            # 목표 열의 자유 란 3개(장기와 무관한 그 주만의 목표)
            "weekly_goals": _split3(wmeta["weekly_goal"] if wmeta else ""),
            "themes_by_label": themes_by_label,
            "name_cells": name_cells,
            "cat_templates": [dict(t) for t in wk_templates],
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
    if _parse_date(week_start_str) is None:
        return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
    form = await request.form()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
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
        d0 = datetime.strptime(week_start_str, "%Y-%m-%d").date()
        wk_dates = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
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
            if sid is None:
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


@router.post("/week/apply-template")
async def week_apply_template(request: Request):
    """선택한 구분 템플릿을 그 주 7일에 일괄 적용한다. 블록 구분 42칸 + 고정 할일 규칙.

    빈 셀은 건너뛰어 기존 구분을 덮지 않는다. 블록 구분은 빈 슬롯에 자동 상속된다.
    고정 할일은 지난번에 넣어 둔 칸(is_routine=1)을 먼저 비우고 새 규칙대로 다시 채우므로,
    규칙을 고치고 다시 골라도 옛 문구가 남지 않는다. 사람이 쓴 칸은 어느 쪽도 건드리지 않는다.
    """
    form = await request.form()
    ws = (form.get("week_start") or "").strip()
    try:
        tid = int_id(form.get("template_id"))
        monday = datetime.strptime(ws, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        cells: dict[tuple[int, str], int] = {}
        for r in conn.execute(
            "SELECT weekday, block_label, category_id FROM cat_template_cell "
            "WHERE template_id = ?",
            (tid,),
        ):
            if r["category_id"] is not None:
                cells[(r["weekday"], r["block_label"])] = r["category_id"]
        rules = [
            dict(r)
            for r in conn.execute(
                "SELECT weekdays, start_time, span, do_text, category_id "
                "FROM routine_rule WHERE template_id = ? ORDER BY display_order, id",
                (tid,),
            )
            if (r["do_text"] or "").strip() and (r["weekdays"] or "").strip()
        ]
        if not cells and not rules:
            return JSONResponse(
                {"ok": False, "error": "empty-template"}, status_code=400
            )
        applied = 0
        filled = 0
        for i in range(7):
            d = monday + timedelta(days=i)
            ds = d.strftime("%Y-%m-%d")
            ensure_day_skeleton(conn, ds)
            for label in CORE_LABELS:
                cid = cells.get((d.weekday(), label))
                if cid is None:
                    continue
                conn.execute(
                    "UPDATE blocks SET category_id = ?, updated_at = ? "
                    "WHERE date = ? AND block_label = ? AND is_core = 1",
                    (cid, now, ds, label),
                )
                applied += 1
            if not rules:
                continue
            conn.execute(
                "UPDATE slots SET do_text = NULL, category_id = NULL, is_routine = 0, "
                "updated_at = ? WHERE date = ? AND is_routine = 1",
                (now, ds),
            )
            for rule in rules:
                if d.weekday() not in {
                    int(p) for p in rule["weekdays"].split(",") if p.strip().isdigit()
                }:
                    continue
                # 요일마다 블록 시간이 다를 수 있다. 그 시각에 칸이 없는 날은 건너뛴다.
                row = conn.execute(
                    "SELECT slot_index FROM slots WHERE date = ? AND start_time = ?",
                    (ds, rule["start_time"]),
                ).fetchone()
                if not row:
                    continue
                start_idx = row["slot_index"]
                span = min(4, max(1, rule["span"] or 1))
                cur = conn.execute(
                    "UPDATE slots SET do_text = ?, is_routine = 1, updated_at = ?, "
                    "category_id = COALESCE(?, category_id) "
                    "WHERE date = ? AND slot_index >= ? AND slot_index < ? "
                    "  AND TRIM(COALESCE(do_text, '')) = ''",
                    (
                        (rule["do_text"] or "").strip(), now, rule["category_id"],
                        ds, start_idx, start_idx + span,
                    ),
                )
                filled += cur.rowcount
    return JSONResponse({"ok": True, "applied": applied, "filled": filled})


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
