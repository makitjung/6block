# 장기플랜(연·분기·월·주 계획 막대)과 영역·항목 관리를 담당하는 라우터
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.common import KST, _parse_date, templates
from app.db import get_conn

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
    """상위 사슬을 하위에 맞춘다. 기간은 하위를 모두 품도록 넓히고, 진척률은 하위 평균을 따른다.

    직접 정한 상위 기간은 줄이지 않는다(연 계획 안에 3개월짜리 하위 하나만 있어도
    연 계획은 그대로 남는다). 날짜는 'YYYY-MM-DD' 라 문자열 비교가 곧 날짜 비교다.
    """
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
        prow = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (pid,)
        ).fetchone()
        if agg and agg["n"] and prow:
            conn.execute(
                "UPDATE lt_item SET start_date = ?, end_date = ?, progress = ?, "
                "updated_at = ? WHERE id = ?",
                (min(prow["start_date"], agg["s"]), max(prow["end_date"], agg["e"]),
                 round(agg["p"] or 0), now, pid),
            )
        cur = pid


def _lt_cover_children(conn, item_id: int) -> bool:
    """이 항목을 자기 하위 전체를 품도록 넓힌다. 넓혔으면 True(기간을 직접 고칠 때 쓴다)."""
    agg = conn.execute(
        "SELECT MIN(start_date) AS s, MAX(end_date) AS e, COUNT(*) AS n "
        "FROM lt_item WHERE parent_id = ?",
        (item_id,),
    ).fetchone()
    if not agg or not agg["n"]:
        return False
    row = conn.execute(
        "SELECT start_date, end_date FROM lt_item WHERE id = ?", (item_id,)
    ).fetchone()
    s, e = min(row["start_date"], agg["s"]), max(row["end_date"], agg["e"])
    if s == row["start_date"] and e == row["end_date"]:
        return False
    conn.execute(
        "UPDATE lt_item SET start_date = ?, end_date = ?, updated_at = ? WHERE id = ?",
        (s, e, datetime.now(KST).isoformat(timespec="seconds"), item_id),
    )
    return True


MAX_LANE = 2      # 겹쳐 그릴 하위 단계(0=상위, 1·2=하위). 더 깊은 항목은 2단계로 눌러 그린다.

# 막대 길이로 나누는 기간 구분. 짧은 쪽을 먼저 본다(1주 이하는 '단기'가 아니라 '초단기').
SPAN_CLASSES = [(7, "xs", "초단기"), (31, "s", "단기"), (183, "m", "중기")]
SPAN_LONG = ("l", "장기")


def _span_class(s: date, e: date) -> tuple[str, str]:
    """기간 길이(일)로 (분류 키, 이름). 7일 이하 초단기 · 31일 이하 단기 · 183일 이하 중기 · 그 위 장기."""
    days = (e - s).days + 1
    for limit, key, label in SPAN_CLASSES:
        if days <= limit:
            return key, label
    return SPAN_LONG


def _lt_descendants(conn, item_id: int) -> list[int]:
    """그 항목 아래의 모든 하위 항목 id(깊이 무관)."""
    out: list[int] = []
    stack = [item_id]
    while stack:
        cur = stack.pop()
        for r in conn.execute("SELECT id FROM lt_item WHERE parent_id = ?", (cur,)):
            out.append(r["id"])
            stack.append(r["id"])
    return out


def _lt_rollup_parent(conn, parent_id: int | None):
    """상위를 잃거나 얻은 쪽의 사슬을 남은 자식 기준으로 다시 계산한다."""
    if not parent_id:
        return
    sib = conn.execute(
        "SELECT id FROM lt_item WHERE parent_id = ? LIMIT 1", (parent_id,)
    ).fetchone()
    if sib:
        _lt_rollup(conn, sib["id"])


def _add_months(d: date, n: int) -> date:
    """달을 n개 더한 날짜. 그 달에 없는 날(1/31 + 1개월)은 말일로 맞춘다."""
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, _month_last(y, m).day))


# 열 한 칸을 옆으로 옮길 때 더할 개월 수(주 단위는 7일로 따로 계산한다).
SHIFT_MONTHS = {"year": 12, "quarter": 3, "month": 1}


def _gantt_areas(conn, areas, span_start: date, span_end: date) -> list[dict]:
    """영역별 간트 행 목록. 최상위 항목 하나가 한 줄이고, 하위 항목은 그 줄 막대 안에 겹친다.

    한 줄은 bars(상위+하위 전부의 막대)와 edits(같은 항목들의 편집 폼)를 함께 담는다.
    left/width 는 보이는 기간 전체에 대한 퍼센트, depth 는 겹칠 단계라 템플릿이 계산 없이 그린다.
    """
    total = (span_end - span_start).days + 1
    today = datetime.now(KST).date()
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

    def bar(it, depth: int) -> dict:
        s = _parse_date(it["start_date"]) or span_start
        e = _parse_date(it["end_date"]) or s
        vs, ve = max(s, span_start), min(e, span_end)
        visible = vs <= ve
        row = dict(it)
        row["depth"] = min(depth, MAX_LANE)
        row["visible"] = visible
        row["left"] = round((vs - span_start).days / total * 100, 3) if visible else 0
        row["width"] = round(((ve - vs).days + 1) / total * 100, 3) if visible else 0
        row["clip_left"] = s < span_start
        row["clip_right"] = e > span_end
        row["range_label"] = f"{s.month}/{s.day}~{e.month}/{e.day}"
        row["has_children"] = bool(children.get(it["id"]))
        row["span_class"], row["span_label"] = _span_class(s, e)
        row["days"] = (e - s).days + 1
        row["past"] = e < today        # 종료일이 지난 항목은 화면에서 기본으로 접는다
        return row

    def walk(it, depth: int, bars: list):
        bars.append(bar(it, depth))
        for c in children.get(it["id"], []):
            if overlaps(c):
                walk(c, depth + 1, bars)

    for it in children.get(None, []):
        if it["area_id"] in rows_by_area and overlaps(it):
            bars: list[dict] = []
            walk(it, 0, bars)
            root = bars[0]
            rows_by_area[it["area_id"]].append({
                "id": root["id"],
                "title": root["title"],
                "range_label": root["range_label"],
                "progress": root["progress"],
                "span_class": root["span_class"],
                "span_label": root["span_label"],
                "past": root["past"],
                "lanes": max(b["depth"] for b in bars),
                "bars": [b for b in bars if b["visible"]],
                "edits": bars,
            })
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
        # 하위가 있는 항목의 기간도 직접 고칠 수 있다. 다만 하위를 밖으로 밀어낼 수는 없어
        # 하위를 모두 품도록 되돌린다.
        widened = _lt_cover_children(conn, item_id)
        _lt_rollup(conn, item_id)
    return JSONResponse({"ok": True, "widened": widened})


@router.post("/plan/item/shift")
async def plan_item_shift(request: Request):
    """계획 막대를 보고 있는 열 단위로 좌우로 옮긴다(기간 길이는 그대로).

    하위가 있는 항목의 기간은 하위에서 자동 계산되므로 옮기지 않는다.
    """
    form = await request.form()
    try:
        item_id = int(form.get("id"))
        steps = int(form.get("steps"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    level = (form.get("level") or "").strip()
    if level not in PLAN_LEVELS:
        return JSONResponse({"ok": False, "error": "bad-level"}, status_code=400)
    if steps == 0:
        return JSONResponse({"ok": True, "moved": 0})
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        if conn.execute(
            "SELECT 1 FROM lt_item WHERE parent_id = ? LIMIT 1", (item_id,)
        ).fetchone():
            return JSONResponse(
                {"ok": False, "error": "하위가 있는 항목의 기간은 하위에서 자동 계산됩니다"},
                status_code=400,
            )
        s, e = _parse_date(row["start_date"]), _parse_date(row["end_date"])
        if not s or not e:
            return JSONResponse({"ok": False, "error": "기간 없음"}, status_code=400)
        span = e - s
        if level == "week":
            s2 = s + timedelta(weeks=steps)
        else:
            s2 = _add_months(s, SHIFT_MONTHS[level] * steps)
        e2 = s2 + span                     # 길이를 그대로 유지한다
        conn.execute(
            "UPDATE lt_item SET start_date = ?, end_date = ?, updated_at = ? WHERE id = ?",
            (s2.isoformat(), e2.isoformat(), now, item_id),
        )
        _lt_rollup(conn, item_id)
    return JSONResponse({"ok": True, "start": s2.isoformat(), "end": e2.isoformat()})


@router.post("/plan/item/resize")
async def plan_item_resize(request: Request):
    """막대의 한쪽 끝(edge=start|end)만 열 단위로 늘리거나 줄인다.

    하위가 있는 항목도 줄일 수 있지만 하위를 모두 품는 선까지만 줄어든다.
    """
    form = await request.form()
    try:
        item_id = int(form.get("id"))
        steps = int(form.get("steps"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    edge = (form.get("edge") or "").strip()
    level = (form.get("level") or "").strip()
    if edge not in ("start", "end") or level not in PLAN_LEVELS:
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    if steps == 0:
        return JSONResponse({"ok": True, "moved": 0})
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        s, e = _parse_date(row["start_date"]), _parse_date(row["end_date"])
        if not s or not e:
            return JSONResponse({"ok": False, "error": "기간 없음"}, status_code=400)
        moved = (s if edge == "start" else e)
        if level == "week":
            moved = moved + timedelta(weeks=steps)
        else:
            moved = _add_months(moved, SHIFT_MONTHS[level] * steps)
        if edge == "start":
            s = moved
        else:
            e = moved
        if e < s:
            return JSONResponse({"ok": False, "error": "기간이 뒤집힙니다"}, status_code=400)
        conn.execute(
            "UPDATE lt_item SET start_date = ?, end_date = ?, updated_at = ? WHERE id = ?",
            (s.isoformat(), e.isoformat(), now, item_id),
        )
        widened = _lt_cover_children(conn, item_id)
        _lt_rollup(conn, item_id)
    return JSONResponse({"ok": True, "widened": widened})


@router.post("/plan/item/reparent")
async def plan_item_reparent(request: Request):
    """막대를 다른 막대의 하위로 넣거나(parent_id), 영역에 놓아 최상위로 뺀다(area_id).

    하위로 들어가면 상위의 기간·진척률이 자기 하위들로 다시 계산돼 상위 막대 안에 겹쳐 보인다.
    """
    form = await request.form()
    try:
        item_id = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    raw_parent = (form.get("parent_id") or "").strip()
    raw_area = (form.get("area_id") or "").strip()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT parent_id, area_id FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        old_parent = row["parent_id"]
        kin = _lt_descendants(conn, item_id)
        if raw_parent:
            try:
                pid = int(raw_parent)
            except ValueError:
                return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
            if pid == item_id or pid in kin:
                return JSONResponse(
                    {"ok": False, "error": "자기 자신이나 자기 하위로는 넣을 수 없습니다"},
                    status_code=400,
                )
            prow = conn.execute(
                "SELECT area_id FROM lt_item WHERE id = ?", (pid,)
            ).fetchone()
            if not prow:
                return JSONResponse({"ok": False, "error": "상위 항목 없음"},
                                    status_code=404)
            new_parent, new_area = pid, prow["area_id"]
        else:
            try:
                new_area = int(raw_area)
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
            if not conn.execute(
                "SELECT 1 FROM lt_area WHERE id = ?", (new_area,)
            ).fetchone():
                return JSONResponse({"ok": False, "error": "영역 없음"}, status_code=404)
            new_parent = None
        if new_parent == old_parent and new_area == row["area_id"]:
            return JSONResponse({"ok": True, "changed": False})
        conn.execute(
            "UPDATE lt_item SET parent_id = ?, area_id = ?, updated_at = ? WHERE id = ?",
            (new_parent, new_area, now, item_id),
        )
        if kin:  # 하위 사슬도 같은 영역으로 함께 옮긴다(영역은 행이라 섞이면 안 된다)
            ph = ",".join("?" * len(kin))
            conn.execute(
                f"UPDATE lt_item SET area_id = ?, updated_at = ? WHERE id IN ({ph})",
                (new_area, now, *kin),
            )
        _lt_rollup_parent(conn, old_parent)   # 하나 빠진 옛 상위
        _lt_rollup(conn, item_id)             # 하나 들어온 새 상위
    return JSONResponse({"ok": True, "changed": True})


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
def plan_view(request: Request, level: str = "month", anchor: str = ""):
    # 기본은 월 단위(오늘이 든 분기의 3개월). 연·분기는 축소로, 주는 확대로 간다.
    if level not in PLAN_LEVELS:
        level = "month"
    a = _parse_anchor(anchor)
    cols, header = _plan_columns(level, a)
    span_start, span_end = cols[0]["start"], cols[-1]["end"]
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
        gantt = _gantt_areas(conn, areas, span_start, span_end)
    # 기본으로 접히는 지난 항목 수(0이면 '지난 항목 보기' 버튼도 내보내지 않는다)
    past_count = sum(
        1 for ar in gantt for r in ar["rows"] for b in r["bars"] if b["past"]
    )
    prev_anchor, next_anchor = _plan_nav(level, a)
    order = list(PLAN_LEVELS)
    i = order.index(level)
    # 항목 추가 기본 시작일. 오늘이 보이는 기간 안이면 오늘, 아니면 그 기간 첫날.
    today = datetime.now(KST).date()
    default_start = today if span_start <= today <= span_end else span_start
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
            "breadcrumb": _plan_breadcrumb(level, a),
            "prev_anchor": prev_anchor,
            "next_anchor": next_anchor,
            "zoom_in": order[i + 1] if i + 1 < len(order) else None,
            "zoom_out": order[i - 1] if i - 1 >= 0 else None,
            "levels": PLAN_LEVELS,
            "level_labels": PLAN_LEVEL_LABELS,
            "gantt": gantt,
            "past_count": past_count,
            "span_start": span_start.strftime("%Y-%m-%d"),
            "span_end": span_end.strftime("%Y-%m-%d"),
            "default_start": default_start.strftime("%Y-%m-%d"),
        },
    )


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
