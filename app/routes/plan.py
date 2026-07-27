# 장기플랜(연·분기·월·주 계획 막대)과 영역·항목 관리를 담당하는 라우터
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.common import KST, _parse_date, templates
from app.config import CORE_BLOCKS, TONE_KEYS, TONES, area_tone
from app.db import get_conn, get_day_blocks

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
    """(열 목록, 헤더 라벨). 보고 있는 기간이 왼쪽에서 두 번째 열에 오도록 앞에 하나를 더 둔다.

    연은 5칸, 분기·월·주는 4칸이다. 열은 key·label·sub·current·week_link·drill_*·start·end
    와 함께 brk(더 큰 단위가 바뀌는 자리의 이름)·brk_level(year|quarter|month)을 가진다.
    brk 는 첫 열에는 달지 않는다(왼쪽 끝에는 그을 자리가 없다).

    drill_level/drill_anchor: 그 열 머리글을 누르면 들어갈 다음(더 잘은) 단위와 anchor.
    start/end: 그 열이 덮는 실제 날짜 구간(date). 간트 막대 위치 계산에 쓴다.
    """
    today = datetime.now(KST).date()
    cols: list[dict] = []
    if level == "year":
        y0 = anchor.year - 1                    # 보고 있는 해가 두 번째
        for y in range(y0, y0 + 5):
            cols.append({"key": str(y), "label": str(y), "sub": "",
                         "current": y == today.year, "week_link": None,
                         "drill_level": "quarter", "drill_anchor": f"{y}-01-01",
                         "start": date(y, 1, 1), "end": date(y, 12, 31),
                         "brk": "", "brk_level": ""})
        header = f"{y0}–{y0 + 4}"
    elif level == "quarter":
        q_start = date(anchor.year, (anchor.month - 1) // 3 * 3 + 1, 1)
        for i in range(4):
            s = _add_months(q_start, (i - 1) * 3)
            y, m0 = s.year, s.month
            q = (m0 - 1) // 3 + 1
            cols.append({"key": f"{y}-Q{q}", "label": f"{q}분기",
                         "sub": f"{m0}~{m0 + 2}월",
                         "current": y == today.year and (today.month - 1) // 3 + 1 == q,
                         "week_link": None,
                         "drill_level": "month",
                         "drill_anchor": f"{y}-{m0:02d}-01",
                         "start": s, "end": _month_last(y, m0 + 2),
                         "brk": f"{y}년" if (q == 1 and i) else "",
                         "brk_level": "year" if (q == 1 and i) else ""})
        header = _span_header(cols)
    elif level == "month":
        m_start = date(anchor.year, anchor.month, 1)
        for i in range(4):
            s = _add_months(m_start, i - 1)
            y, m = s.year, s.month
            if m == 1:
                brk, lv = f"{y}년", "year"
            elif m in (4, 7, 10):
                brk, lv = f"{(m - 1) // 3 + 1}분기", "quarter"
            else:
                brk, lv = "", ""
            cols.append({"key": f"{y}-{m:02d}", "label": f"{m}월", "sub": "",
                         "current": y == today.year and m == today.month,
                         "week_link": None,
                         "drill_level": "week", "drill_anchor": f"{y}-{m:02d}-01",
                         "start": s, "end": _month_last(y, m),
                         "brk": brk if i else "", "brk_level": lv if i else ""})
        header = _span_header(cols)
    else:  # week
        cur_monday = today - timedelta(days=today.weekday())
        first = anchor - timedelta(days=anchor.weekday() + 7)   # 보고 있는 주가 두 번째
        prev_month = None
        for i in range(4):
            monday = first + timedelta(weeks=i)
            end = monday + timedelta(days=6)
            if prev_month is None or monday.month == prev_month:
                brk, lv = "", ""
            elif monday.month == 1:
                brk, lv = f"{monday.year}년", "year"
            else:
                brk, lv = f"{monday.month}월", "month"
            prev_month = monday.month
            key = monday.strftime("%Y-%m-%d")
            cols.append({"key": key, "label": f"{monday.month}/{monday.day}",
                         "sub": f"~{end.month}/{end.day} · {monday.isocalendar()[1]}주",
                         "current": monday == cur_monday, "week_link": key,
                         "drill_level": None, "drill_anchor": None,
                         "start": monday, "end": end,
                         "brk": brk, "brk_level": lv})
        header = _span_header(cols)
    return cols, header


def _span_header(cols) -> str:
    """보이는 기간 전체를 한 줄로. 해가 같으면 해를 한 번만 적는다."""
    s, e = cols[0]["start"], cols[-1]["end"]
    if s.year == e.year:
        return f"{s.year}년 {s.month}월 – {e.month}월"
    return f"{s.year}년 {s.month}월 – {e.year}년 {e.month}월"


def _plan_nav(level: str, anchor: date):
    """이전/다음 anchor(YYYY-MM-DD 문자열) 쌍. 그 단위 하나만큼만 옮긴다."""
    if level == "year":
        return f"{anchor.year - 1:04d}-01-01", f"{anchor.year + 1:04d}-01-01"
    if level == "quarter":
        return (_add_months(anchor, -3).strftime("%Y-%m-%d"),
                _add_months(anchor, 3).strftime("%Y-%m-%d"))
    if level == "month":
        return (_add_months(anchor, -1).strftime("%Y-%m-%d"),
                _add_months(anchor, 1).strftime("%Y-%m-%d"))
    return ((anchor - timedelta(days=7)).strftime("%Y-%m-%d"),
            (anchor + timedelta(days=7)).strftime("%Y-%m-%d"))


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


def _block_rows() -> list[dict]:
    """간트 왼쪽에 세울 행. 코어블록 B1~B6 + 블록을 정하지 않은 항목이 모이는 '미지정' 한 줄."""
    times = {lbl: f"{s}~{e}" for lbl, is_core, s, e in get_day_blocks() if is_core}
    rows = [{"key": b, "label": b, "time": times.get(b, "")} for b in CORE_BLOCKS]
    rows.append({"key": "", "label": "미지정", "time": "블록 없음"})
    return rows


def _gantt_blocks(conn, areas, span_start: date, span_end: date) -> list[dict]:
    """블록(B1~B6·미지정)별 간트 행 목록. 그 블록으로 배정된 막대가 한 줄에 모두 들어간다.

    막대 색은 영역 톤(tone)이고 진하기는 기간 구분(span_class)이라 한 줄 안에서도
    어느 영역·어느 기간인지 함께 읽힌다. 하위 항목은 자기 블록이 없으면 상위 블록을 따른다.
    상위·하위는 지금처럼 한 줄에서 겹쳐 그리되, depth 는 같은 줄에 있는 조상 수로만 센다
    (하위가 다른 블록으로 빠지면 그 줄에서는 다시 맨 위 단계가 된다).
    left/width 는 보이는 기간 전체에 대한 퍼센트라 템플릿이 계산 없이 그린다.
    """
    total = (span_end - span_start).days + 1
    today = datetime.now(KST).date()
    tones = {a["id"]: a["tone"] for a in areas}
    names = {a["id"]: a["name"] for a in areas}
    rows = _block_rows()
    valid = {b["key"] for b in rows}
    children: dict[int | None, list] = {}
    for r in conn.execute(
        "SELECT id, area_id, parent_id, title, start_date, end_date, progress, "
        "       block_label FROM lt_item ORDER BY start_date, id"
    ):
        if r["area_id"] in tones:
            children.setdefault(r["parent_id"], []).append(dict(r))

    def overlaps(it) -> bool:
        s, e = _parse_date(it["start_date"]), _parse_date(it["end_date"])
        if not s or not e:
            return False
        if s <= span_end and e >= span_start:
            return True
        return any(overlaps(c) for c in children.get(it["id"], []))

    def bar(it, block: str, depth: int) -> dict:
        s = _parse_date(it["start_date"]) or span_start
        e = _parse_date(it["end_date"]) or s
        vs, ve = max(s, span_start), min(e, span_end)
        visible = vs <= ve
        row = dict(it)
        row["block"] = block
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
        row["tone"] = tones.get(it["area_id"], "blue")
        row["area_name"] = names.get(it["area_id"], "")
        return row

    bars_by_block: dict[str, list] = {k: [] for k in valid}

    def walk(it, parent_block: str, same_row_depth: int):
        own = (it["block_label"] or "").strip()
        block = own if (own and own in valid) else parent_block
        depth = same_row_depth if block == parent_block else 0
        b = bar(it, block, depth)
        if b["visible"]:
            bars_by_block[block].append(b)
        for c in children.get(it["id"], []):
            if overlaps(c):
                walk(c, block, depth + 1)

    for it in children.get(None, []):
        if it["area_id"] in tones and overlaps(it):
            walk(it, "", 0)

    # 키 이름은 'items'를 피한다(Jinja에서 dict.items 메서드와 겹친다).
    return [{**row,
             "lanes": max((b["depth"] for b in bars_by_block[row["key"]]), default=0),
             "bars": bars_by_block[row["key"]]}
            for row in rows]


def _clean_block(raw) -> str:
    """폼에서 온 블록 값을 B1~B6 중 하나로. 비었거나 모르는 값이면 ''(미지정)."""
    b = (raw or "").strip()
    return b if b in CORE_BLOCKS else ""


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
            "progress, block_label, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (area_id, parent_id, title, start.isoformat(), end.isoformat(),
             _clean_block(form.get("block")) or None, now),
        )
        new_id = cur.lastrowid
        _lt_rollup(conn, new_id)
    return JSONResponse({"ok": True, "id": new_id})


@router.post("/plan/item/update")
async def plan_item_update(request: Request):
    """간트 항목의 제목·기간·진척률·블록을 고친다(보낸 값만 바꾼다)."""
    form = await request.form()
    try:
        item_id = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    fields: dict = {}
    if (form.get("title") or "").strip():
        fields["title"] = form.get("title").strip()
    # 블록은 빈 값도 뜻이 있다('미지정'으로 되돌리기). 칸이 왔는지로만 판단한다.
    if form.get("block") is not None:
        fields["block_label"] = _clean_block(form.get("block")) or None
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
                "SELECT id, name, tone FROM lt_area WHERE is_active = 1 "
                "ORDER BY display_order"
            )
        ]
        all_areas = conn.execute(
            "SELECT id, name, is_active, tone FROM lt_area "
            "ORDER BY is_active DESC, display_order"
        ).fetchall()
        gantt = _gantt_blocks(conn, areas, span_start, span_end)
    # 기본으로 접히는 지난 항목 수(0이면 '지난 항목 보기' 버튼도 내보내지 않는다)
    past_count = sum(1 for row in gantt for b in row["bars"] if b["past"])
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
            "core_blocks": CORE_BLOCKS,
            "tones": TONES,
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
                "INSERT INTO lt_area (name, display_order, is_active, tone) "
                "VALUES (?, ?, 1, ?)",
                (name, order, area_tone(order)),
            )
            cid = cur.lastrowid
    return JSONResponse({"ok": True, "id": cid, "name": name})


@router.post("/plan/area/update")
async def plan_area_update(request: Request):
    """영역 이름이나 막대 색(tone)을 바꾼다. 보낸 값만 고친다."""
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    fields: dict = {}
    if (form.get("name") or "").strip():
        fields["name"] = form.get("name").strip()
    tone = (form.get("tone") or "").strip()
    if tone:
        if tone not in TONE_KEYS:
            return JSONResponse({"ok": False, "error": "모르는 색"}, status_code=400)
        fields["tone"] = tone
    if not fields:
        return JSONResponse({"ok": False}, status_code=400)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE lt_area SET {sets} WHERE id = ?", (*fields.values(), cid))
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
