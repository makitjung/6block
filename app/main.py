# 오늘/주간 입력과 PWA 서빙, 포모도로 정적 자원을 제공하는 FastAPI 메인 애플리케이션
import json
import re
import threading
import urllib.parse
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
    BACKUP_DIR,
    CLOUD_BACKUP_DIR,
    DAY_BLOCKS,
    TONE_KEYS,
    TONES,
    WEEK_CORE_BLOCKS,
    hhmm_to_min,
    slots_for_day,
)
from app.db import (
    BLOCK_TIMES_KEY,
    get_conn,
    get_day_blocks,
    get_settings,
    init_db,
    set_setting,
)
from app.integrations import gcal, gcal_write, things

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).parent
KO_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
CORE_LABELS = [b[0] for b in DAY_BLOCKS if b[1]]  # B1..B6


def _migrate_gcal_titles():
    """옛 종류(감상·결심)로 만든 구글 이벤트 제목 접두어를 새 종류로 한 번만 정정한다."""
    try:
        if not gcal_write.enabled():
            return
        if get_settings().get("reflect_gcal_titles_migrated") == "1":
            return
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT kind, title, text, tags, gcal_event_id FROM reflection "
                "WHERE gcal_event_id IS NOT NULL AND kind IN ('감사', '결정') LIMIT 500"
            ).fetchall()
        for r in rows:
            gcal_write.update_event(
                r["gcal_event_id"], r["kind"], _reflect_title(r["title"], r["text"]),
                r["text"] or "", r["tags"] or "",
            )
        set_setting("reflect_gcal_titles_migrated", "1")
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # 옛 구글 이벤트 제목 정정은 시작을 막지 않게 백그라운드에서(한 번만).
    threading.Thread(target=_migrate_gcal_titles, daemon=True).start()
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


# -- 장기플랜 기간 계산 ------------------------------------------------------
PLAN_LEVELS = ("year", "quarter", "month", "week")
PLAN_LEVEL_LABELS = {"year": "연", "quarter": "분기", "month": "월", "week": "주"}


def _parse_anchor(anchor: str) -> date:
    """anchor 쿼리(YYYY-MM-DD)를 date로. 비었거나 잘못되면 오늘(KST)."""
    try:
        return datetime.strptime(anchor, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return datetime.now(KST).date()


def _plan_columns(level: str, anchor: date):
    """(열 목록, 헤더 라벨). 열은 key·label·sub·current·week_link·drill_* 를 가진다.

    drill_level/drill_anchor: 그 열 머리글을 누르면 들어갈 다음(더 잘은) 단위와 anchor.
    """
    today = datetime.now(KST).date()
    cols: list[dict] = []
    if level == "year":
        y0 = anchor.year
        for y in range(y0, y0 + 6):
            cols.append({"key": str(y), "label": str(y), "sub": "",
                         "current": y == today.year, "week_link": None,
                         "drill_level": "quarter", "drill_anchor": f"{y}-01-01"})
        header = f"{y0}–{y0 + 5}"
    elif level == "quarter":
        y = anchor.year
        for q in range(1, 5):
            cols.append({"key": f"{y}-Q{q}", "label": f"{q}분기",
                         "sub": f"{(q - 1) * 3 + 1}~{q * 3}월",
                         "current": y == today.year and (today.month - 1) // 3 + 1 == q,
                         "week_link": None,
                         "drill_level": "month",
                         "drill_anchor": f"{y}-{(q - 1) * 3 + 1:02d}-01"})
        header = f"{y}년"
    elif level == "month":
        y = anchor.year
        for m in range(1, 13):
            cols.append({"key": f"{y}-{m:02d}", "label": f"{m}월", "sub": "",
                         "current": y == today.year and m == today.month,
                         "week_link": None,
                         "drill_level": "week", "drill_anchor": f"{y}-{m:02d}-01"})
        header = f"{y}년"
    else:  # week
        y, m = anchor.year, anchor.month
        first = date(y, m, 1)
        nextm = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        last = nextm - timedelta(days=1)
        monday = first - timedelta(days=first.weekday())
        cur_monday = today - timedelta(days=today.weekday())
        while monday <= last:
            key = monday.strftime("%Y-%m-%d")
            end = monday + timedelta(days=6)
            cols.append({"key": key, "label": f"{monday.month}/{monday.day}",
                         "sub": f"~{end.month}/{end.day}",
                         "current": monday == cur_monday, "week_link": key,
                         "drill_level": None, "drill_anchor": None})
            monday += timedelta(days=7)
        header = f"{y}년 {m}월"
    return cols, header


def _plan_nav(level: str, anchor: date):
    """현재 단위에서 이전/다음 기간으로 이동할 anchor(YYYY-MM-DD 문자열) 쌍."""
    if level == "year":
        return f"{anchor.year - 6:04d}-01-01", f"{anchor.year + 6:04d}-01-01"
    if level in ("quarter", "month"):
        return f"{anchor.year - 1:04d}-01-01", f"{anchor.year + 1:04d}-01-01"
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


def _skeleton_matches_config(conn, date_str: str) -> bool:
    """DB의 그날 블록 골격이 현재 효과적 설정(시간 편집 반영)과 정확히 같은지."""
    have = [
        (r["block_label"], r["start_time"], r["end_time"])
        for r in conn.execute(
            "SELECT block_label, start_time, end_time FROM blocks "
            "WHERE date = ? ORDER BY block_order",
            (date_str,),
        )
    ]
    want = [(label, start, end) for (label, _core, start, end) in get_day_blocks()]
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
    day_blocks = get_day_blocks()
    block_ids = {}
    for order, (label, is_core, start, end) in enumerate(day_blocks):
        cur = conn.execute(
            """
            INSERT INTO blocks (date, block_order, block_label, is_core,
                                start_time, end_time, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (date_str, order, label, 1 if is_core else 0, start, end, now),
        )
        block_ids[label] = cur.lastrowid
    for slot_idx, label, s_t, e_t in slots_for_day(day_blocks):
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
        # '다시 볼 날짜'가 이 날짜인 고민·감상(그날 다시 보라고 잡아둔 것)
        due_reflections = conn.execute(
            "SELECT id, kind, title, text, tags FROM reflection "
            "WHERE review_date = ? ORDER BY id DESC",
            (date_str,),
        ).fetchall()
        # 이 날짜 요일의 컨셉(오늘 각 블록 오른쪽에 표시)
        wc = conn.execute(
            "SELECT text FROM weekday_concept WHERE weekday = ?", (d.weekday(),)
        ).fetchone()

    weekday_concept = (wc["text"] if wc else "") or ""
    weekday_label = KO_WEEKDAYS[d.weekday()]
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
            "due_reflections": [dict(r) for r in due_reflections],
            "weekday_concept": weekday_concept,
            "weekday_label": weekday_label,
            "cal_enabled": gcal.enabled(),
            "things_write_on": things.enabled(),
            "gcal_events_on": gcal_write.events_enabled(),
        },
    )


@app.post("/save/day/{date_str}")
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
            elif prefix == "bloc":
                conn.execute(
                    "UPDATE blocks SET location = ?, updated_at = ? WHERE id = ?",
                    (val or None, now, sid),
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


# -- 필드별 자동저장 (blur/debounce 한 칸 즉시 저장) -------------------------
# 장기플랜 /plan/cell/save 와 같은 단일 필드 저장 패턴. 전체 폼 저장(저장 버튼)과
# 병행해 쓴다. 클라이언트는 한 필드가 바뀌면 곧장 이 엔드포인트로 보낸다.

_VALID_BLOCK_FIELDS = {"plan_text", "see_text", "bcat", "bname", "bloc"}
_VALID_SLOT_FIELDS = {"do_text", "did_text", "cat"}


@app.post("/save/field")
async def save_field(request: Request):
    """한 필드만 즉시 저장한다. entity=block|slot|meta, id, field, value 를 받는다."""
    form = await request.form()
    entity = (form.get("entity") or "").strip()
    field = (form.get("field") or "").strip()
    raw_id = form.get("id")
    value = form.get("value") or ""
    now = datetime.now(KST).isoformat(timespec="seconds")
    # block/slot 은 숫자 id, meta(날짜)·wmeta(주 시작일)·theme(주 시작일) 는 문자열 id 를 쓴다.
    rid = None
    if entity not in ("meta", "wmeta", "theme"):
        try:
            rid = int(raw_id)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)

    with get_conn() as conn:
        if entity == "block":
            if field not in _VALID_BLOCK_FIELDS:
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            row = conn.execute(
                "SELECT date, block_label FROM blocks WHERE id = ?", (rid,)
            ).fetchone()
            if not row:
                return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
            if field == "bname":
                # 일간 덮어쓰기 판정을 위해 주간 이름과 비교(None이면 상속)
                wk = week_start(
                    datetime.strptime(row["date"], "%Y-%m-%d").date()
                ).strftime("%Y-%m-%d")
                wrow = conn.execute(
                    "SELECT theme_text FROM weekly_block_themes "
                    "WHERE week_start = ? AND block_label = ?",
                    (wk, row["block_label"]),
                ).fetchone()
                override = _name_override(value, (wrow["theme_text"] if wrow else ""))
                conn.execute(
                    "UPDATE blocks SET name = ?, updated_at = ? WHERE id = ?",
                    (override, now, rid),
                )
            elif field == "bcat":
                cid = int(value) if value else None
                conn.execute(
                    "UPDATE blocks SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, rid),
                )
            else:  # plan_text | see_text | bloc
                col = "location" if field == "bloc" else field
                conn.execute(
                    f"UPDATE blocks SET {col} = ?, updated_at = ? WHERE id = ?",
                    ((value or None) if field == "bloc" else value, now, rid),
                )
        elif entity == "slot":
            if field not in _VALID_SLOT_FIELDS:
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            if field == "cat":
                cid = int(value) if value else None
                conn.execute(
                    "UPDATE slots SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, rid),
                )
            else:  # do_text | did_text
                conn.execute(
                    f"UPDATE slots SET {field} = ?, updated_at = ? WHERE id = ?",
                    (value, now, rid),
                )
        elif entity == "meta":
            # id 자리에 날짜(문자열)가 온다. field: goal1~3|dplan1~3|memo|vow
            date_str = form.get("id") or ""
            if field in ("memo", "vow"):
                conn.execute(
                    "INSERT INTO daily_meta (date, %s) VALUES (?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET %s = excluded.%s"
                    % (field, field, field),
                    (date_str, value),
                )
            elif field.startswith("goal") or field.startswith("dplan"):
                # 목표/계획 3칸: 같은 prefix의 3값을 읽어 들여, 바뀐 한 칸만 갱신한 뒤
                # 줄바꿈 합친 전체를 다시 저장한다(클라이언트가 나머지 두 값을 같이 보냄).
                prefix = "goal" if field.startswith("goal") else "dplan"
                vals = [form.get(f"{prefix}{i}", "") or "" for i in (1, 2, 3)]
                # 폼에서 안 온 칸이 있을 수 있으니 기존 값으로 보충
                col = "today_goal" if prefix == "goal" else "daily_plan"
                existing = conn.execute(
                    f"SELECT {col} FROM daily_meta WHERE date = ?", (date_str,)
                ).fetchone()
                parts = (existing[col] if existing and existing[col] else "").split("\n") if existing else []
                parts = (parts + ["", "", ""])[:3]
                for i in range(3):
                    if f"{prefix}{i+1}" in form:
                        parts[i] = form.get(f"{prefix}{i+1}", "") or ""
                joined = "\n".join(p.strip() for p in parts)
                joined = joined if joined.strip() else ""
                conn.execute(
                    "INSERT INTO daily_meta (date, %s) VALUES (?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET %s = excluded.%s"
                    % (col, col, col),
                    (date_str, joined),
                )
            else:
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
        elif entity == "wmeta":
            # id 자리에 주 시작일(week_start). field: weekly_goal|appointments|vow|memo
            ws = form.get("id") or ""
            if field not in ("weekly_goal", "appointments", "vow", "memo"):
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            conn.execute(
                "INSERT INTO weekly_meta (week_start, %s) VALUES (?, ?) "
                "ON CONFLICT(week_start) DO UPDATE SET %s = excluded.%s"
                % (field, field, field),
                (ws, value),
            )
        elif entity == "theme":
            # id=week_start, label=블록 라벨(B1..B6), value=테마 텍스트
            ws = form.get("id") or ""
            label = (form.get("label") or "").strip()
            if not label:
                return JSONResponse({"ok": False, "error": "bad-label"}, status_code=400)
            conn.execute(
                "INSERT INTO weekly_block_themes (week_start, block_label, theme_text, "
                "updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(week_start, block_label) DO UPDATE SET "
                "theme_text = excluded.theme_text, updated_at = excluded.updated_at",
                (ws, label, value, now),
            )
        else:
            return JSONResponse({"ok": False, "error": "bad-entity"}, status_code=400)
    return JSONResponse({"ok": True})


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


@app.post("/inbox/update")
async def inbox_update(request: Request):
    """수집함 항목 텍스트를 수정한다(오늘·주간 공용. 같은 inbox 테이블)."""
    form = await request.form()
    try:
        item_id = int(form.get("item_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    text = (form.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE inbox SET text = ? WHERE id = ?", (text, item_id))
    return JSONResponse({"ok": True})


# -- 오늘 외부 입력: Things3 할일 / 구글 일정 쓰기 -------------------------


@app.post("/things/add")
async def things_add(request: Request):
    """오늘 탭에서 입력한 할일을 Things3 Today에 만든다(macOS AppleScript)."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    if not things.enabled():
        return JSONResponse({"ok": False, "error": "things-off"}, status_code=400)
    ok = things.add_todo(title)
    if not ok:
        return JSONResponse({"ok": False, "error": "권한 미승인 또는 Things3 미실행"},
                            status_code=502)
    return JSONResponse({"ok": True})


@app.post("/gcal/event/add")
async def gcal_event_add(request: Request):
    """오늘 탭에서 입력한 일정을 일정용 구글 캘린더에 만든다(서비스계정)."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    time_hhmm = (form.get("time") or "").strip() or None
    date_str = (form.get("date") or today_str()).strip()
    if not title:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    if not gcal_write.events_enabled():
        return JSONResponse(
            {"ok": False, "error": "일정 쓰기 미설정(캘린더 공유 + GCAL_WRITE_EVENTS_CALENDAR_ID)"},
            status_code=400,
        )
    try:
        ev = gcal_write.create_calendar_event(title, date_str, time_hhmm)
    except Exception:
        ev = None
    if not ev:
        return JSONResponse({"ok": False, "error": "캘린더 생성 실패"}, status_code=502)
    return JSONResponse({"ok": True, "id": ev})


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
            "week_total_hours": len(slots_for_day(get_day_blocks())) * 0.5 * 7,
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


# -- 장기플랜 ---------------------------------------------------------------


@app.get("/plan")
def plan_view(request: Request, level: str = "year", anchor: str = ""):
    if level not in PLAN_LEVELS:
        level = "year"
    a = _parse_anchor(anchor)
    cols, header = _plan_columns(level, a)
    keys = [c["key"] for c in cols]
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
        },
    )


@app.post("/plan/cell/save")
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


@app.post("/plan/area/add")
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


@app.post("/plan/area/update")
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


@app.post("/plan/area/move")
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


@app.post("/plan/area/delete")
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


# -- 설정 -------------------------------------------------------------------


def _backup_status() -> list[dict]:
    """로컬·클라우드 백업 폴더의 최신 .sql 덤프 상태(파일명·크기KB·경과일)를 돌려준다."""
    today = datetime.now(KST).date()
    out = []
    for label, d in (("로컬", BACKUP_DIR), ("클라우드", CLOUD_BACKUP_DIR)):
        info = {"label": label, "ok": False, "name": "없음", "kb": 0, "age": None}
        try:
            files = sorted(d.glob("blocks-*.sql"))  # 파일명이 YYYYMMDD라 사전식=시간순
            if files:
                latest = files[-1]
                info["ok"] = True
                info["name"] = latest.name
                info["kb"] = round(latest.stat().st_size / 1024)
                m = re.match(r"blocks-(\d{8})\.sql", latest.name)
                if m:
                    fd = datetime.strptime(m.group(1), "%Y%m%d").date()
                    info["age"] = (today - fd).days
        except Exception:
            pass
        out.append(info)
    return out


@app.get("/settings")
def settings_view(request: Request):
    settings = get_settings()
    with get_conn() as conn:
        cats = conn.execute(
            "SELECT id, name, tone, is_active FROM categories "
            "ORDER BY is_active DESC, display_order"
        ).fetchall()
        wc_map = {
            r["weekday"]: (r["text"] or "")
            for r in conn.execute("SELECT weekday, text FROM weekday_concept")
        }
    weekday_concepts = [
        {"weekday": i, "label": KO_WEEKDAYS[i], "text": wc_map.get(i, "")}
        for i in range(7)
    ]
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "categories": [dict(c) for c in cats],
            "tones": TONES,
            "settings": settings,
            "weekday_concepts": weekday_concepts,
            "day_blocks": [
                {"order": i, "label": lbl, "is_core": core, "start": s, "end": e}
                for i, (lbl, core, s, e) in enumerate(get_day_blocks())
            ],
            "events_calendar_id": gcal_write.events_calendar_id(),
            "gcal_events_on": gcal_write.events_enabled(),
            "sa_email": gcal_write.service_account_email(),
        },
    )


def _data_summary() -> dict:
    """데이터 탭 요약(기록 일수·슬롯 수·기간·미처리 수집함·활성 구분)."""
    with get_conn() as conn:
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
        active_cats = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE is_active = 1"
        ).fetchone()[0]
    return {
        "rec_days": rec_days,
        "slot_recs": slot_recs,
        "first": span[0] or "-",
        "last": span[1] or "-",
        "inbox_open": inbox_open,
        "active_cats": active_cats,
    }


@app.get("/data")
def data_view(request: Request):
    """데이터 탭: 요약·백업·내보내기·삭제(설정에서 분리, 화면 2분할)."""
    return templates.TemplateResponse(
        "data.html",
        {
            "request": request,
            "summary": _data_summary(),
            "backup_status": _backup_status(),
            "today": today_str(),
        },
    )


def _valid_hhmm30(s: str) -> bool:
    """'HH:MM' 이고 분이 00/30, 00:00~24:00 범위인지(30분 슬롯 경계 유지)."""
    if not re.match(r"^\d{2}:\d{2}$", s or ""):
        return False
    h, m = int(s[:2]), int(s[3:5])
    return 0 <= h <= 24 and m in (0, 30) and (h * 60 + m) <= 24 * 60


@app.post("/settings/blocktimes")
async def settings_blocktimes(request: Request):
    """8블록의 시작·끝 시간만 저장한다(라벨·코어여부·개수 고정). 30분 경계·겹침을 검증한다."""
    form = await request.form()
    n = len(DAY_BLOCKS)
    times = []
    prev_end = None
    for i in range(n):
        s = (form.get(f"start_{i}") or "").strip()
        e = (form.get(f"end_{i}") or "").strip()
        label = DAY_BLOCKS[i][0]
        if not _valid_hhmm30(s) or not _valid_hhmm30(e):
            return JSONResponse(
                {"ok": False, "error": f"{label} 시간 형식이 잘못됨(HH:MM, 30분 단위)"},
                status_code=400,
            )
        if hhmm_to_min(s) >= hhmm_to_min(e):
            return JSONResponse(
                {"ok": False, "error": f"{label}: 시작이 끝보다 빨라야 합니다"},
                status_code=400,
            )
        if prev_end is not None and hhmm_to_min(s) < prev_end:
            return JSONResponse(
                {"ok": False, "error": f"{label}이 앞 블록과 겹칩니다"}, status_code=400
            )
        prev_end = hhmm_to_min(e)
        times.append({"start": s, "end": e})
    set_setting(BLOCK_TIMES_KEY, json.dumps(times))
    return JSONResponse({"ok": True})


@app.post("/settings/blocktimes/reset")
async def settings_blocktimes_reset():
    """블록 시간 오버라이드를 지워 기본 시간표로 되돌린다."""
    set_setting(BLOCK_TIMES_KEY, "")
    return JSONResponse({"ok": True})


@app.post("/settings/events-calendar")
async def settings_events_calendar(request: Request):
    """오늘 탭 일정 쓰기용 구글 캘린더 ID를 저장한다(빈 값이면 일정 쓰기 해제)."""
    form = await request.form()
    value = (form.get("value") or "").strip()
    set_setting("gcal_events_calendar_id", value)
    return JSONResponse({"ok": True, "enabled": gcal_write.events_enabled()})


@app.post("/settings/events-calendar/test")
async def settings_events_calendar_test():
    """저장된 일정용 캘린더에 테스트 이벤트를 만들고 지워 연결을 확인한다."""
    return JSONResponse(gcal_write.test_events_write())


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
    allowed = {"start_view", "default_theme", "pomo_auto", "pomo_warn5", "collapse_blocks",
               "show_location", "show_did", "show_reflect"}
    for key in allowed:
        if form.get(key) is not None:
            set_setting(key, form.get(key))
    return JSONResponse({"ok": True})


@app.post("/settings/weekday")
async def settings_weekday(request: Request):
    """요일별 컨셉(0=월~6=일) 한 칸을 저장한다."""
    form = await request.form()
    try:
        wd = int(form.get("weekday"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    if not 0 <= wd <= 6:
        return JSONResponse({"ok": False}, status_code=400)
    text = (form.get("text") or "").strip()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO weekday_concept (weekday, text, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(weekday) DO UPDATE SET text = excluded.text, "
            "updated_at = excluded.updated_at",
            (wd, text, now),
        )
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
    w.writerow(["날짜", "블록", "블록이름", "시각", "구분", "DO(계획)", "한일(실제)",
                "완료", "블록PLAN", "블록SEE"])
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT s.date, b.block_label, b.name AS bname, s.start_time, c.name AS cat, "
            "       s.do_text, s.did_text, s.done, b.plan_text, b.see_text "
            "FROM slots s JOIN blocks b ON b.id = s.block_id "
            "LEFT JOIN categories c ON c.id = s.category_id "
            "WHERE s.date BETWEEN ? AND ? ORDER BY s.date, s.slot_index",
            (start, end),
        ):
            w.writerow([
                r["date"], r["block_label"], r["bname"] or "", r["start_time"],
                r["cat"] or "", r["do_text"] or "", r["did_text"] or "", r["done"],
                r["plan_text"] or "", r["see_text"] or "",
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


# -- 분석 -------------------------------------------------------------------


def _calc_streak(rec_dates: set, today: date) -> int:
    """오늘(기록 없으면 어제)부터 거꾸로 연속으로 기록이 있는 날 수를 센다."""
    if not rec_dates:
        return 0
    cur = today
    if cur.strftime("%Y-%m-%d") not in rec_dates:
        cur = today - timedelta(days=1)
    streak = 0
    while cur.strftime("%Y-%m-%d") in rec_dates:
        streak += 1
        cur = cur - timedelta(days=1)
    return streak


@app.get("/analytics")
def analytics_view(request: Request, rng: str = "7", q: str = ""):
    today = datetime.now(KST).date()
    today_s = today.strftime("%Y-%m-%d")
    with get_conn() as conn:
        if rng == "all":
            row = conn.execute("SELECT MIN(date) FROM slots").fetchone()
            start = row[0] or today_s
            range_label = "전체"
        else:
            rng = "30" if rng == "30" else "7"
            days = int(rng)
            start = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
            range_label = f"최근 {days}일"
        cat_rows = conn.execute(
            "SELECT c.name, c.tone, COUNT(s.id) AS cnt "
            "FROM slots s JOIN categories c ON c.id = s.category_id "
            "WHERE s.date >= ? AND s.date <= ? GROUP BY c.id ORDER BY cnt DESC",
            (start, today_s),
        ).fetchall()
        day_rows = conn.execute(
            "SELECT date, "
            "SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done_cnt, "
            "SUM(CASE WHEN (do_text IS NOT NULL AND TRIM(do_text) != '') "
            "         OR category_id IS NOT NULL OR done = 1 THEN 1 ELSE 0 END) AS planned_cnt "
            "FROM slots WHERE date >= ? AND date <= ? GROUP BY date ORDER BY date",
            (start, today_s),
        ).fetchall()
        pd_rows = conn.execute(
            "SELECT b.date, COUNT(*) AS planned, "
            "SUM(CASE WHEN EXISTS(SELECT 1 FROM slots s WHERE s.block_id = b.id "
            "    AND ((s.do_text IS NOT NULL AND TRIM(s.do_text) != '') OR s.done = 1)) "
            "    THEN 1 ELSE 0 END) AS achieved "
            "FROM blocks b WHERE b.is_core = 1 AND TRIM(COALESCE(b.plan_text, '')) != '' "
            "  AND b.date >= ? AND b.date <= ? GROUP BY b.date ORDER BY b.date",
            (start, today_s),
        ).fetchall()
        rec_dates = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM slots "
                "WHERE (do_text IS NOT NULL AND TRIM(do_text) != '') OR done = 1"
            )
        }
    cat_total = sum(r["cnt"] for r in cat_rows)
    cats = [
        {"name": r["name"], "tone": r["tone"], "hours": r["cnt"] * 0.5,
         "pct": round(r["cnt"] / cat_total * 100) if cat_total else 0}
        for r in cat_rows
    ]
    days_data = [
        {"date": r["date"], "wd": _ko_weekday(r["date"]), "short": _short_date(r["date"]),
         "done": r["done_cnt"], "planned": r["planned_cnt"],
         "pct": round(r["done_cnt"] / r["planned_cnt"] * 100) if r["planned_cnt"] else 0}
        for r in day_rows
    ]
    pd_total_p = sum(r["planned"] for r in pd_rows)
    pd_total_a = sum(r["achieved"] for r in pd_rows)
    pd_data = [
        {"date": r["date"], "short": _short_date(r["date"]),
         "planned": r["planned"], "achieved": r["achieved"],
         "pct": round(r["achieved"] / r["planned"] * 100) if r["planned"] else 0}
        for r in pd_rows
    ]
    summary = {
        "streak": _calc_streak(rec_dates, today),
        "rec_days": len(days_data),
        "total_hours": round(sum(c["hours"] for c in cats), 1),
        "avg_done": round(sum(d["pct"] for d in days_data) / len(days_data)) if days_data else 0,
        "pd_pct": round(pd_total_a / pd_total_p * 100) if pd_total_p else 0,
    }
    # 분석·검색 병합: 검색어가 있으면 지난 슬롯/블록 기록을 같은 화면에서 함께 보여준다.
    q = (q or "").strip()
    s_slots, s_blocks = _search_records(q)
    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "rng": rng,
            "range_label": range_label,
            "start": start,
            "end": today_s,
            "cats": cats,
            "days_data": days_data,
            "pd_data": pd_data,
            "summary": summary,
            "q": q,
            "s_slots": s_slots,
            "s_blocks": s_blocks,
        },
    )


# -- 기록 검색 (분석·검색 탭에 병합) ---------------------------------------


def _search_records(q: str):
    """슬롯 DO·한일과 블록 PLAN·SEE·이름을 날짜를 가로질러 찾아 (slots, blocks) 반환."""
    q = (q or "").strip()
    if not q:
        return [], []
    like = f"%{q}%"
    with get_conn() as conn:
        slots = [
            dict(r)
            for r in conn.execute(
                "SELECT s.date, s.start_time, b.block_order, b.block_label, "
                "       s.do_text, s.did_text "
                "FROM slots s JOIN blocks b ON b.id = s.block_id "
                "WHERE s.do_text LIKE ? OR s.did_text LIKE ? "
                "ORDER BY s.date DESC, s.slot_index LIMIT 300",
                (like, like),
            )
        ]
        blocks = [
            dict(r)
            for r in conn.execute(
                "SELECT date, block_order, block_label, name, plan_text, see_text "
                "FROM blocks "
                "WHERE plan_text LIKE ? OR see_text LIKE ? OR name LIKE ? "
                "ORDER BY date DESC, block_order LIMIT 300",
                (like, like, like),
            )
        ]
    return slots, blocks


@app.get("/search")
def search_view(q: str = ""):
    """과거 호환: 검색은 분석·검색(/analytics) 탭으로 이동했다."""
    target = "/analytics?q=" + urllib.parse.quote((q or "").strip()) if q else "/analytics"
    return RedirectResponse(url=target)


# -- 고결감 (반복 고민·결정·감사) ------------------------------------------

REFLECT_KINDS = ("고민", "결정", "감사")


def _reflect_title(title, text) -> str:
    """제목이 비면 내용 첫 줄에서 만든다(구글 summary가 비지 않게)."""
    t = (title or "").strip()
    if t:
        return t
    return ((text or "").strip().splitlines() or [""])[0][:120]


def _import_gcal_reflections():
    """고결감 캘린더에서 로컬에 없는 이벤트(구글에서 직접 만든 것)를 reflection으로 가져온다."""
    if not gcal_write.enabled():
        return
    today = datetime.now(KST).date()
    try:
        evs = gcal_write.list_reflection_events(
            today - timedelta(days=730), today + timedelta(days=730)
        )
    except Exception:
        return
    if not evs:
        return
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        existing = {
            r[0] for r in conn.execute(
                "SELECT gcal_event_id FROM reflection WHERE gcal_event_id IS NOT NULL"
            )
        }
        for ev in evs:
            if ev["id"] in existing:
                continue
            conn.execute(
                "INSERT INTO reflection (kind, title, text, tags, event_date, "
                "review_date, created_at, gcal_event_id, synced) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (ev["kind"], ev["title"], ev["content"], ev["tags"], ev["date"],
                 None, now, ev["id"]),
            )


@app.get("/reflect")
def reflect_view(request: Request, q: str = "", kind: str = ""):
    _import_gcal_reflections()  # 구글 캘린더에서 만든 것도 탭에 보이게(양방향)
    q = (q or "").strip()
    kind = kind if kind in REFLECT_KINDS else ""
    where: list[str] = []
    params: list = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if q:
        where.append("(title LIKE ? OR text LIKE ? OR tags LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql = "SELECT * FROM reflection"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY event_date DESC, id DESC LIMIT 500"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return templates.TemplateResponse(
        "reflect.html",
        {
            "request": request,
            "items": [dict(r) for r in rows],
            "kinds": REFLECT_KINDS,
            "q": q,
            "kind": kind,
            "today": today_str(),
            "gcal_write_on": gcal_write.enabled(),
        },
    )


@app.post("/reflect/add")
async def reflect_add(request: Request):
    form = await request.form()
    kind = form.get("kind") if form.get("kind") in REFLECT_KINDS else "고민"
    title = (form.get("title") or "").strip()
    text = (form.get("text") or "").strip()                     # 내용
    tags = (form.get("tags") or "").strip()
    event_date = today_str()                                    # 기록일은 자동(오늘)
    review_date = (form.get("review_date") or "").strip() or None  # 입력할 때만 저장
    if not title and not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    title = _reflect_title(title, text)
    now = datetime.now(KST).isoformat(timespec="seconds")
    # 캘린더 일정은 다시 볼 날짜가 있으면 그날, 없으면 기록일에 올린다.
    cal_date = review_date or event_date
    # 반영을 시도하되, 실패해도 DB에는 저장해 나중에 재시도할 수 있게 한다.
    # 제목→구글 summary, 내용→description.
    try:
        event_id = gcal_write.create_event(kind, title, text, tags, cal_date)
    except Exception:
        event_id = None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reflection (kind, title, text, tags, event_date, review_date, "
            "created_at, gcal_event_id, synced) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, title, text, tags, event_date, review_date, now, event_id,
             1 if event_id else 0),
        )
        new_id = cur.lastrowid
    return JSONResponse({"ok": True, "id": new_id, "synced": bool(event_id)})


@app.post("/reflect/sync/{item_id}")
def reflect_sync(item_id: int):
    """캘린더 반영에 실패했던 항목을 다시 시도한다."""
    event_id = None
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM reflection WHERE id = ?", (item_id,)).fetchone()
        if not r:
            return JSONResponse({"ok": False}, status_code=404)
        if r["synced"] and r["gcal_event_id"]:
            return JSONResponse({"ok": True, "synced": True})
        cal_date = r["review_date"] or r["event_date"]
        title = _reflect_title(r["title"], r["text"])
        try:
            event_id = gcal_write.create_event(
                r["kind"], title, r["text"] or "", r["tags"] or "", cal_date
            )
        except Exception:
            event_id = None
        if event_id:
            conn.execute(
                "UPDATE reflection SET gcal_event_id = ?, synced = 1 WHERE id = ?",
                (event_id, item_id),
            )
    return JSONResponse({"ok": bool(event_id), "synced": bool(event_id)})


@app.post("/reflect/delete/{item_id}")
def reflect_delete(item_id: int):
    """기록을 삭제하고, 캘린더 이벤트가 있으면 함께 지운다."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT gcal_event_id FROM reflection WHERE id = ?", (item_id,)
        ).fetchone()
        if r and r["gcal_event_id"]:
            try:
                gcal_write.delete_event(r["gcal_event_id"])
            except Exception:
                pass
        conn.execute("DELETE FROM reflection WHERE id = ?", (item_id,))
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
    return {
        "gcal": gcal.status(),
        "gcal_write": gcal_write.status(),
        "things": things.status(),
    }


@app.get("/api/now")
def api_now():
    """클라이언트가 서버 시각 기준으로 포모도로 정렬할 수 있게 KST를 반환."""
    n = datetime.now(KST)
    return {"iso": n.isoformat(timespec="seconds"), "epoch_ms": int(n.timestamp() * 1000)}
