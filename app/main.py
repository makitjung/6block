# 오늘/주간 입력과 PWA 서빙, 포모도로 정적 자원을 제공하는 FastAPI 메인 애플리케이션
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import (
    DAY_BLOCKS,
    TONE_KEYS,
    TONES,
    WEEK_CORE_BLOCKS,
    WEEK_TOTAL_HOURS,
    hhmm_to_min,
    slots_for_day,
)
from app.db import get_conn, get_settings, init_db, set_setting
from app.integrations import gcal, things

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).parent
KO_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
CORE_LABELS = [b[0] for b in DAY_BLOCKS if b[1]]  # B1..B6


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="6block", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.middleware("http")
async def no_cache_headers(request: Request, call_next):
    """정적 자원·HTML은 항상 서버와 재검증(no-cache)해 옛 캐시(특히 폰 PWA)가 남지 않게 한다.

    StaticFiles의 ETag/Last-Modified와 함께 동작해, 안 바뀌면 304로 가볍게,
    바뀌면 새 파일을 받게 한다.
    """
    response = await call_next(request)
    path = request.url.path
    ctype = response.headers.get("content-type", "")
    if (
        path.startswith("/static/")
        or path.endswith(".webmanifest")
        or ctype.startswith("text/html")
    ):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _ko_weekday(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return KO_WEEKDAYS[d.weekday()]


def _pretty_date(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return f"{d.month}월 {d.day}일 {KO_WEEKDAYS[d.weekday()]}요일"


def _short_date(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return f"{d.month}.{d.day}"


templates.env.filters["ko_weekday"] = _ko_weekday
templates.env.filters["pretty_date"] = _pretty_date
templates.env.filters["short_date"] = _short_date


def _asset_ver() -> str:
    """app.js/style.css의 최신 수정시각을 캐시버스팅 쿼리값으로 반환(파일 바뀌면 자동 변경)."""
    try:
        mtimes = [
            (BASE_DIR / "static" / "app.js").stat().st_mtime,
            (BASE_DIR / "static" / "style.css").stat().st_mtime,
        ]
        return str(int(max(mtimes)))
    except OSError:
        return "1"


templates.env.globals["asset_ver"] = _asset_ver
templates.env.globals["get_settings"] = get_settings


def today_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _skeleton_matches_config(conn, date_str: str) -> bool:
    """DB의 그날 블록 골격이 현재 설정(DAY_BLOCKS)과 정확히 같은지."""
    have = [
        (r["block_label"], r["start_time"], r["end_time"])
        for r in conn.execute(
            "SELECT block_label, start_time, end_time FROM blocks "
            "WHERE date = ? ORDER BY block_order",
            (date_str,),
        )
    ]
    want = [(label, start, end) for (label, _core, start, end) in DAY_BLOCKS]
    return have == want


def _day_has_content(conn, date_str: str) -> bool:
    """그날에 사용자가 입력한 내용이 있는지(슬롯 do·한 일·구분·완료, 블록 plan·see·이름·구분)."""
    if conn.execute(
        "SELECT 1 FROM slots WHERE date = ? AND ("
        "TRIM(COALESCE(do_text,'')) != '' OR TRIM(COALESCE(did_text,'')) != '' "
        "OR category_id IS NOT NULL OR done = 1) LIMIT 1",
        (date_str,),
    ).fetchone():
        return True
    return bool(
        conn.execute(
            "SELECT 1 FROM blocks WHERE date = ? AND ("
            "TRIM(COALESCE(plan_text,'')) != '' OR TRIM(COALESCE(see_text,'')) != '' "
            "OR category_id IS NOT NULL OR TRIM(COALESCE(name,'')) != '') LIMIT 1",
            (date_str,),
        ).fetchone()
    )


def ensure_day_skeleton(conn, date_str: str):
    """블록·슬롯이 없으면 생성한다. 설정이 바뀌었고 입력이 없는 날은 새 배치로 자동 재생성한다."""
    if conn.execute(
        "SELECT 1 FROM blocks WHERE date = ? LIMIT 1", (date_str,)
    ).fetchone():
        # 골격이 현재 설정과 같거나, 사용자가 입력한 내용이 있으면 그대로 둔다.
        if _skeleton_matches_config(conn, date_str) or _day_has_content(conn, date_str):
            return
        # 설정이 바뀌었고 입력이 없는 날은 옛 골격을 지우고 새 배치로 다시 만든다.
        conn.execute("DELETE FROM slots WHERE date = ?", (date_str,))
        conn.execute("DELETE FROM blocks WHERE date = ?", (date_str,))
    now = datetime.now(KST).isoformat(timespec="seconds")
    block_ids = {}
    for order, (label, is_core, start, end) in enumerate(DAY_BLOCKS):
        cur = conn.execute(
            """
            INSERT INTO blocks (date, block_order, block_label, is_core,
                                start_time, end_time, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (date_str, order, label, 1 if is_core else 0, start, end, now),
        )
        block_ids[label] = cur.lastrowid
    for slot_idx, label, s_t, e_t in slots_for_day():
        conn.execute(
            """
            INSERT INTO slots (date, block_id, slot_index, start_time, end_time,
                               updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (date_str, block_ids[label], slot_idx, s_t, e_t, now),
        )


@app.get("/")
def root():
    view = get_settings().get("start_view", "today")
    return RedirectResponse(url="/week" if view == "week" else "/today")


@app.get("/today")
def today_view(request: Request):
    return _day_view(request, today_str())


@app.get("/day/{date_str}")
def day_view(request: Request, date_str: str):
    return _day_view(request, date_str)


def _name_override(value, inherited: str):
    """블록 이름 입력값을 주간 상속과 비교해 덮어쓰기 값(없으면 None)을 돌려준다.

    비었거나 주간 이름과 같으면 None(주간 값을 따름), 다르면 그 값으로 덮어쓴다.
    """
    v = (value or "").strip()
    return None if (not v or v == inherited) else v


def _split3(s) -> list[str]:
    """줄바꿈으로 저장된 목표/계획을 정확히 3칸으로 분리(빈 칸 유지)."""
    parts = (s or "").split("\n")
    return (parts + ["", "", ""])[:3]


def _join3(form, prefix: str) -> str:
    """폼의 prefix1/2/3 값을 줄바꿈으로 합친다. 모두 비면 빈 문자열."""
    vals = [(form.get(f"{prefix}{i}", "") or "").strip() for i in (1, 2, 3)]
    joined = "\n".join(vals)
    return joined if joined.strip() else ""


def _distribute(blocks, timed_items):
    """시각이 있는 항목을 시작 분 기준으로 해당 블록에 배치한다.

    반환: (block_id -> [item...], 어느 블록에도 안 들어간 leftover 리스트).
    """
    ranges = [
        (b["id"], hhmm_to_min(b["start_time"]), hhmm_to_min(b["end_time"]))
        for b in blocks
    ]
    by_block: dict[int, list] = {b["id"]: [] for b in blocks}
    leftover: list = []
    for it in timed_items:
        m = it["start_min"]
        for bid, s, e in ranges:
            if s <= m < e:
                by_block[bid].append(it)
                break
        else:
            leftover.append(it)
    for items in by_block.values():
        items.sort(key=lambda x: x["start_min"])
    return by_block, leftover


def _day_agenda(blocks, d, is_today):
    """그날의 캘린더 일정·Things Today를 모으고 시간 항목을 블록에 배치한다.

    반환: (cal_events 전체, task_list 전체, block_id -> [시간 항목...]).
    """
    cal_events = gcal.events_for_date(d)
    task_list = things.today_tasks(d, include_overdue=is_today)
    timed: list = []
    for ev in cal_events:
        if not ev["all_day"] and ev["start_min"] is not None:
            timed.append(
                {
                    "kind": "event",
                    "title": ev["title"],
                    "time": ev["start"],
                    "end": ev["end"],
                    "start_min": ev["start_min"],
                    "color": ev["color"],
                }
            )
    for t in task_list:
        if t["time_min"] is not None:
            timed.append(
                {
                    "kind": "task",
                    "title": t["title"],
                    "time": t["time"],
                    "end": None,
                    "start_min": t["time_min"],
                }
            )
    block_events, _leftover = _distribute(blocks, timed)
    return cal_events, task_list, block_events


def _day_view(request: Request, date_str: str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    prev_date = (d - timedelta(days=1)).strftime("%Y-%m-%d")
    next_date = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    is_today = date_str == today_str()
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
        categories = [
            {"id": r["id"], "name": r["name"], "color": r["color"],
             "tone": r["tone"]}
            for r in conn.execute(
                "SELECT id, name, color, tone FROM categories "
                "WHERE is_active = 1 ORDER BY display_order"
            )
        ]
        blocks = conn.execute(
            "SELECT * FROM blocks WHERE date = ? ORDER BY block_order",
            (date_str,),
        ).fetchall()
        slots = conn.execute(
            "SELECT * FROM slots WHERE date = ? ORDER BY slot_index",
            (date_str,),
        ).fetchall()
        meta = conn.execute(
            "SELECT * FROM daily_meta WHERE date = ?", (date_str,)
        ).fetchone()
        # 오늘이 속한 주의 B1-B6 테마를 가져와 PLAN 영역 위에 placeholder로 노출
        wk_start = week_start(d).strftime("%Y-%m-%d")
        theme_rows = conn.execute(
            "SELECT block_label, theme_text FROM weekly_block_themes "
            "WHERE week_start = ?",
            (wk_start,),
        ).fetchall()
        inbox = conn.execute(
            "SELECT id, text FROM inbox WHERE done = 0 ORDER BY id DESC"
        ).fetchall()

    themes_by_label = {r["block_label"]: r["theme_text"] for r in theme_rows}
    # 일간 블록 이름 = 일간 덮어쓰기(blocks.name)가 있으면 그것, 없으면 주간 이름.
    block_name_by_id = {
        b["id"]: ((b["name"] or "").strip() or (themes_by_label.get(b["block_label"]) or ""))
        for b in blocks
    }
    slots_by_block: dict[int, list] = {}
    for s in slots:
        slots_by_block.setdefault(s["block_id"], []).append(s)

    # 외부 연동: Things3 Today + 구글 캘린더 일정.
    # 전체 목록은 최상단에 1번만 줄바꿈으로 노출(cal_events, task_list),
    # 시각이 있는 항목만 해당 시간 블록의 아젠다로 배치한다.
    cal_events, task_list, block_events = _day_agenda(blocks, d, is_today)

    # 오늘 목표/계획을 각각 3개로 분리(줄바꿈 저장, 레거시 1줄도 호환).
    goals = _split3(meta["today_goal"] if meta else "")
    plans = _split3(meta["daily_plan"] if meta else "")

    return templates.TemplateResponse(
        "today.html",
        {
            "request": request,
            "date_str": date_str,
            "prev_date": prev_date,
            "next_date": next_date,
            "is_today": is_today,
            "blocks": blocks,
            "slots_by_block": slots_by_block,
            "categories": categories,
            "meta": meta,
            "goals": goals,
            "plans": plans,
            "themes_by_label": themes_by_label,
            "block_name_by_id": block_name_by_id,
            "block_events": block_events,
            "cal_events": cal_events,
            "task_list": task_list,
            "inbox": inbox,
            "cal_enabled": gcal.enabled(),
        },
    )


@app.post("/save/{date_str}")
async def save_day(date_str: str, request: Request):
    form = await request.form()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
        # 일간 블록 이름 덮어쓰기 판정을 위해 주간 이름과 블록 라벨을 미리 로드
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        wk = week_start(d).strftime("%Y-%m-%d")
        block_label_by_id = {
            r["id"]: r["block_label"]
            for r in conn.execute(
                "SELECT id, block_label FROM blocks WHERE date = ?", (date_str,)
            )
        }
        weekly_name = {
            r["block_label"]: (r["theme_text"] or "").strip()
            for r in conn.execute(
                "SELECT block_label, theme_text FROM weekly_block_themes "
                "WHERE week_start = ?",
                (wk,),
            )
        }
        for key, val in form.multi_items():
            prefix, _, suffix = key.partition("_")
            if not suffix.isdigit():
                continue
            sid = int(suffix)
            if prefix == "plan":
                conn.execute(
                    "UPDATE blocks SET plan_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "see":
                conn.execute(
                    "UPDATE blocks SET see_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "do":
                conn.execute(
                    "UPDATE slots SET do_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "did":
                conn.execute(
                    "UPDATE slots SET did_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "cat":
                cid = int(val) if val else None
                conn.execute(
                    "UPDATE slots SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, sid),
                )
            elif prefix == "bcat":
                cid = int(val) if val else None
                conn.execute(
                    "UPDATE blocks SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, sid),
                )
            elif prefix == "bname":
                label = block_label_by_id.get(sid, "")
                override = _name_override(val, weekly_name.get(label, ""))
                conn.execute(
                    "UPDATE blocks SET name = ?, updated_at = ? WHERE id = ?",
                    (override, now, sid),
                )
        conn.execute(
            """
            INSERT INTO daily_meta (date, today_goal, daily_plan, memo, vow)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                today_goal = excluded.today_goal,
                daily_plan = excluded.daily_plan,
                memo = excluded.memo,
                vow = excluded.vow
            """,
            (
                date_str,
                _join3(form, "goal"),
                _join3(form, "dplan"),
                form.get("memo", ""),
                form.get("vow", ""),
            ),
        )
    return RedirectResponse(url=f"/day/{date_str}", status_code=303)


# -- GTD 빠른 수집함 --------------------------------------------------------


@app.post("/inbox/add")
async def inbox_add(request: Request):
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO inbox (text, created_at) VALUES (?, ?)", (text, now)
        )
        new_id = cur.lastrowid
    return JSONResponse({"ok": True, "id": new_id, "text": text})


@app.post("/inbox/done/{item_id}")
def inbox_done(item_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE inbox SET done = 1 WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True})


@app.post("/inbox/delete/{item_id}")
def inbox_delete(item_id: int):
    """수집함 항목을 완전히 삭제한다(정리 ✓와 달리 DB에서 지움)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM inbox WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True})


@app.post("/inbox/assign")
async def inbox_assign(request: Request):
    """수집함 항목을 한 블록의 PLAN 끝에 한 줄로 옮기고 수집함에서는 정리한다(GTD 정리 단계)."""
    form = await request.form()
    try:
        item_id = int(form.get("item_id"))
        block_id = int(form.get("block_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        it = conn.execute("SELECT text FROM inbox WHERE id = ?", (item_id,)).fetchone()
        blk = conn.execute(
            "SELECT plan_text FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        if not it or not blk:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        cur = (blk["plan_text"] or "").rstrip()
        plan_text = f"{cur}\n{it['text']}" if cur else it["text"]
        conn.execute(
            "UPDATE blocks SET plan_text = ?, updated_at = ? WHERE id = ?",
            (plan_text, now, block_id),
        )
        conn.execute("UPDATE inbox SET done = 1 WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True, "block_id": block_id, "plan_text": plan_text})


# -- 슬롯 실행 체크 + 실시간 폴링 -------------------------------------------


@app.post("/slot/done/{slot_id}")
async def slot_done(slot_id: int, request: Request):
    """DO 옆 체크박스. 즉시 저장(폼 저장과 별개)."""
    form = await request.form()
    val = 1 if (form.get("done") in ("1", "true", "on")) else 0
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "UPDATE slots SET done = ?, updated_at = ? WHERE id = ?",
            (val, now, slot_id),
        )
    return JSONResponse({"ok": True, "done": val})


@app.get("/api/day/{date_str}")
def api_day(date_str: str):
    """현재 캘린더·Things 아젠다를 JSON으로. 클라이언트가 주기적으로 폴링해 갱신."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    is_today = date_str == today_str()
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
        blocks = conn.execute(
            "SELECT * FROM blocks WHERE date = ? ORDER BY block_order",
            (date_str,),
        ).fetchall()
    cal_events, task_list, block_events = _day_agenda(blocks, d, is_today)
    order_by_id = {b["id"]: b["block_order"] for b in blocks}
    blocks_json: dict[str, list] = {}
    for bid, items in block_events.items():
        if items:
            blocks_json[str(order_by_id[bid])] = items
    return JSONResponse(
        {
            "cal_enabled": gcal.enabled(),
            "events": [
                {"all_day": e["all_day"], "start": e["start"], "title": e["title"],
                 "color": e["color"]}
                for e in cal_events
            ],
            "tasks": [
                {
                    "time": t["time"],
                    "title": t["title"],
                    "deadline": t["deadline"],
                    "overdue": t["overdue"],
                }
                for t in task_list
            ],
            "blocks": blocks_json,
        }
    )


@app.get("/week")
def week_view(request: Request):
    return _week_view(request, week_start(datetime.now(KST).date()))


@app.get("/week/{date_str}")
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
            {"id": r["id"], "name": r["name"], "color": r["color"],
             "tone": r["tone"]}
            for r in conn.execute(
                "SELECT id, name, color, tone FROM categories "
                "WHERE is_active = 1 ORDER BY display_order"
            )
        ]
        cat_summary = conn.execute(
            f"""
            SELECT c.name, c.color, c.tone, COUNT(s.id) AS slot_count
            FROM slots s
            JOIN categories c ON c.id = s.category_id
            WHERE s.date IN ({placeholders})
            GROUP BY c.id
            ORDER BY slot_count DESC
            """,
            dates,
        ).fetchall()
        plan_total = conn.execute(
            f"""
            SELECT COUNT(*) FROM blocks
            WHERE date IN ({placeholders}) AND is_core = 1
              AND plan_text IS NOT NULL AND TRIM(plan_text) != ''
            """,
            dates,
        ).fetchone()[0]
        achieved = conn.execute(
            f"""
            SELECT COUNT(DISTINCT b.id) FROM blocks b
            JOIN slots s ON s.block_id = b.id
            WHERE b.date IN ({placeholders}) AND b.is_core = 1
              AND b.plan_text IS NOT NULL AND TRIM(b.plan_text) != ''
              AND s.do_text IS NOT NULL AND TRIM(s.do_text) != ''
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
        # 주간 리뷰(GTD 검토): 미처리 수집함 + 계획만 하고 실행 흔적 없는 코어 블록
        review_inbox = conn.execute(
            "SELECT id, text FROM inbox WHERE done = 0 ORDER BY id DESC"
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
    achieve_pct = round(achieved / plan_total * 100) if plan_total else 0
    used_core_total = WEEK_CORE_BLOCKS

    total_slots = sum(r["slot_count"] for r in cat_summary)
    cat_summary_pct = [
        {
            "name": r["name"],
            "color": r["color"],
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
            "week_total_hours": WEEK_TOTAL_HOURS,
            "wmeta": wmeta,
            "themes_by_label": themes_by_label,
            "core_labels": CORE_LABELS,
            "week_block_events": week_block_events,
            "week_allday": week_allday,
            "cal_enabled": gcal.enabled(),
            "today": today_str(),
            "review_inbox": review_inbox,
            "missed_blocks": missed_blocks,
        },
    )


@app.post("/week/save/{week_start_str}")
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


# -- 설정 -------------------------------------------------------------------


@app.get("/settings")
def settings_view(request: Request):
    settings = get_settings()
    with get_conn() as conn:
        cats = conn.execute(
            "SELECT id, name, tone, is_active FROM categories "
            "ORDER BY is_active DESC, display_order"
        ).fetchall()
        rec_filter = "(do_text IS NOT NULL AND TRIM(do_text) != '') OR done = 1"
        rec_days = conn.execute(
            f"SELECT COUNT(DISTINCT date) FROM slots WHERE {rec_filter}"
        ).fetchone()[0]
        slot_recs = conn.execute(
            f"SELECT COUNT(*) FROM slots WHERE {rec_filter}"
        ).fetchone()[0]
        span = conn.execute(
            f"SELECT MIN(date), MAX(date) FROM slots WHERE {rec_filter}"
        ).fetchone()
        inbox_open = conn.execute(
            "SELECT COUNT(*) FROM inbox WHERE done = 0"
        ).fetchone()[0]
    summary = {
        "rec_days": rec_days,
        "slot_recs": slot_recs,
        "first": span[0] or "-",
        "last": span[1] or "-",
        "inbox_open": inbox_open,
        "active_cats": sum(1 for c in cats if c["is_active"]),
    }
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "categories": [dict(c) for c in cats],
            "tones": TONES,
            "settings": settings,
            "summary": summary,
            "today": today_str(),
        },
    )


@app.post("/settings/category/add")
async def settings_cat_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    tone = (form.get("tone") or "black").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    if tone not in TONE_KEYS:
        tone = "black"
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
        if row:  # 같은 이름이 있으면(비활성 포함) 다시 활성화하고 톤만 갱신
            conn.execute(
                "UPDATE categories SET is_active = 1, tone = ? WHERE id = ?",
                (tone, row["id"]),
            )
            cid = row["id"]
        else:
            order = conn.execute(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM categories"
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO categories (name, color, tone, display_order, is_active) "
                "VALUES (?, '#202124', ?, ?, 1)",
                (name, tone, order),
            )
            cid = cur.lastrowid
    return JSONResponse({"ok": True, "id": cid, "name": name, "tone": tone})


@app.post("/settings/category/update")
async def settings_cat_update(request: Request):
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    fields = {}
    if form.get("name") is not None and (form.get("name") or "").strip():
        fields["name"] = form.get("name").strip()
    if form.get("tone") in TONE_KEYS:
        fields["tone"] = form.get("tone")
    if form.get("is_active") is not None:
        fields["is_active"] = 1 if form.get("is_active") in ("1", "true", "on") else 0
    if not fields:
        return JSONResponse({"ok": False}, status_code=400)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE categories SET {sets} WHERE id = ?", (*fields.values(), cid)
        )
    return JSONResponse({"ok": True})


@app.post("/settings/category/move")
async def settings_cat_move(request: Request):
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    direction = form.get("dir")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, display_order FROM categories WHERE is_active = 1 "
            "ORDER BY display_order"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if cid not in ids:
            return JSONResponse({"ok": False}, status_code=404)
        i = ids.index(cid)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(rows):
            a, b = rows[i], rows[j]
            conn.execute(
                "UPDATE categories SET display_order = ? WHERE id = ?",
                (b["display_order"], a["id"]),
            )
            conn.execute(
                "UPDATE categories SET display_order = ? WHERE id = ?",
                (a["display_order"], b["id"]),
            )
    return JSONResponse({"ok": True})


@app.post("/settings/category/delete")
async def settings_cat_delete(request: Request):
    """카테고리를 숨김 처리한다(소프트 삭제). 슬롯·블록의 기존 참조는 보존된다."""
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE categories SET is_active = 0 WHERE id = ?", (cid,))
    return JSONResponse({"ok": True})


@app.post("/settings/save")
async def settings_save(request: Request):
    form = await request.form()
    allowed = {"start_view", "default_theme", "pomo_auto", "pomo_warn5", "collapse_blocks"}
    for key in allowed:
        if form.get(key) is not None:
            set_setting(key, form.get(key))
    return JSONResponse({"ok": True})


@app.post("/settings/backup")
def settings_backup():
    """scripts/backup.py를 즉시 실행해 .sql 덤프를 만든다."""
    try:
        import importlib.util

        path = BASE_DIR.parent / "scripts" / "backup.py"
        spec = importlib.util.spec_from_file_location("backup", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.dump()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)


@app.get("/settings/export.csv")
def settings_export(start: str, end: str):
    """기간 내 슬롯 기록을 CSV로 내보낸다(엑셀 호환 UTF-8 BOM)."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["날짜", "블록", "시각", "구분", "DO(계획)", "한일(실제)", "완료"])
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT s.date, b.block_label, s.start_time, c.name AS cat, "
            "       s.do_text, s.did_text, s.done "
            "FROM slots s JOIN blocks b ON b.id = s.block_id "
            "LEFT JOIN categories c ON c.id = s.category_id "
            "WHERE s.date BETWEEN ? AND ? ORDER BY s.date, s.slot_index",
            (start, end),
        ):
            w.writerow([
                r["date"], r["block_label"], r["start_time"], r["cat"] or "",
                r["do_text"] or "", r["did_text"] or "", r["done"],
            ])
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=6block-{start}_{end}.csv"},
    )


@app.post("/settings/purge")
async def settings_purge(request: Request):
    """기간 내 기록(슬롯·블록·일 메타)을 삭제한다. 되돌릴 수 없다."""
    form = await request.form()
    start = (form.get("start") or "").strip()
    end = (form.get("end") or "").strip()
    if not start or not end:
        return JSONResponse({"ok": False, "error": "기간 필요"}, status_code=400)
    with get_conn() as conn:
        conn.execute("DELETE FROM slots WHERE date BETWEEN ? AND ?", (start, end))
        conn.execute("DELETE FROM blocks WHERE date BETWEEN ? AND ?", (start, end))
        conn.execute("DELETE FROM daily_meta WHERE date BETWEEN ? AND ?", (start, end))
    return JSONResponse({"ok": True})


# -- PWA --------------------------------------------------------------------


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(
        BASE_DIR / "static" / "manifest.json",
        media_type="application/manifest+json",
    )


@app.get("/api/health")
def api_health():
    """연동 상태 점검. 브라우저에서 /api/health로 캘린더·Things 연결 확인."""
    return {"gcal": gcal.status(), "things": things.status()}


@app.get("/api/now")
def api_now():
    """클라이언트가 서버 시각 기준으로 포모도로 정렬할 수 있게 KST를 반환."""
    n = datetime.now(KST)
    return {"iso": n.isoformat(timespec="seconds"), "epoch_ms": int(n.timestamp() * 1000)}
