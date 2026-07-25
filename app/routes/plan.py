# 장기플랜(연·분기·월·주 표 + 간트)과 영역·항목 관리를 담당하는 라우터
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.common import (
    KST,
    _ai_split,
    _child_anchor,
    _child_periods,
    _off_loop,
    _parse_date,
    _rule_distribute,
    templates,
)
from app.db import get_conn
from app.integrations import ai

router = APIRouter()


# -- 장기플랜 기간 계산 ------------------------------------------------------
PLAN_LEVELS = ("year", "quarter", "month", "week")
PLAN_LEVEL_LABELS = {"year": "연", "quarter": "분기", "month": "월", "week": "주"}


def _parse_anchor(anchor: str) -> date:
    """anchor 쿼리(YYYY-MM-DD)를 date로. 비었거나 잘못되면 오늘(KST)."""
    try:
        return datetime.strptime(anchor, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return datetime.now(KST).date()


def _month_last(y: int, m: int) -> date:
    """그 달의 마지막 날."""
    return (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)


def _plan_columns(level: str, anchor: date):
    """(열 목록, 헤더 라벨). 열은 key·label·sub·current·week_link·drill_*·start·end 를 가진다.

    drill_level/drill_anchor: 그 열 머리글을 누르면 들어갈 다음(더 잘은) 단위와 anchor.
    start/end: 그 열이 덮는 실제 날짜 구간(date). 간트 막대 위치 계산에 쓴다.
    """
    today = datetime.now(KST).date()
    cols: list[dict] = []
    if level == "year":
        y0 = anchor.year
        for y in range(y0, y0 + 6):
            cols.append({"key": str(y), "label": str(y), "sub": "",
                         "current": y == today.year, "week_link": None,
                         "drill_level": "quarter", "drill_anchor": f"{y}-01-01",
                         "start": date(y, 1, 1), "end": date(y, 12, 31)})
        header = f"{y0}–{y0 + 5}"
    elif level == "quarter":
        y = anchor.year
        for q in range(1, 5):
            m0 = (q - 1) * 3 + 1
            cols.append({"key": f"{y}-Q{q}", "label": f"{q}분기",
                         "sub": f"{m0}~{q * 3}월",
                         "current": y == today.year and (today.month - 1) // 3 + 1 == q,
                         "week_link": None,
                         "drill_level": "month",
                         "drill_anchor": f"{y}-{m0:02d}-01",
                         "start": date(y, m0, 1), "end": _month_last(y, q * 3)})
        header = f"{y}년"
    elif level == "month":
        # 기본은 anchor가 속한 분기의 3개월만 포커싱해 보여준다(← → 로 분기 단위 이동).
        y = anchor.year
        q = (anchor.month - 1) // 3 + 1
        m0 = (q - 1) * 3 + 1
        for m in range(m0, m0 + 3):
            cols.append({"key": f"{y}-{m:02d}", "label": f"{m}월", "sub": "",
                         "current": y == today.year and m == today.month,
                         "week_link": None,
                         "drill_level": "week", "drill_anchor": f"{y}-{m:02d}-01",
                         "start": date(y, m, 1), "end": _month_last(y, m)})
        header = f"{y}년 {q}분기 ({m0}~{m0 + 2}월)"
    else:  # week
        y, m = anchor.year, anchor.month
        first = date(y, m, 1)
        last = _month_last(y, m)
        monday = first - timedelta(days=first.weekday())
        cur_monday = today - timedelta(days=today.weekday())
        while monday <= last:
            key = monday.strftime("%Y-%m-%d")
            end = monday + timedelta(days=6)
            cols.append({"key": key, "label": f"{monday.month}/{monday.day}",
                         "sub": f"~{end.month}/{end.day}",
                         "current": monday == cur_monday, "week_link": key,
                         "drill_level": None, "drill_anchor": None,
                         "start": monday, "end": end})
            monday += timedelta(days=7)
        header = f"{y}년 {m}월"
    return cols, header


def _plan_nav(level: str, anchor: date):
    """현재 단위에서 이전/다음 기간으로 이동할 anchor(YYYY-MM-DD 문자열) 쌍."""
    if level == "year":
        return f"{anchor.year - 6:04d}-01-01", f"{anchor.year + 6:04d}-01-01"
    if level == "quarter":
        return f"{anchor.year - 1:04d}-01-01", f"{anchor.year + 1:04d}-01-01"
    if level == "month":
        # 월 뷰는 분기(3개월) 단위로 앞뒤 이동한다.
        q = (anchor.month - 1) // 3 + 1
        m0 = (q - 1) * 3 + 1
        prev_q = date(anchor.year - 1, 10, 1) if m0 == 1 else date(anchor.year, m0 - 3, 1)
        next_q = date(anchor.year + 1, 1, 1) if m0 == 10 else date(anchor.year, m0 + 3, 1)
        return prev_q.strftime("%Y-%m-%d"), next_q.strftime("%Y-%m-%d")
    y, m = anchor.year, anchor.month
    prev_last = date(y, m, 1) - timedelta(days=1)          # 지난달 말일
    next_first = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return prev_last.strftime("%Y-%m-01"), next_first.strftime("%Y-%m-%d")


def _plan_ancestors(level: str, anchor: date):
    """현재 anchor가 속한 상위 단위들의 (level, label, key). 현재보다 굵은 단위만."""
    q = (anchor.month - 1) // 3 + 1
    coarser = [
        ("year", str(anchor.year), str(anchor.year)),
        ("quarter", f"{q}분기", f"{anchor.year}-Q{q}"),
        ("month", f"{anchor.month}월", f"{anchor.year}-{anchor.month:02d}"),
    ]
    idx = PLAN_LEVELS.index(level)
    return [
        {"level": lv, "label": label, "key": key}
        for lv, label, key in coarser
        if PLAN_LEVELS.index(lv) < idx
    ]


def _plan_breadcrumb(level: str, anchor: date):
    """연>분기>월>주 경로. 각 단위는 anchor가 속한 기간 라벨 + 그 단위로 가는 링크."""
    q = (anchor.month - 1) // 3 + 1
    monday = anchor - timedelta(days=anchor.weekday())
    labels = {
        "year": str(anchor.year),
        "quarter": f"{q}분기",
        "month": f"{anchor.month}월",
        "week": f"{monday.month}/{monday.day} 주",
    }
    a = anchor.strftime("%Y-%m-%d")
    idx = PLAN_LEVELS.index(level)
    return [
        {"level": lv, "label": labels[lv], "anchor": a, "current": lv == level}
        for i, lv in enumerate(PLAN_LEVELS)
        if i <= idx
    ]


# -- 장기플랜 간트 ----------------------------------------------------------
# lt_item 한 줄이 간트 막대 하나다. parent_id 로 연→분기→월→주 항목을 잇고,
# 하위가 있는 항목은 기간(하위 최소~최대)과 진척률(하위 평균)을 하위에서 자동으로 따라간다.


def _parse_date(s) -> date | None:
    """'YYYY-MM-DD' 를 date 로. 형식이 틀리면 None."""
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _lt_rollup(conn, item_id: int | None):
    """항목의 상위 사슬을 하위 값으로 갱신한다(기간=하위 최소~최대, 진척률=하위 평균)."""
    now = datetime.now(KST).isoformat(timespec="seconds")
    seen: set[int] = set()
    cur = item_id
    while cur:
        row = conn.execute(
            "SELECT parent_id FROM lt_item WHERE id = ?", (cur,)
        ).fetchone()
        pid = row["parent_id"] if row else None
        if not pid or pid in seen:      # 상위가 없거나 순환이면 멈춘다
            return
        seen.add(pid)
        agg = conn.execute(
            "SELECT MIN(start_date) AS s, MAX(end_date) AS e, "
            "       AVG(progress) AS p, COUNT(*) AS n "
            "FROM lt_item WHERE parent_id = ?",
            (pid,),
        ).fetchone()
        if agg and agg["n"]:
            conn.execute(
                "UPDATE lt_item SET start_date = ?, end_date = ?, progress = ?, "
                "updated_at = ? WHERE id = ?",
                (agg["s"], agg["e"], round(agg["p"] or 0), now, pid),
            )
        cur = pid


def _gantt_areas(conn, areas, span_start: date, span_end: date) -> list[dict]:
    """영역별 간트 행 목록. 보이는 기간과 겹치는 항목만 상위→하위 순으로 편다.

    각 행에 left/width(퍼센트)와 잘림 여부를 담아 템플릿이 계산 없이 그리게 한다.
    """
    total = (span_end - span_start).days + 1
    rows_by_area: dict[int, list] = {a["id"]: [] for a in areas}
    children: dict[int | None, list] = {}
    for r in conn.execute(
        "SELECT id, area_id, parent_id, title, start_date, end_date, progress "
        "FROM lt_item ORDER BY start_date, id"
    ):
        if r["area_id"] in rows_by_area:
            children.setdefault(r["parent_id"], []).append(dict(r))

    def overlaps(it) -> bool:
        s, e = _parse_date(it["start_date"]), _parse_date(it["end_date"])
        if not s or not e:
            return False
        if s <= span_end and e >= span_start:
            return True
        return any(overlaps(c) for c in children.get(it["id"], []))

    def walk(it, depth: int):
        s = _parse_date(it["start_date"]) or span_start
        e = _parse_date(it["end_date"]) or s
        vs, ve = max(s, span_start), min(e, span_end)
        visible = vs <= ve
        row = dict(it)
        row["depth"] = depth
        row["visible"] = visible
        row["left"] = round((vs - span_start).days / total * 100, 3) if visible else 0
        row["width"] = round(((ve - vs).days + 1) / total * 100, 3) if visible else 0
        row["clip_left"] = s < span_start
        row["clip_right"] = e > span_end
        row["range_label"] = f"{s.month}/{s.day}~{e.month}/{e.day}"
        row["has_children"] = bool(children.get(it["id"]))
        rows_by_area[it["area_id"]].append(row)
        for c in children.get(it["id"], []):
            if overlaps(c):
                walk(c, depth + 1)

    for it in children.get(None, []):
        if it["area_id"] in rows_by_area and overlaps(it):
            walk(it, 0)
    # 키 이름은 'items'를 피한다(Jinja에서 dict.items 메서드와 겹친다).
    return [
        {"id": a["id"], "name": a["name"], "rows": rows_by_area[a["id"]]}
        for a in areas
    ]


@router.post("/plan/item/add")
async def plan_item_add(request: Request):
    """간트 항목을 만든다. parent_id 를 주면 그 항목의 하위로 붙고 영역을 물려받는다."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    start = _parse_date(form.get("start"))
    end = _parse_date(form.get("end")) or start
    raw_parent = (form.get("parent_id") or "").strip()
    try:
        area_id = int(form.get("area_id"))
    except (TypeError, ValueError):
        area_id = 0
    if not title:
        return JSONResponse({"ok": False, "error": "제목을 입력하세요"}, status_code=400)
    if not start or not end:
        return JSONResponse({"ok": False, "error": "시작·종료 날짜가 필요합니다"},
                            status_code=400)
    if end < start:
        return JSONResponse({"ok": False, "error": "종료일이 시작일보다 빠릅니다"},
                            status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        parent_id = None
        if raw_parent:
            try:
                pid = int(raw_parent)
            except ValueError:
                return JSONResponse({"ok": False, "error": "상위 항목 값이 잘못됨"},
                                    status_code=400)
            prow = conn.execute(
                "SELECT id, area_id FROM lt_item WHERE id = ?", (pid,)
            ).fetchone()
            if not prow:
                return JSONResponse({"ok": False, "error": "상위 항목 없음"},
                                    status_code=404)
            parent_id, area_id = prow["id"], prow["area_id"]
        if not conn.execute(
            "SELECT 1 FROM lt_area WHERE id = ?", (area_id,)
        ).fetchone():
            return JSONResponse({"ok": False, "error": "영역 없음"}, status_code=404)
        cur = conn.execute(
            "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, "
            "progress, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (area_id, parent_id, title, start.isoformat(), end.isoformat(), now),
        )
        new_id = cur.lastrowid
        _lt_rollup(conn, new_id)
    return JSONResponse({"ok": True, "id": new_id})


@router.post("/plan/item/update")
async def plan_item_update(request: Request):
    """간트 항목의 제목·기간·진척률을 고친다(보낸 값만 바꾼다)."""
    form = await request.form()
    try:
        item_id = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    fields: dict = {}
    if (form.get("title") or "").strip():
        fields["title"] = form.get("title").strip()
    if form.get("start") is not None and (form.get("start") or "").strip():
        d = _parse_date(form.get("start"))
        if not d:
            return JSONResponse({"ok": False, "error": "시작일 형식"}, status_code=400)
        fields["start_date"] = d.isoformat()
    if form.get("end") is not None and (form.get("end") or "").strip():
        d = _parse_date(form.get("end"))
        if not d:
            return JSONResponse({"ok": False, "error": "종료일 형식"}, status_code=400)
        fields["end_date"] = d.isoformat()
    if form.get("progress") is not None and (form.get("progress") or "").strip():
        try:
            fields["progress"] = max(0, min(100, int(form.get("progress"))))
        except ValueError:
            return JSONResponse({"ok": False, "error": "진척률 형식"}, status_code=400)
    if not fields:
        return JSONResponse({"ok": False, "error": "바꿀 값 없음"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        s = fields.get("start_date", row["start_date"])
        e = fields.get("end_date", row["end_date"])
        if e < s:
            return JSONResponse({"ok": False, "error": "종료일이 시작일보다 빠릅니다"},
                                status_code=400)
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE lt_item SET {sets}, updated_at = ? WHERE id = ?",
            (*fields.values(), now, item_id),
        )
        _lt_rollup(conn, item_id)
    return JSONResponse({"ok": True})


@router.post("/plan/item/delete")
async def plan_item_delete(request: Request):
    """간트 항목을 지운다. 하위 항목도 함께 지워지고 상위 기간은 다시 계산된다."""
    form = await request.form()
    try:
        item_id = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT parent_id FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": True})
        conn.execute("DELETE FROM lt_item WHERE id = ?", (item_id,))
        if row["parent_id"]:
            # 지워진 항목의 형제를 기준으로 상위 사슬을 다시 계산한다.
            sib = conn.execute(
                "SELECT id FROM lt_item WHERE parent_id = ? LIMIT 1", (row["parent_id"],)
            ).fetchone()
            if sib:
                _lt_rollup(conn, sib["id"])
    return JSONResponse({"ok": True})


# -- 장기플랜 ---------------------------------------------------------------


@router.get("/plan")
def plan_view(request: Request, level: str = "year", anchor: str = "",
              view: str = "table"):
    if level not in PLAN_LEVELS:
        level = "year"
    view = view if view in ("table", "gantt") else "table"
    a = _parse_anchor(anchor)
    cols, header = _plan_columns(level, a)
    keys = [c["key"] for c in cols]
    span_start, span_end = cols[0]["start"], cols[-1]["end"]
    ancestors = _plan_ancestors(level, a)
    anc_keys = [x["key"] for x in ancestors]
    with get_conn() as conn:
        areas = [
            dict(x)
            for x in conn.execute(
                "SELECT id, name FROM lt_area WHERE is_active = 1 ORDER BY display_order"
            )
        ]
        all_areas = conn.execute(
            "SELECT id, name, is_active FROM lt_area "
            "ORDER BY is_active DESC, display_order"
        ).fetchall()
        grid: dict[int, dict[str, str]] = {}
        if keys:
            ph = ",".join("?" * len(keys))
            for r in conn.execute(
                f"SELECT area_id, period_key, content FROM lt_plan "
                f"WHERE level = ? AND period_key IN ({ph})",
                (level, *keys),
            ):
                grid.setdefault(r["area_id"], {})[r["period_key"]] = r["content"]
        # 상위 맥락: 조상 단위(연·분기·월)의 영역별 계획을 모은다.
        anc_map: dict[tuple, str] = {}
        if anc_keys:
            aph = ",".join("?" * len(anc_keys))
            for r in conn.execute(
                f"SELECT area_id, period_key, content FROM lt_plan "
                f"WHERE period_key IN ({aph})",
                anc_keys,
            ):
                anc_map[(r["area_id"], r["period_key"])] = r["content"]
        gantt = _gantt_areas(conn, areas, span_start, span_end) if view == "gantt" else []
    parent_ctx = []
    for ar in areas:
        rows = [
            {"label": anc["label"], "content": anc_map[(ar["id"], anc["key"])]}
            for anc in ancestors
            if anc_map.get((ar["id"], anc["key"]))
        ]
        if rows:
            parent_ctx.append({"name": ar["name"], "rows": rows})
    prev_anchor, next_anchor = _plan_nav(level, a)
    order = list(PLAN_LEVELS)
    i = order.index(level)
    return templates.TemplateResponse(
        "plan.html",
        {
            "request": request,
            "level": level,
            "level_label": PLAN_LEVEL_LABELS[level],
            "anchor": a.strftime("%Y-%m-%d"),
            "columns": cols,
            "header": header,
            "areas": areas,
            "all_areas": [dict(x) for x in all_areas],
            "grid": grid,
            "breadcrumb": _plan_breadcrumb(level, a),
            "parent_ctx": parent_ctx,
            "prev_anchor": prev_anchor,
            "next_anchor": next_anchor,
            "zoom_in": order[i + 1] if i + 1 < len(order) else None,
            "zoom_out": order[i - 1] if i - 1 >= 0 else None,
            "levels": PLAN_LEVELS,
            "level_labels": PLAN_LEVEL_LABELS,
            "view": view,
            "gantt": gantt,
            "span_start": span_start.strftime("%Y-%m-%d"),
            "span_end": span_end.strftime("%Y-%m-%d"),
        },
    )


@router.post("/plan/cell/save")
async def plan_cell_save(request: Request):
    """장기플랜 칸 한 개를 자동저장. 내용이 비면 행을 지워 깔끔하게 유지한다."""
    form = await request.form()
    level = (form.get("level") or "").strip()
    period_key = (form.get("period_key") or "").strip()
    try:
        area_id = int(form.get("area_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    if level not in PLAN_LEVELS or not period_key:
        return JSONResponse({"ok": False}, status_code=400)
    content = (form.get("content") or "").strip()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        if content:
            conn.execute(
                "INSERT INTO lt_plan (level, period_key, area_id, content, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(level, period_key, area_id) DO UPDATE SET "
                "content = excluded.content, updated_at = excluded.updated_at",
                (level, period_key, area_id, content, now),
            )
        else:
            conn.execute(
                "DELETE FROM lt_plan WHERE level = ? AND period_key = ? AND area_id = ?",
                (level, period_key, area_id),
            )
    return JSONResponse({"ok": True})


@router.post("/plan/decompose")
async def plan_decompose(request: Request):
    """장기 칸(연/분기/월)을 바로 아래 단위로 세분화한다. 빈 자식 칸만 채운다."""
    form = await request.form()
    level = (form.get("level") or "").strip()
    period_key = (form.get("period_key") or "").strip()
    try:
        area_id = int(form.get("area_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    if level not in ("year", "quarter", "month"):
        return JSONResponse(
            {"ok": False, "error": "이 단위는 세분화할 수 없습니다"}, status_code=400
        )
    child_level, children = _child_periods(level, period_key)
    if not children:
        return JSONResponse({"ok": False, "error": "no-children"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        prow = conn.execute(
            "SELECT content FROM lt_plan WHERE level=? AND period_key=? AND area_id=?",
            (level, period_key, area_id),
        ).fetchone()
        parent_text = ((prow["content"] if prow else "") or "").strip()
        if not parent_text:
            return JSONResponse(
                {"ok": False, "error": "상위 계획을 먼저 적어 주세요"}, status_code=400
            )
        arow = conn.execute(
            "SELECT name FROM lt_area WHERE id=?", (area_id,)
        ).fetchone()
        area_name = arow["name"] if arow else ""
        keys = [k for k, _ in children]
        ph = ",".join("?" * len(keys))
        existing = {
            r["period_key"]: (r["content"] or "")
            for r in conn.execute(
                f"SELECT period_key, content FROM lt_plan "
                f"WHERE level=? AND area_id=? AND period_key IN ({ph})",
                (child_level, area_id, *keys),
            )
        }
        labels = [lbl for _, lbl in children]
        contents = (
            await _off_loop(_ai_split, parent_text, labels, area_name, PLAN_LEVEL_LABELS[level])
            if ai.enabled() else None
        )
        used_ai = contents is not None
        if contents is None:
            contents = _rule_distribute(parent_text, len(children))
        filled = 0
        for (key, _lbl), gen in zip(children, contents):
            if existing.get(key, "").strip():
                continue
            gen = (gen or "").strip()
            if not gen:
                continue
            conn.execute(
                "INSERT INTO lt_plan (level, period_key, area_id, content, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(level, period_key, area_id) DO UPDATE "
                "SET content = excluded.content, updated_at = excluded.updated_at",
                (child_level, key, area_id, gen, now),
            )
            filled += 1
    return JSONResponse({
        "ok": True, "child_level": child_level,
        "child_anchor": _child_anchor(level, period_key),
        "filled": filled, "used_ai": used_ai,
    })


@router.post("/plan/area/add")
async def plan_area_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM lt_area WHERE name = ?", (name,)).fetchone()
        if row:  # 같은 이름이 있으면(숨김 포함) 다시 활성화
            conn.execute("UPDATE lt_area SET is_active = 1 WHERE id = ?", (row["id"],))
            cid = row["id"]
        else:
            order = conn.execute(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM lt_area"
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO lt_area (name, display_order, is_active) VALUES (?, ?, 1)",
                (name, order),
            )
            cid = cur.lastrowid
    return JSONResponse({"ok": True, "id": cid, "name": name})


@router.post("/plan/area/update")
async def plan_area_update(request: Request):
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE lt_area SET name = ? WHERE id = ?", (name, cid))
    return JSONResponse({"ok": True})


@router.post("/plan/area/move")
async def plan_area_move(request: Request):
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    direction = form.get("dir")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, display_order FROM lt_area WHERE is_active = 1 "
            "ORDER BY display_order"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if cid not in ids:
            return JSONResponse({"ok": False}, status_code=404)
        i = ids.index(cid)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(rows):
            a, b = rows[i], rows[j]
            conn.execute("UPDATE lt_area SET display_order = ? WHERE id = ?",
                         (b["display_order"], a["id"]))
            conn.execute("UPDATE lt_area SET display_order = ? WHERE id = ?",
                         (a["display_order"], b["id"]))
    return JSONResponse({"ok": True})


@router.post("/plan/area/delete")
async def plan_area_delete(request: Request):
    """영역을 숨김 처리(소프트 삭제)한다. 그 영역의 계획 내용은 보존된다."""
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE lt_area SET is_active = 0 WHERE id = ?", (cid,))
    return JSONResponse({"ok": True})
