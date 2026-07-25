# 주간 화면(주간 목표·블록 테마·통계·주간 리뷰)과 그 저장을 담당하는 라우터
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.common import (
    CORE_LABELS,
    KST,
    _ai_split,
    _name_override,
    _rule_distribute,
    _short_date,
    ensure_day_skeleton,
    templates,
    today_str,
    week_start,
)
from app.config import WEEK_CORE_BLOCKS, hhmm_to_min, slots_for_day
from app.db import get_conn, get_day_blocks
from app.integrations import ai, gcal

router = APIRouter()


@router.get("/week")
def week_view(request: Request):
    return _week_view(request, week_start(datetime.now(KST).date()))


@router.get("/week/{date_str}")
def week_view_for(request: Request, date_str: str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return _week_view(request, week_start(d))


def _week_view(request: Request, monday: date):
    dates = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    placeholders = ",".join("?" * len(dates))
    week_start_str = monday.strftime("%Y-%m-%d")
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
        cat_summary = conn.execute(
            f"""
            SELECT c.name, c.tone, COUNT(s.id) AS slot_count
            FROM slots s
            JOIN blocks b ON b.id = s.block_id
            JOIN categories c ON c.id = COALESCE(s.category_id, b.category_id)
            WHERE s.date IN ({placeholders})
            GROUP BY c.id
            ORDER BY slot_count DESC
            """,
            dates,
        ).fetchall()
        plan_total = conn.execute(
            f"""
            SELECT COUNT(s.id) FROM slots s
            JOIN blocks b ON b.id = s.block_id
            WHERE b.date IN ({placeholders}) AND b.is_core = 1
              AND b.plan_text IS NOT NULL AND TRIM(b.plan_text) != ''
            """,
            dates,
        ).fetchone()[0]
        achieved = conn.execute(
            f"""
            SELECT COUNT(s.id) FROM slots s
            JOIN blocks b ON b.id = s.block_id
            WHERE b.date IN ({placeholders}) AND b.is_core = 1
              AND b.plan_text IS NOT NULL AND TRIM(b.plan_text) != ''
              AND (s.done = 1 OR (s.do_text IS NOT NULL AND TRIM(s.do_text) != ''))
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
        # 장기 계획 맥락: 이 주가 속한 연·분기·월 계획과 장기 탭의 이 주(주 단위) 계획을 함께 보여준다.
        wk_areas = conn.execute(
            "SELECT id, name FROM lt_area WHERE is_active = 1 ORDER BY display_order"
        ).fetchall()
        ctx_q = (monday.month - 1) // 3 + 1
        ctx_levels = [
            ("year", str(monday.year), "연"),
            ("quarter", f"{monday.year}-Q{ctx_q}", "분기"),
            ("month", f"{monday.year}-{monday.month:02d}", "월"),
            ("week", week_start_str, "주"),
        ]
        lt_rows = conn.execute(
            "SELECT area_id, level, content FROM lt_plan "
            "WHERE (level='year' AND period_key=?) OR (level='quarter' AND period_key=?) "
            "   OR (level='month' AND period_key=?) OR (level='week' AND period_key=?)",
            (ctx_levels[0][1], ctx_levels[1][1], ctx_levels[2][1], ctx_levels[3][1]),
        ).fetchall()
        # 간트 항목 중 이 주에 걸친 것. 연·분기 계획이 이번 주에 어디까지 닿는지 함께 본다.
        wk_items = conn.execute(
            "SELECT area_id, title, start_date, end_date, progress FROM lt_item "
            "WHERE start_date <= ? AND end_date >= ? ORDER BY start_date, id",
            (dates[6], dates[0]),
        ).fetchall()
        # 주간 리뷰(GTD 검토): 미처리 수집함 + 계획만 하고 실행 흔적 없는 코어 블록
        review_inbox = conn.execute(
            "SELECT id, text, status FROM inbox WHERE done = 0 ORDER BY id DESC"
        ).fetchall()
        missed_blocks = conn.execute(
            f"""
            SELECT b.date, b.block_label, b.block_order, b.name, b.plan_text
            FROM blocks b
            WHERE b.date IN ({placeholders}) AND b.is_core = 1
              AND b.plan_text IS NOT NULL AND TRIM(b.plan_text) != ''
              AND NOT EXISTS (
                  SELECT 1 FROM slots s WHERE s.block_id = b.id
                    AND ((s.do_text IS NOT NULL AND TRIM(s.do_text) != '') OR s.done = 1)
              )
            ORDER BY b.date, b.block_order
            """,
            dates,
        ).fetchall()
        # 주간 검토: 이번 주에 기록한 고결감(고민·결정·감사)을 한 자리에서 회고
        week_reflections = conn.execute(
            "SELECT kind, title, text, event_date FROM reflection "
            "WHERE event_date BETWEEN ? AND ? ORDER BY event_date DESC, id DESC LIMIT 50",
            (dates[0], dates[6]),
        ).fetchall()

    blocks_by_date: dict[str, list] = {d: [] for d in dates}
    for r in rows:
        blocks_by_date[r["date"]].append(r)

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
    # 장기 계획 맥락을 영역별로 묶는다(연·분기·월·주 중 내용 있는 것만).
    lt_map = {(r["area_id"], r["level"]): (r["content"] or "") for r in lt_rows}
    items_by_area: dict[int, list] = {}
    for r in wk_items:
        items_by_area.setdefault(r["area_id"], []).append({
            "title": r["title"], "progress": r["progress"],
            "range": f"{_short_date(r['start_date'])}~{_short_date(r['end_date'])}",
        })
    plan_context = []
    for ar in wk_areas:
        rows = [
            {"level": lv, "level_label": lv_label, "anchor": week_start_str,
             "content": (lt_map.get((ar["id"], lv)) or "").strip()}
            for lv, _key, lv_label in ctx_levels
            if (lt_map.get((ar["id"], lv)) or "").strip()
        ]
        gitems = items_by_area.get(ar["id"], [])
        if rows or gitems:
            plan_context.append({"name": ar["name"], "rows": rows, "gantt": gitems})
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
        "week.html",
        {
            "request": request,
            "week_start": week_start_str,
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
            "themes_by_label": themes_by_label,
            "cat_templates": [dict(t) for t in wk_templates],
            "plan_context": plan_context,
            "core_labels": CORE_LABELS,
            "week_block_events": week_block_events,
            "week_allday": week_allday,
            "cal_enabled": gcal.enabled(),
            "today": today_str(),
            "review_inbox": review_inbox,
            "missed_blocks": missed_blocks,
            "week_reflections": [dict(r) for r in week_reflections],
        },
    )


@router.post("/week/save/{week_start_str}")
async def save_week(week_start_str: str, request: Request):
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
                form.get("weekly_goal", ""),
                form.get("appointments", ""),
                form.get("vow", ""),
                form.get("memo", ""),
            ),
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
            if not suffix.isdigit():
                continue
            sid = int(suffix)
            if prefix == "bname":
                label = block_label_by_id.get(sid, "")
                override = _name_override(val, weekly_name.get(label, ""))
                conn.execute(
                    "UPDATE blocks SET name = ?, updated_at = ? WHERE id = ?",
                    (override, now, sid),
                )
            elif prefix == "bcat":
                cid = int(val) if val else None
                conn.execute(
                    "UPDATE blocks SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, sid),
                )
    return RedirectResponse(url=f"/week/{week_start_str}", status_code=303)


@router.post("/week/apply-template")
async def week_apply_template(request: Request):
    """선택한 구분 템플릿을 그 주 7일 코어 블록 구분에 요일별로 일괄 적용한다.

    빈 셀은 건너뛰어 기존 구분을 덮지 않는다. 블록 구분은 빈 슬롯에 자동 상속된다.
    """
    form = await request.form()
    ws = (form.get("week_start") or "").strip()
    try:
        tid = int(form.get("template_id"))
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
        if not cells:
            return JSONResponse(
                {"ok": False, "error": "empty-template"}, status_code=400
            )
        applied = 0
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
    return JSONResponse({"ok": True, "applied": applied})


@router.post("/week/decompose-themes")
async def week_decompose_themes(request: Request):
    """이번 주 계획(주간 목표 + 장기 주 계획)을 B1~B6 블록 테마로 나눈다. 빈 테마만 채운다."""
    form = await request.form()
    ws = (form.get("week_start") or "").strip()
    try:
        datetime.strptime(ws, "%Y-%m-%d")
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        wm = conn.execute(
            "SELECT weekly_goal FROM weekly_meta WHERE week_start = ?", (ws,)
        ).fetchone()
        goal = ((wm["weekly_goal"] if wm else "") or "").strip()
        wk_plans = [
            (r["content"] or "").strip()
            for r in conn.execute(
                "SELECT content FROM lt_plan WHERE level='week' AND period_key=?", (ws,)
            )
            if (r["content"] or "").strip()
        ]
        context = "\n".join([goal] + wk_plans).strip()
        if not context:
            return JSONResponse(
                {"ok": False, "error": "주간 목표나 이 주 계획을 먼저 적어 주세요"},
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
