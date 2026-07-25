# 오늘/주간 입력과 PWA 서빙, 포모도로 정적 자원을 제공하는 FastAPI 메인 애플리케이션
import hashlib
import json
import os
import re
import signal
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
    BLOCK_TIMES_WD_KEY,
    get_conn,
    get_day_blocks,
    get_settings,
    get_weekday_overrides,
    init_db,
    set_setting,
)
from app.integrations import ai, gcal, gcal_write, things

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


def _weekday_of(date_str: str) -> int:
    """'YYYY-MM-DD'의 요일(0=월 ~ 6=일). 요일별 세션 시간을 고르는 데 쓴다."""
    return datetime.strptime(date_str, "%Y-%m-%d").date().weekday()


def _skeleton_matches_config(conn, date_str: str) -> bool:
    """DB의 그날 블록 골격이 현재 효과적 설정(요일별 시간 편집 반영)과 정확히 같은지."""
    have = [
        (r["block_label"], r["start_time"], r["end_time"])
        for r in conn.execute(
            "SELECT block_label, start_time, end_time FROM blocks "
            "WHERE date = ? ORDER BY block_order",
            (date_str,),
        )
    ]
    want = [
        (label, start, end)
        for (label, _core, start, end) in get_day_blocks(_weekday_of(date_str))
    ]
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
            # 점심·저녁 버퍼 블록은 기본 구분이 '기타'로 자동 세팅되므로 사용자 내용이 아니다.
            # 코어 블록의 구분만 사용자 내용으로 친다(안 그러면 모든 날이 '내용 있음'이 돼
            # 세션 시간 변경이 빈 날에도 반영되지 않는다).
            "OR (is_core = 1 AND category_id IS NOT NULL) OR TRIM(COALESCE(name,'')) != '' "
            # 장소만 지정해 둔 날도 사용자 입력이다(빠지면 시간 변경 시 골격 재생성으로 유실된다).
            "OR TRIM(COALESCE(location,'')) != '') LIMIT 1",
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
    day_blocks = get_day_blocks(_weekday_of(date_str))
    # 점심·저녁 버퍼 블록은 기본 구분을 '기타'로 시드해 시간 분포 통계에 잡히게 한다.
    etc_row = conn.execute(
        "SELECT id FROM categories WHERE name = '기타' LIMIT 1"
    ).fetchone()
    etc_id = etc_row["id"] if etc_row else None
    default_cat = {"점심": etc_id, "저녁": etc_id}
    block_ids = {}
    for order, (label, is_core, start, end) in enumerate(day_blocks):
        cur = conn.execute(
            """
            INSERT INTO blocks (date, block_order, block_label, is_core,
                                start_time, end_time, category_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (date_str, order, label, 1 if is_core else 0, start, end,
             default_cat.get(label), now),
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
    """폼의 prefix1/2/3 값을 줄바꿈으로 합친다. 각 칸 내부의 줄바꿈은 공백으로 눌러
    3칸 구분(줄바꿈)이 깨지지 않게 한다. 모두 비면 빈 문자열."""
    vals = [
        (form.get(f"{prefix}{i}", "") or "").replace("\r", " ").replace("\n", " ").strip()
        for i in (1, 2, 3)
    ]
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
            "SELECT id, kind, title, text, tags, event_date, review_note FROM reflection "
            "WHERE (review_date = ? AND source_id IS NULL) "
            "   OR (source_id IS NOT NULL AND event_date = ?) "
            "ORDER BY id DESC",
            (date_str, date_str),
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

    # 하루 마감 요약: 완료·기록 슬롯 수와 코어 블록 계획→실행 달성.
    done_slots = sum(1 for s in slots if s["done"])
    recorded_slots = sum(
        1 for s in slots
        if (s["do_text"] or "").strip() or (s["did_text"] or "").strip()
        or s["category_id"] or s["done"]
    )
    core_planned = core_achieved = 0
    for b in blocks:
        if b["is_core"] and (b["plan_text"] or "").strip():
            core_planned += 1
            if any((s["do_text"] or "").strip() or s["done"]
                   for s in slots_by_block.get(b["id"], [])):
                core_achieved += 1
    day_stats = {
        "done": done_slots, "recorded": recorded_slots,
        "core_planned": core_planned, "core_achieved": core_achieved,
    }

    # 외부 연동: Things3 Today + 구글 캘린더 일정.
    # 전체 목록은 최상단에 1번만 줄바꿈으로 노출(cal_events, task_list),
    # 시각이 있는 항목만 해당 시간 블록의 아젠다로 배치한다.
    cal_events, task_list, block_events = _day_agenda(blocks, d, is_today)

    # 오늘 목표/달성/감사·반성을 각각 3개로 분리(줄바꿈 저장, 레거시 1줄도 호환).
    goals = _split3(meta["today_goal"] if meta else "")
    plans = _split3(meta["daily_plan"] if meta else "")
    grats = _split3(meta["gratitude"] if meta else "")
    # 각 3줄에 직접 입력한 자유 태그도 같은 방식으로 3칸으로 분리.
    goal_tags = _split3(meta["goal_tags"] if meta else "")
    plan_tags = _split3(meta["plan_tags"] if meta else "")
    grat_tags = _split3(meta["grat_tags"] if meta else "")

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
            "grats": grats,
            "goal_tags": goal_tags,
            "plan_tags": plan_tags,
            "grat_tags": grat_tags,
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
            "day_stats": day_stats,
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
            INSERT INTO daily_meta (date, today_goal, daily_plan, memo, vow, gratitude,
                                    goal_tags, plan_tags, grat_tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                today_goal = excluded.today_goal,
                daily_plan = excluded.daily_plan,
                memo = excluded.memo,
                vow = excluded.vow,
                gratitude = excluded.gratitude,
                goal_tags = excluded.goal_tags,
                plan_tags = excluded.plan_tags,
                grat_tags = excluded.grat_tags
            """,
            (
                date_str,
                _join3(form, "goal"),
                _join3(form, "dplan"),
                form.get("memo", ""),
                form.get("vow", ""),
                _join3(form, "grat"),
                _join3(form, "goaltag"),
                _join3(form, "plantag"),
                _join3(form, "grattag"),
            ),
        )
    # 저장 후: 오늘 달성 3줄을 '성과' 캘린더에 종일 이벤트로 자동 반영(설명란 1. 2. 3.).
    # 캘린더 I/O는 DB 잠금 밖에서 하고, 실패해도 저장은 그대로 둔다.
    if gcal_write.achieve_enabled():
        try:
            items = [(form.get(f"dplan{i}", "") or "").strip() for i in (1, 2, 3)]
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT achieve_event_id FROM daily_meta WHERE date = ?", (date_str,)
                ).fetchone()
            existing = row["achieve_event_id"] if row else None
            new_id = gcal_write.upsert_achievement_event(date_str, items, existing)
            if new_id != existing:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE daily_meta SET achieve_event_id = ? WHERE date = ?",
                        (new_id, date_str),
                    )
        except Exception:
            pass
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

    # 달성(dplan) 자동저장이면 저장 뒤 성과 캘린더에 반영할 날짜를 담는다(없으면 None).
    achieve_date = None
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
            elif field.startswith("goaltag") or field.startswith("plantag") or field.startswith("grattag"):
                # 목표/달성/감사 각 줄의 자유 태그 3칸(직접 입력). 바뀐 칸과 그룹 나머지 값을 함께 받아
                # 합친다. 클라이언트는 prefix+번호(goaltag1) 또는 숫자 키(1/2/3)로 보낼 수 있어 둘 다 받는다.
                if field.startswith("goaltag"):
                    prefix, col = "goaltag", "goal_tags"
                elif field.startswith("plantag"):
                    prefix, col = "plantag", "plan_tags"
                else:
                    prefix, col = "grattag", "grat_tags"
                existing = conn.execute(
                    f"SELECT {col} FROM daily_meta WHERE date = ?", (date_str,)
                ).fetchone()
                parts = (existing[col] if existing and existing[col] else "").split("\n") if existing else []
                parts = (parts + ["", "", ""])[:3]
                for i in range(3):
                    pre_key, num_key = f"{prefix}{i + 1}", str(i + 1)
                    if pre_key in form:
                        raw = form.get(pre_key, "") or ""
                    elif num_key in form:
                        raw = form.get(num_key, "") or ""
                    else:
                        continue
                    parts[i] = raw.replace("\r", " ").replace("\n", " ").strip()
                joined = "\n".join(parts)
                joined = joined if joined.strip() else ""
                conn.execute(
                    "INSERT INTO daily_meta (date, %s) VALUES (?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET %s = excluded.%s"
                    % (col, col, col),
                    (date_str, joined),
                )
            elif field.startswith("goal") or field.startswith("dplan") or field.startswith("grat"):
                # 목표/달성/감사·반성 3칸: 바뀐 한 칸과 나머지 두 칸(클라이언트가 함께 보냄)을
                # 합쳐 줄바꿈으로 저장한다. 클라이언트는 세 값을 숫자 키(1/2/3)로, 폼 전체 저장 등은
                # prefix+번호 키로 보낼 수 있어 둘 다 받는다. 각 칸 내부 줄바꿈은 공백으로 눌러 3칸 구분 보호.
                if field.startswith("goal"):
                    prefix, col = "goal", "today_goal"
                elif field.startswith("dplan"):
                    prefix, col = "dplan", "daily_plan"
                else:
                    prefix, col = "grat", "gratitude"
                existing = conn.execute(
                    f"SELECT {col} FROM daily_meta WHERE date = ?", (date_str,)
                ).fetchone()
                parts = (existing[col] if existing and existing[col] else "").split("\n") if existing else []
                parts = (parts + ["", "", ""])[:3]
                for i in range(3):
                    pre_key, num_key = f"{prefix}{i+1}", str(i + 1)
                    if pre_key in form:
                        raw = form.get(pre_key, "") or ""
                    elif num_key in form:
                        raw = form.get(num_key, "") or ""
                    else:
                        continue
                    parts[i] = raw.replace("\r", " ").replace("\n", " ")
                joined = "\n".join(p.strip() for p in parts)
                joined = joined if joined.strip() else ""
                conn.execute(
                    "INSERT INTO daily_meta (date, %s) VALUES (?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET %s = excluded.%s"
                    % (col, col, col),
                    (date_str, joined),
                )
                if prefix == "dplan":
                    achieve_date = date_str  # 달성이 바뀌었으니 저장 후 성과 캘린더 반영
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
    # 달성 자동저장이면 성과 캘린더에도 즉시 반영한다(저장 버튼 없이도 최신 유지). DB 잠금 밖에서 I/O.
    if achieve_date and gcal_write.achieve_enabled():
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT daily_plan, achieve_event_id FROM daily_meta WHERE date = ?",
                    (achieve_date,),
                ).fetchone()
            items = (row["daily_plan"] or "").split("\n") if row else []
            existing = row["achieve_event_id"] if row else None
            new_id = gcal_write.upsert_achievement_event(achieve_date, items, existing)
            if new_id != existing:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE daily_meta SET achieve_event_id = ? WHERE date = ?",
                        (new_id, achieve_date),
                    )
        except Exception:
            pass
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


INBOX_STATUSES = {"", "next", "wait", "someday", "ref"}


@app.post("/inbox/status")
async def inbox_status(request: Request):
    """수집함 항목의 GTD 상태(미분류/다음행동/대기/언젠가/참고)를 저장한다(주간 정리 단계)."""
    form = await request.form()
    try:
        item_id = int(form.get("item_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    status = (form.get("status") or "").strip()
    if status not in INBOX_STATUSES:
        return JSONResponse({"ok": False, "error": "bad-status"}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE inbox SET status = ? WHERE id = ?", (status, item_id))
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


@app.post("/block/rollover")
async def block_rollover(request: Request):
    """이 블록의 PLAN을 다음 날 같은 블록 PLAN 끝에 복사한다(미룬 계획 이월)."""
    form = await request.form()
    try:
        block_id = int(form.get("block_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        src = conn.execute(
            "SELECT date, block_label, plan_text FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        if not src:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        plan = (src["plan_text"] or "").strip()
        if not plan:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        d = datetime.strptime(src["date"], "%Y-%m-%d").date()
        nxt = (d + timedelta(days=1)).strftime("%Y-%m-%d")
        ensure_day_skeleton(conn, nxt)
        dst = conn.execute(
            "SELECT id, plan_text FROM blocks WHERE date = ? AND block_label = ?",
            (nxt, src["block_label"]),
        ).fetchone()
        if not dst:
            return JSONResponse({"ok": False, "error": "no-target"}, status_code=404)
        cur = (dst["plan_text"] or "").rstrip()
        new_plan = f"{cur}\n{plan}" if cur else plan
        conn.execute(
            "UPDATE blocks SET plan_text = ?, updated_at = ? WHERE id = ?",
            (new_plan, now, dst["id"]),
        )
    return JSONResponse({"ok": True, "date": nxt, "label": src["block_label"]})


@app.post("/meta/tomorrow-goal")
async def meta_tomorrow_goal(request: Request):
    """하루 마감에서 적은 '내일 가장 중요한 일'을 다음 날 목표 1번에 저장한다."""
    form = await request.form()
    date_str = (form.get("date") or "").strip()
    text = (form.get("text") or "").strip()
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
    nxt = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT today_goal FROM daily_meta WHERE date = ?", (nxt,)
        ).fetchone()
        base = (row["today_goal"] if row and row["today_goal"] else "")
        parts = (base.split("\n") + ["", "", ""])[:3]
        parts[0] = text
        joined = "\n".join(p.strip() for p in parts)
        joined = joined if joined.strip() else ""
        conn.execute(
            "INSERT INTO daily_meta (date, today_goal) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET today_goal = excluded.today_goal",
            (nxt, joined),
        )
    return JSONResponse({"ok": True, "date": nxt})


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
                    "tags": t.get("tags", []),
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
        # 슬롯 구분이 비면(NULL) 그 슬롯이 속한 블록 구분을 따른다(블록→슬롯 상속).
        cat_summary = conn.execute(
            f"""
            SELECT c.name, c.color, c.tone, COUNT(s.id) AS slot_count
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


@app.post("/week/apply-template")
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


# -- 자동 세분화 (규칙기반 기본 + 선택적 AI) --------------------------------


def _child_periods(level: str, period_key: str):
    """상위 (level, period_key)의 바로 아래 단위와 자식 기간 목록.

    반환 (child_level, [(period_key, label), ...]). 세분화 불가면 (None, []).
    """
    try:
        if level == "year":
            y = int(period_key)
            return "quarter", [(f"{y}-Q{q}", f"{q}분기") for q in range(1, 5)]
        if level == "quarter":
            ys, qs = period_key.split("-Q")
            y, q = int(ys), int(qs)
            m0 = (q - 1) * 3 + 1
            return "month", [(f"{y}-{m:02d}", f"{m}월") for m in range(m0, m0 + 3)]
        if level == "month":
            y, m = (int(x) for x in period_key.split("-"))
            first = date(y, m, 1)
            last = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
            monday = first - timedelta(days=first.weekday())
            out = []
            while monday <= last:
                out.append((monday.strftime("%Y-%m-%d"), f"{monday.month}/{monday.day} 주"))
                monday += timedelta(days=7)
            return "week", out
    except (ValueError, AttributeError):
        return None, []
    return None, []


def _child_anchor(level: str, period_key: str) -> str:
    """세분화 후 이동할 자식 단위 화면의 anchor(날짜 문자열)."""
    if level == "year":
        return f"{period_key}-01-01"
    if level == "quarter":
        ys, qs = period_key.split("-Q")
        return f"{int(ys)}-{(int(qs) - 1) * 3 + 1:02d}-01"
    return f"{period_key}-01"  # month → 그 달 1일


def _rule_distribute(parent_text: str, n: int) -> list[str]:
    """부모 텍스트를 자식 n개 내용으로 나눈다. 여러 줄이면 분배, 한 줄이면 참고로 복제."""
    lines = [ln.strip() for ln in (parent_text or "").splitlines() if ln.strip()]
    if not lines:
        return [""] * n
    if len(lines) == 1:
        return [lines[0]] * n
    buckets: list[list[str]] = [[] for _ in range(n)]
    for i, ln in enumerate(lines):
        buckets[i % n].append(ln)
    return ["\n".join(b) for b in buckets]


def _ai_split(parent_text: str, labels: list[str], area_name: str,
              parent_label: str) -> list[str] | None:
    """AI로 상위 계획을 각 자식 기간(labels)별 내용으로 나눈다. 실패·미설정 시 None."""
    n = len(labels)
    system = ("당신은 개인 시간관리 코치입니다. 상위 계획을 하위 기간별 구체적 "
              "실행 항목으로 나눕니다. 한국어로 간결하게 답합니다.")
    user = (
        f"영역: {area_name or '(없음)'}\n"
        f"상위({parent_label}) 계획:\n{parent_text}\n\n"
        f"이 계획을 다음 {n}개 기간에 나눠, 각 기간에 할 구체적 항목 1~3개를 "
        f"제시하세요: {', '.join(labels)}.\n"
        f"반드시 길이 {n}의 JSON 문자열 배열만 출력하세요. "
        "각 원소는 그 기간 내용이며 여러 항목은 줄바꿈으로 구분합니다."
    )
    reply = ai.complete(system, user, max_tokens=800, temperature=0.5)
    if not reply:
        return None
    try:
        arr = json.loads(reply[reply.index("["):reply.rindex("]") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(arr, list) or not arr:
        return None
    arr = [str(x).strip() for x in arr]
    return (arr + [""] * n)[:n]


@app.post("/week/decompose-themes")
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
        contents = _ai_split(context, CORE_LABELS, "", "주") if ai.enabled() else None
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


@app.post("/plan/item/add")
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


@app.post("/plan/item/update")
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


@app.post("/plan/item/delete")
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


@app.get("/plan")
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


@app.post("/plan/decompose")
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
            _ai_split(parent_text, labels, area_name, PLAN_LEVEL_LABELS[level])
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


def _load_cat_templates(conn) -> list[dict]:
    """구분 템플릿 목록을 셀(요일 0~6 × 코어블록 → 구분)까지 채워 돌려준다."""
    templates_ = [
        dict(r)
        for r in conn.execute(
            "SELECT id, name, display_order FROM cat_template "
            "ORDER BY display_order, id"
        )
    ]
    cmap: dict[int, dict[int, dict[str, int]]] = {}
    for r in conn.execute(
        "SELECT template_id, weekday, block_label, category_id FROM cat_template_cell"
    ):
        cmap.setdefault(r["template_id"], {}).setdefault(r["weekday"], {})[
            r["block_label"]
        ] = r["category_id"]
    for t in templates_:
        t["cells"] = cmap.get(t["id"], {})
    return templates_


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
        cat_templates = _load_cat_templates(conn)
    weekday_concepts = [
        {"weekday": i, "label": KO_WEEKDAYS[i], "text": wc_map.get(i, "")}
        for i in range(7)
    ]
    active_categories = [dict(c) for c in cats if c["is_active"]]
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "categories": [dict(c) for c in cats],
            "active_categories": active_categories,
            "cat_templates": cat_templates,
            "core_labels": CORE_LABELS,
            "weekdays": list(enumerate(KO_WEEKDAYS)),
            "tones": TONES,
            "settings": settings,
            "weekday_concepts": weekday_concepts,
            "block_scopes": _block_scopes(),
            "events_calendar_id": gcal_write.events_calendar_id(),
            "gcal_events_on": gcal_write.events_enabled(),
            "achieve_calendar_id": gcal_write.achieve_calendar_id(),
            "gcal_achieve_on": gcal_write.achieve_enabled(),
            "sa_email": gcal_write.service_account_email(),
            "ai_status": ai.status(),
            "env_path": str(_env_file_path()),
            "env_content": _read_env_text(),
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


def _block_scopes() -> list[dict]:
    """세션 시간 편집 범위 8개(공통 + 월~일). 덮어쓰지 않은 요일은 공통 값을 그대로 보여준다."""
    overrides = get_weekday_overrides()
    scopes = [{"key": "", "label": "공통", "sub": "모든 요일 기본", "overridden": False}]
    for i in range(7):
        scopes.append({
            "key": str(i), "label": KO_WEEKDAYS[i], "sub": f"{KO_WEEKDAYS[i]}요일",
            "overridden": bool(overrides.get(str(i))),
        })
    for sc in scopes:
        blocks = get_day_blocks(int(sc["key"]) if sc["key"] else None)
        sc["rows"] = [
            {"order": i, "label": lbl, "is_core": core, "start": s, "end": e}
            for i, (lbl, core, s, e) in enumerate(blocks)
        ]
    return scopes


def _valid_hhmm(s: str) -> bool:
    """'HH:MM' 이고 00:00~24:00 범위인지. 분은 자유 — 세션 30분 단위는 블록 길이(30분 배수)로 보장한다."""
    if not re.match(r"^\d{2}:\d{2}$", s or ""):
        return False
    h, m = int(s[:2]), int(s[3:5])
    return 0 <= h <= 24 and 0 <= m <= 59 and (h * 60 + m) <= 24 * 60


def _parse_scope(raw) -> tuple[bool, int | None]:
    """세션 시간 편집 범위 입력값을 (유효한가, 요일 또는 None) 으로. ''=공통, '0'~'6'=요일."""
    s = (raw or "").strip()
    if not s:
        return True, None
    if s.isdigit() and 0 <= int(s) <= 6:
        return True, int(s)
    return False, None


@app.post("/settings/blocktimes")
async def settings_blocktimes(request: Request):
    """8블록의 시작·끝 시간을 저장한다(라벨·코어여부·개수 고정). 30분 경계·겹침을 검증한다.

    scope 가 비면 공통(모든 요일 기본), '0'~'6' 이면 그 요일만 덮어쓴다.
    """
    form = await request.form()
    ok_scope, weekday = _parse_scope(form.get("scope"))
    if not ok_scope:
        return JSONResponse({"ok": False, "error": "요일 값이 잘못됨"}, status_code=400)
    n = len(DAY_BLOCKS)
    times = []
    prev_end = None
    for i in range(n):
        s = (form.get(f"start_{i}") or "").strip()
        e = (form.get(f"end_{i}") or "").strip()
        label = DAY_BLOCKS[i][0]
        if not _valid_hhmm(s) or not _valid_hhmm(e):
            return JSONResponse(
                {"ok": False, "error": f"{label} 시간 형식이 잘못됨(HH:MM)"},
                status_code=400,
            )
        if hhmm_to_min(s) >= hhmm_to_min(e):
            return JSONResponse(
                {"ok": False, "error": f"{label}: 시작이 끝보다 빨라야 합니다"},
                status_code=400,
            )
        if (hhmm_to_min(e) - hhmm_to_min(s)) % 30 != 0:
            return JSONResponse(
                {"ok": False, "error": f"{label}: 블록 길이가 30분 단위여야 합니다(세션 30분 유지)"},
                status_code=400,
            )
        if prev_end is not None and hhmm_to_min(s) < prev_end:
            return JSONResponse(
                {"ok": False, "error": f"{label}이 앞 블록과 겹칩니다"}, status_code=400
            )
        prev_end = hhmm_to_min(e)
        times.append({"start": s, "end": e})
    if weekday is None:
        set_setting(BLOCK_TIMES_KEY, json.dumps(times))
    else:
        overrides = get_weekday_overrides()
        overrides[str(weekday)] = times
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))
    return JSONResponse({"ok": True, "scope": "" if weekday is None else str(weekday)})


@app.post("/settings/blocktimes/reset")
async def settings_blocktimes_reset(request: Request):
    """공통은 기본 시간표로, 요일은 덮어쓰기를 지워 공통을 따르게 되돌린다."""
    form = await request.form()
    ok_scope, weekday = _parse_scope(form.get("scope"))
    if not ok_scope:
        return JSONResponse({"ok": False, "error": "요일 값이 잘못됨"}, status_code=400)
    if weekday is None:
        set_setting(BLOCK_TIMES_KEY, "")
    else:
        overrides = get_weekday_overrides()
        overrides.pop(str(weekday), None)
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))
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


@app.post("/settings/achieve-calendar")
async def settings_achieve_calendar(request: Request):
    """오늘 '달성'을 쓸 성과 캘린더 ID를 저장한다(빈 값이면 성과 쓰기 해제)."""
    form = await request.form()
    value = (form.get("value") or "").strip()
    set_setting("gcal_achieve_calendar_id", value)
    return JSONResponse({"ok": True, "enabled": gcal_write.achieve_enabled()})


@app.post("/settings/achieve-calendar/test")
async def settings_achieve_calendar_test():
    """저장된 성과 캘린더에 테스트 이벤트를 만들고 지워 연결을 확인한다."""
    return JSONResponse(gcal_write.test_achieve_write())


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


# -- 구분 템플릿 (설정 탭) --------------------------------------------------

@app.post("/settings/template/add")
async def settings_template_add(request: Request):
    """새 구분 템플릿을 빈 상태로 추가하고 생성된 id를 돌려준다."""
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "no-name"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        order = conn.execute(
            "SELECT COALESCE(MAX(display_order), -1) + 1 FROM cat_template"
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO cat_template (name, display_order, updated_at) "
            "VALUES (?, ?, ?)",
            (name, order, now),
        )
    return JSONResponse({"ok": True, "id": cur.lastrowid})


@app.post("/settings/template/rename")
async def settings_template_rename(request: Request):
    """구분 템플릿 이름을 바꾼다."""
    form = await request.form()
    try:
        tid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "no-name"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "UPDATE cat_template SET name = ?, updated_at = ? WHERE id = ?",
            (name, now, tid),
        )
    return JSONResponse({"ok": True})


@app.post("/settings/template/delete")
async def settings_template_delete(request: Request):
    """구분 템플릿과 그 셀을 함께 삭제한다."""
    form = await request.form()
    try:
        tid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    with get_conn() as conn:
        conn.execute("DELETE FROM cat_template WHERE id = ?", (tid,))
    return JSONResponse({"ok": True})


@app.post("/settings/template/cell")
async def settings_template_cell(request: Request):
    """템플릿 한 칸(요일 0~6 × 코어블록)의 구분을 저장한다. 값이 비면 미지정."""
    form = await request.form()
    try:
        tid = int(form.get("template_id"))
        weekday = int(form.get("weekday"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    label = (form.get("block_label") or "").strip()
    if not (0 <= weekday <= 6) or label not in CORE_LABELS:
        return JSONResponse({"ok": False, "error": "bad-cell"}, status_code=400)
    raw = form.get("category_id")
    cid = int(raw) if raw else None
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cat_template_cell "
            "(template_id, weekday, block_label, category_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(template_id, weekday, block_label) DO UPDATE SET "
            "category_id = excluded.category_id",
            (tid, weekday, label, cid),
        )
    return JSONResponse({"ok": True})


# -- .env 편집 (설정 탭) ----------------------------------------------------

# 설정 탭의 .env 편집기가 프로젝트 루트 .env를 그대로 보여주고 저장한다. 값은 서버 재시작
# 후 반영된다(config.py가 기동 시 load_dotenv). 개인용(테일스케일 내부) 단일 사용자 전제라
# 시크릿을 화면엔 보여주되 서버 로그에는 남기지 않고, 백업은 레포 밖(6block-data)에 둔다.
def _env_file_path() -> Path:
    """프로젝트 루트의 .env 경로."""
    return BASE_DIR.parent / ".env"


def _read_env_text() -> str:
    """.env 내용을 문자열로 읽는다(없으면 빈 문자열)."""
    try:
        return _env_file_path().read_text(encoding="utf-8")
    except OSError:
        return ""


@app.post("/settings/env/save")
async def settings_env_save(request: Request):
    """.env 전체 내용을 저장한다. 직전 내용을 6block-data에 백업하고 임시파일로 원자적
    교체하며 권한 0o600을 유지한다. 저장 후 서버를 재시작해야 값이 반영된다."""
    form = await request.form()
    content = form.get("content")
    if content is None:
        return JSONResponse({"ok": False, "error": "no-content"}, status_code=400)
    if len(content) > 100_000:
        return JSONResponse({"ok": False, "error": "too-large"}, status_code=400)
    text = content.replace("\r\n", "\n")
    env_path = _env_file_path()
    tmp = env_path.with_name(".env.tmp")
    try:
        if env_path.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            bak = BACKUP_DIR / ".env.bak"       # 레포 밖(6block-data/backups)에 백업
            bak.write_bytes(env_path.read_bytes())
            os.chmod(bak, 0o600)
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, env_path)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


# -- 서버 재시작 (launchd) --------------------------------------------------

# 이 앱은 launchd 서비스(io.6block.uvicorn, KeepAlive)로 상시 구동된다. 설정의 재시작
# 버튼이 이 엔드포인트를 부르면 응답을 먼저 돌려준 뒤 약 1초 뒤 자기 자신에게 SIGTERM을
# 보낸다. 정상 종료(SIGKILL 아님)라 SQLite 연결·WAL이 깨끗이 닫히고, launchd가 KeepAlive로
# 새 프로세스를 띄워 코드·.env 변경을 반영한다.
@app.post("/settings/restart")
async def settings_restart():
    """이 서버를 재시작한다(응답 후 SIGTERM 자기 종료 → launchd가 KeepAlive로 재기동)."""
    threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return JSONResponse({"ok": True})


# -- AI 연결 (선택) --------------------------------------------------------


@app.post("/settings/ai/save")
async def settings_ai_save(request: Request):
    """AI 연결의 base URL·모델을 저장한다(키는 보안상 .env AI_API_KEY로만 관리)."""
    form = await request.form()
    set_setting("ai_base_url", (form.get("base_url") or "").strip())
    set_setting("ai_model", (form.get("model") or "").strip())
    return JSONResponse({"ok": True, "status": ai.status()})


@app.post("/settings/ai/test")
async def settings_ai_test():
    """현재 설정으로 AI에 짧은 호출을 보내 연결을 확인한다."""
    if not ai.enabled():
        return JSONResponse(
            {"ok": False, "error": ".env의 AI_API_KEY와 base URL·모델을 확인하세요"}
        )
    reply = ai.complete("You reply with a single word.",
                        "Reply with the word OK.", max_tokens=5, temperature=0)
    if reply:
        return JSONResponse({"ok": True, "reply": reply[:40]})
    return JSONResponse(
        {"ok": False, "error": "호출 실패 · 키·주소·모델·잔액을 확인하세요"}
    )


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
            "LEFT JOIN categories c ON c.id = COALESCE(s.category_id, b.category_id) "
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


def _on_this_day(today: date):
    """예전 오늘(어제·1주·한 달·1년 전)의 한 일(슬롯)과 고결감을 모아 회고용으로 돌려준다."""
    points = [(1, "어제"), (7, "1주 전"), (30, "한 달 전"), (365, "1년 전")]
    out = []
    with get_conn() as conn:
        for off, label in points:
            d = (today - timedelta(days=off)).strftime("%Y-%m-%d")
            slots = [
                dict(r) for r in conn.execute(
                    "SELECT b.block_label, s.start_time, s.do_text, s.did_text "
                    "FROM slots s JOIN blocks b ON b.id = s.block_id "
                    "WHERE s.date = ? AND (TRIM(COALESCE(s.do_text,'')) != '' "
                    "OR TRIM(COALESCE(s.did_text,'')) != '') ORDER BY s.slot_index LIMIT 10",
                    (d,),
                )
            ]
            refls = [
                dict(r) for r in conn.execute(
                    "SELECT kind, title, text FROM reflection WHERE event_date = ? "
                    "ORDER BY id DESC LIMIT 10",
                    (d,),
                )
            ]
            if slots or refls:
                out.append({"label": label, "date": d, "slots": slots, "reflections": refls})
    return out


def _build_insights(summary, weekday_data, block_pd, cats) -> list[str]:
    """축적 데이터에서 규칙기반 개선점 문장을 만든다(근거가 충분한 항목만)."""
    out: list[str] = []
    wd_valid = [w for w in weekday_data if w["planned"] >= 3]
    if wd_valid:
        worst = min(wd_valid, key=lambda w: w["pct"])
        best = max(wd_valid, key=lambda w: w["pct"])
        if worst["pct"] + 15 <= best["pct"]:
            out.append(
                f"{worst['label']}요일 완료율이 {worst['pct']}%로 가장 낮습니다"
                f"(최고 {best['label']} {best['pct']}%). 그 요일 계획을 줄이거나 "
                f"쉬운 일부터 배치해 보세요."
            )
    bp_valid = [b for b in block_pd if b["planned"] >= 3]
    if bp_valid:
        worst_b = min(bp_valid, key=lambda b: b["pct"])
        if worst_b["pct"] < 60:
            out.append(
                f"{worst_b['label']} 블록이 계획 대비 실행 {worst_b['pct']}%로 가장 "
                f"낮습니다. 그 시간대에 무리한 계획을 잡고 있지 않은지 살펴보세요."
            )
    if cats and cats[0]["pct"] >= 40:
        out.append(
            f"'{cats[0]['name']}' 구분이 전체 시간의 {cats[0]['pct']}%로 가장 큽니다. "
            f"구분 배분이 목표와 맞는지 돌아보세요."
        )
    if summary["pd_pct"] and summary["pd_pct"] < 50:
        out.append(
            f"코어 블록 계획→실행이 {summary['pd_pct']}%입니다. 계획을 줄여 "
            f"실행률부터 올리는 편이 낫습니다."
        )
    if summary["avg_done"] >= 80:
        out.append(
            f"평균 완료율 {summary['avg_done']}%로 잘 지키고 있습니다. "
            f"계획량을 조금 늘려도 좋습니다."
        )
    if not out:
        out.append("아직 개선점을 뽑을 만큼 데이터가 충분하지 않습니다. 기록을 더 쌓아 보세요.")
    return out


def _ai_insights(summary, weekday_data, block_pd, cats) -> str | None:
    """AI로 지표를 요약한 짧은 개선 제안. 실패·미설정 시 None."""
    wd = ", ".join(f"{w['label']} {w['pct']}%" for w in weekday_data if w["planned"])
    bp = ", ".join(f"{b['label']} {b['pct']}%" for b in block_pd)
    ct = ", ".join(f"{c['name']} {c['pct']}%" for c in cats[:5])
    metrics = (
        f"평균 완료율 {summary['avg_done']}%, 코어 계획→실행 {summary['pd_pct']}%, "
        f"연속기록 {summary['streak']}일.\n요일별 완료율: {wd or '자료 부족'}.\n"
        f"블록별 계획대비 실행: {bp or '자료 부족'}.\n구분 배분: {ct or '자료 부족'}."
    )
    system = ("당신은 개인 시간관리 코치입니다. 아래 지표를 보고 한국어로 구체적이고 "
              "실천 가능한 개선 제안을 2~3가지, 각 한 문장으로 제시합니다. 군더더기 없이.")
    return ai.complete(system, metrics, max_tokens=400, temperature=0.5)


def _exec_funnel(conn, start, end):
    """실행 퍼널: 코어 블록 계획(구분) → 슬롯 구체화(DO) → 슬롯 실행(done·한일) 3단계 비율과,
    실행 점수(3단계 곱)·실질 실행율(실행 슬롯/전체 코어 슬롯)을 [start,end] 기간으로 계산한다.
    계획된 블록 = 구분(category_id)을 넣은 코어 블록. 실행 = done 체크 또는 '한일'(did_text) 기록."""
    b = conn.execute(
        "SELECT COUNT(*) AS core_blocks, "
        "SUM(CASE WHEN category_id IS NOT NULL THEN 1 ELSE 0 END) AS designed_blocks "
        "FROM blocks WHERE is_core = 1 AND date >= ? AND date <= ?",
        (start, end),
    ).fetchone()
    s = conn.execute(
        "SELECT COUNT(*) AS slots_in_designed, "
        "SUM(CASE WHEN TRIM(COALESCE(s.do_text,'')) != '' THEN 1 ELSE 0 END) AS detailed_slots, "
        "SUM(CASE WHEN TRIM(COALESCE(s.do_text,'')) != '' "
        "         AND (s.done = 1 OR TRIM(COALESCE(s.did_text,'')) != '') THEN 1 ELSE 0 END) AS executed_detailed "
        "FROM slots s JOIN blocks b ON b.id = s.block_id "
        "WHERE b.is_core = 1 AND b.category_id IS NOT NULL "
        "  AND s.date >= ? AND s.date <= ?",
        (start, end),
    ).fetchone()
    a = conn.execute(
        "SELECT COUNT(*) AS core_slots, "
        "SUM(CASE WHEN s.done = 1 OR TRIM(COALESCE(s.did_text,'')) != '' THEN 1 ELSE 0 END) AS done_slots "
        "FROM slots s JOIN blocks b ON b.id = s.block_id "
        "WHERE b.is_core = 1 AND s.date >= ? AND s.date <= ?",
        (start, end),
    ).fetchone()
    core_blocks = b["core_blocks"] or 0
    designed = b["designed_blocks"] or 0
    slots_in_designed = s["slots_in_designed"] or 0
    detailed = s["detailed_slots"] or 0
    executed = s["executed_detailed"] or 0
    core_slots = a["core_slots"] or 0
    done_slots = a["done_slots"] or 0
    design_r = designed / core_blocks if core_blocks else 0
    detail_r = detailed / slots_in_designed if slots_in_designed else 0
    exec_r = executed / detailed if detailed else 0
    return {
        "core_blocks": core_blocks, "designed_blocks": designed,
        "slots_in_designed": slots_in_designed, "detailed_slots": detailed,
        "executed_detailed": executed, "core_slots": core_slots, "done_slots": done_slots,
        "design_pct": round(design_r * 100), "detail_pct": round(detail_r * 100),
        "exec_pct": round(exec_r * 100),
        "exec_score": round(design_r * detail_r * exec_r * 100),
        "real_exec": round(done_slots / core_slots * 100) if core_slots else 0,
    }


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
        # 슬롯 구분이 비면 블록 구분을 따라 집계한다(블록→슬롯 상속).
        cat_rows = conn.execute(
            "SELECT c.name, c.tone, COUNT(s.id) AS cnt "
            "FROM slots s JOIN blocks b ON b.id = s.block_id "
            "JOIN categories c ON c.id = COALESCE(s.category_id, b.category_id) "
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
        # 블록별(B1~B6) 계획 대비 실행: 어느 시간대를 반복적으로 흘려보내는지 본다.
        block_pd_rows = conn.execute(
            "SELECT b.block_label AS lbl, MIN(b.block_order) AS ord, COUNT(*) AS planned, "
            "SUM(CASE WHEN EXISTS(SELECT 1 FROM slots s WHERE s.block_id = b.id "
            "    AND ((s.do_text IS NOT NULL AND TRIM(s.do_text) != '') OR s.done = 1)) "
            "    THEN 1 ELSE 0 END) AS achieved "
            "FROM blocks b WHERE b.is_core = 1 AND TRIM(COALESCE(b.plan_text, '')) != '' "
            "  AND b.date >= ? AND b.date <= ? GROUP BY b.block_label ORDER BY ord",
            (start, today_s),
        ).fetchall()
        rec_dates = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM slots "
                "WHERE (do_text IS NOT NULL AND TRIM(do_text) != '') OR done = 1"
            )
        }
        funnel = _exec_funnel(conn, start, today_s)
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
    # 요일별 완료율(어느 요일을 자주 흘려보내는지) — 일자 데이터를 요일로 묶는다.
    wd_acc = {i: [0, 0] for i in range(7)}
    for r in day_rows:
        wd = datetime.strptime(r["date"], "%Y-%m-%d").date().weekday()
        wd_acc[wd][0] += r["done_cnt"] or 0
        wd_acc[wd][1] += r["planned_cnt"] or 0
    weekday_data = [
        {"label": KO_WEEKDAYS[i], "done": wd_acc[i][0], "planned": wd_acc[i][1],
         "pct": round(wd_acc[i][0] / wd_acc[i][1] * 100) if wd_acc[i][1] else 0}
        for i in range(7)
    ]
    block_pd = [
        {"label": r["lbl"], "planned": r["planned"], "achieved": r["achieved"],
         "pct": round(r["achieved"] / r["planned"] * 100) if r["planned"] else 0}
        for r in block_pd_rows
    ]
    insights = _build_insights(summary, weekday_data, block_pd, cats)
    ai_summary = _ai_insights(summary, weekday_data, block_pd, cats) if ai.enabled() else None
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
            "weekday_data": weekday_data,
            "block_pd": block_pd,
            "insights": insights,
            "ai_summary": ai_summary,
            "summary": summary,
            "funnel": funnel,
            "q": q,
            "s_slots": s_slots,
            "s_blocks": s_blocks,
            "flashback": _on_this_day(today),
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


def _cascade_local_delete(conn, row):
    """로컬 reflection 한 줄을 지우면서 짝(원본↔다시보기 사본) 관계를 정리한다.
    사본을 지우면 원본의 '다시 볼 날짜'를 풀고, 원본을 지우면 자식 사본도 함께 지운다."""
    if row["source_id"]:
        conn.execute(
            "UPDATE reflection SET review_date = NULL WHERE id = ?", (row["source_id"],)
        )
    else:
        conn.execute("DELETE FROM reflection WHERE source_id = ?", (row["id"],))
    conn.execute("DELETE FROM reflection WHERE id = ?", (row["id"],))


def _import_gcal_reflections(force: bool = False):
    """고결감 캘린더와 로컬을 맞춘다(추가·수정·삭제). 구글에서 직접 만들거나 고치거나 지운 것을
    6블록에 그대로 반영한다. force=True면 60초 읽기 캐시를 비우고 즉시 다시 읽는다."""
    if not gcal_write.enabled():
        return
    if force:
        gcal_write.invalidate_cache()
    today = datetime.now(KST).date()
    lo, hi = today - timedelta(days=730), today + timedelta(days=730)
    lo_s, hi_s = lo.isoformat(), hi.isoformat()
    try:
        evs = gcal_write.list_reflection_events(lo, hi)
    except Exception:
        return
    # 빈 응답(일시적 실패 포함)이면 삭제 reconcile을 돌리지 않는다. 그렇지 않으면 구글이
    # 잠깐 0건을 돌려줄 때 동기화됐던 로컬 기록이 한꺼번에 삭제될 수 있다.
    if not evs:
        return
    by_id = {ev["id"]: ev for ev in evs}
    now = datetime.now(KST).isoformat(timespec="seconds")
    # 막 만든 항목은 구글 목록 반영 지연으로 오삭제될 수 있어 2분간 삭제 대상에서 뺀다.
    del_cutoff = (datetime.now(KST) - timedelta(minutes=2)).isoformat(timespec="seconds")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, gcal_event_id, kind, title, text, tags, event_date, "
            "source_id, created_at FROM reflection WHERE gcal_event_id IS NOT NULL"
        ).fetchall()
        local_by_event = {r["gcal_event_id"]: r for r in rows}
        # 1) 추가·수정: 구글 이벤트를 로컬에 맞춘다(로컬 전용 필드 review_date·source_id는 보존).
        for eid, ev in by_id.items():
            r = local_by_event.get(eid)
            # 다시보기 사본은 설명이 '다시보기 내용 우선 + 원본'이라 6block이 관리한다.
            # 구글 설명을 로컬 text로 되덮으면 원본 참조가 깨지므로 역동기화에서 뺀다(삭제 감지는 유지).
            if r is not None and r["source_id"]:
                continue
            if r is None:
                conn.execute(
                    "INSERT INTO reflection (kind, title, text, tags, event_date, "
                    "review_date, created_at, gcal_event_id, synced) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (ev["kind"], ev["title"], ev["content"], ev["tags"], ev["date"],
                     None, now, eid),
                )
            elif (
                (r["kind"] or "") != ev["kind"]
                or (r["title"] or "") != (ev["title"] or "")
                or (r["text"] or "") != (ev["content"] or "")
                or (r["tags"] or "") != (ev["tags"] or "")
                or (r["event_date"] or "") != ev["date"]
            ):
                conn.execute(
                    "UPDATE reflection SET kind = ?, title = ?, text = ?, tags = ?, "
                    "event_date = ? WHERE id = ?",
                    (ev["kind"], ev["title"], ev["content"], ev["tags"], ev["date"],
                     r["id"]),
                )
        # 2) 삭제: 동기화됐던 것이 조회 범위 안에서 구글에서 사라졌으면 로컬에서도 지운다.
        for r in rows:
            if r["gcal_event_id"] in by_id:
                continue
            if not (lo_s <= (r["event_date"] or "") <= hi_s):
                continue                                   # 조회 범위 밖은 손대지 않음
            if (r["created_at"] or "") > del_cutoff:
                continue                                   # 방금 만든 것은 보호
            _cascade_local_delete(conn, r)


def _reflect_ctx(q: str = "", kind: str = "") -> dict:
    """고결감 화면·부분갱신이 함께 쓰는 컨텍스트(목록·미도래·태그)를 만든다."""
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
    today = today_str()
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        # 미도래 다시보기: '다시 볼 날짜가 남은 원본'만 한 번 보여준다(사본 중복 제거).
        upcoming = conn.execute(
            "SELECT * FROM reflection "
            "WHERE source_id IS NULL AND review_date IS NOT NULL AND review_date > ? "
            "ORDER BY review_date ASC LIMIT 30",
            (today,),
        ).fetchall()
        # 원본 id → 다시보기 사본 id (원본 카드에서 사본으로 바로 이동하기 위함).
        child_map = {
            r["source_id"]: r["id"]
            for r in conn.execute(
                "SELECT id, source_id FROM reflection WHERE source_id IS NOT NULL"
            )
        }
        # 다시보기 사본 카드에 보여줄 '원본의 다시보기 내용'(원본 id → review_note).
        parent_note_map = {
            r["id"]: (r["review_note"] or "")
            for r in conn.execute(
                "SELECT id, review_note FROM reflection WHERE source_id IS NULL"
            )
        }
        tag_rows = conn.execute(
            "SELECT DISTINCT tags FROM reflection WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
    seen: set[str] = set()
    all_tags: list[str] = []
    for tr in tag_rows:
        for t in (tr["tags"] or "").split():
            t = t.strip().rstrip(",")
            if t and t not in seen:
                seen.add(t)
                all_tags.append(t)
    items = []
    for r in rows:
        d = dict(r)
        d["review_child_id"] = child_map.get(r["id"])
        if r["source_id"]:
            d["parent_review_note"] = parent_note_map.get(r["source_id"], "")
        items.append(d)
    return {
        "items": items,
        "kinds": REFLECT_KINDS,
        "q": q,
        "kind": kind,
        "today": today,
        "gcal_write_on": gcal_write.enabled(),
        "upcoming_reviews": [dict(r) for r in upcoming],
        "all_tags": all_tags,
    }


def _reflect_sig(ctx: dict) -> str:
    """목록·미도래의 현재 상태 지문. 자동 폴링에서 변화 없으면 화면을 건드리지 않게 비교한다."""
    parts: list[str] = []
    for it in ctx["items"]:
        parts.append("|".join(str(it.get(k, "")) for k in (
            "id", "kind", "title", "text", "tags", "event_date", "review_date",
            "synced", "review_child_id", "parent_review_note")))
    for u in ctx["upcoming_reviews"]:
        parts.append("u" + "|".join(str(u.get(k, "")) for k in (
            "id", "review_date", "title", "text")))
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()


@app.get("/reflect")
def reflect_view(request: Request, q: str = "", kind: str = ""):
    _import_gcal_reflections()  # 구글 캘린더에서 만든 것도 탭에 보이게(양방향)
    # 검색어는 화면에서 유사검색(클라이언트)으로 거른다. 서버는 종류만 걸러 폴링과 같은 집합을
    # 그려 지문이 일치하게 하고, q는 검색창 채우기에만 쓴다.
    ctx = _reflect_ctx("", kind)
    ctx["q"] = (q or "").strip()
    ctx["request"] = request
    ctx["sig"] = _reflect_sig(ctx)
    return templates.TemplateResponse("reflect.html", ctx)


@app.get("/reflect/list")
def reflect_list(q: str = "", kind: str = "", force: int = 0):
    """자동 폴링·수동 동기화용 부분 응답. 목록·미도래 HTML과 변경감지 지문을 돌려준다."""
    _import_gcal_reflections(force=bool(force))
    ctx = _reflect_ctx(q, kind)
    env = templates.env
    return JSONResponse({
        "ok": True,
        "sig": _reflect_sig(ctx),
        "list_html": env.get_template("_reflect_list.html").render(ctx),
        "upcoming_html": env.get_template("_reflect_upcoming.html").render(ctx),
    })


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
    # 원본 이벤트는 항상 기록일(event_date)에 올린다.
    try:
        event_id = gcal_write.create_event(kind, title, text, tags, event_date)
    except Exception:
        event_id = None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reflection (kind, title, text, tags, event_date, review_date, "
            "created_at, gcal_event_id, synced) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, title, text, tags, event_date, review_date, now, event_id,
             1 if event_id else 0),
        )
        new_id = cur.lastrowid
        # 다시 볼 날짜가 있으면 별도 '다시보기' 항목을 생성한다(원본과 독립 삭제 가능).
        if review_date and review_date != event_date:
            review_title = f"다시보기: {title}"
            try:
                rev_event_id = gcal_write.create_event(
                    kind, review_title, text, tags, review_date
                )
            except Exception:
                rev_event_id = None
            conn.execute(
                "INSERT INTO reflection (kind, title, text, tags, event_date, "
                "created_at, gcal_event_id, source_id, synced) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, review_title, text, tags, review_date, now,
                 rev_event_id, new_id, 1 if rev_event_id else 0),
            )
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
        title = _reflect_title(r["title"], r["text"])
        try:
            event_id = gcal_write.create_event(
                r["kind"], title, r["text"] or "", r["tags"] or "", r["event_date"]
            )
        except Exception:
            event_id = None
        if event_id:
            conn.execute(
                "UPDATE reflection SET gcal_event_id = ?, synced = 1 WHERE id = ?",
                (event_id, item_id),
            )
    return JSONResponse({"ok": bool(event_id), "synced": bool(event_id)})


@app.post("/reflect/update/{item_id}")
async def reflect_update(item_id: int, request: Request):
    """종류·제목·내용·태그·다시 볼 날짜를 수정하고, 구글 이벤트와 다시보기 사본까지 함께 맞춘다."""
    form = await request.form()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM reflection WHERE id = ?", (item_id,)).fetchone()
        if not r:
            return JSONResponse({"ok": False}, status_code=404)
        if r["source_id"]:
            # 다시보기 사본은 직접 편집하지 않는다(원본에서 관리).
            return JSONResponse({"ok": False, "error": "copy"}, status_code=400)
        kind = (form.get("kind") or "").strip()
        kind = kind if kind in REFLECT_KINDS else (r["kind"] or "고민")
        title = (form.get("title") or "").strip()
        text = (form.get("text") or "").strip()
        tags = (form.get("tags") or "").strip()
        review_date = (form.get("review_date") or "").strip() or None
        event_date = (form.get("event_date") or "").strip() or r["event_date"]
        if not title and not text:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        title = _reflect_title(title, text)
        conn.execute(
            "UPDATE reflection SET kind = ?, title = ?, text = ?, tags = ?, "
            "review_date = ?, event_date = ? WHERE id = ?",
            (kind, title, text, tags, review_date, event_date, item_id),
        )
        if r["gcal_event_id"]:
            try:
                gcal_write.update_event(r["gcal_event_id"], kind, title, text, tags)
            except Exception:
                pass
        # 다시보기 사본 재조정(다시 볼 날짜가 기록일과 다를 때만 존재).
        child = conn.execute(
            "SELECT * FROM reflection WHERE source_id = ?", (item_id,)
        ).fetchone()
        want_copy = bool(review_date and review_date != event_date)
        if want_copy:
            review_title = f"다시보기: {title}"
            if child is None:
                try:
                    rev_eid = gcal_write.create_review_copy(
                        kind, review_title, r["review_note"], text, tags, review_date
                    )
                except Exception:
                    rev_eid = None
                conn.execute(
                    "INSERT INTO reflection (kind, title, text, tags, event_date, "
                    "created_at, gcal_event_id, source_id, synced) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (kind, review_title, text, tags, review_date, now, rev_eid,
                     item_id, 1 if rev_eid else 0),
                )
            elif (child["event_date"] or "") != review_date:
                # 날짜가 바뀌면 구글 일정도 옮긴다(삭제 후 그날로 재생성).
                if child["gcal_event_id"]:
                    try:
                        gcal_write.delete_event(child["gcal_event_id"])
                    except Exception:
                        pass
                try:
                    rev_eid = gcal_write.create_review_copy(
                        kind, review_title, r["review_note"], text, tags, review_date
                    )
                except Exception:
                    rev_eid = None
                conn.execute(
                    "UPDATE reflection SET kind = ?, title = ?, text = ?, tags = ?, "
                    "event_date = ?, gcal_event_id = ?, synced = ? WHERE id = ?",
                    (kind, review_title, text, tags, review_date, rev_eid,
                     1 if rev_eid else 0, child["id"]),
                )
            else:
                if child["gcal_event_id"]:
                    try:
                        gcal_write.update_review_copy(
                            child["gcal_event_id"], kind, review_title,
                            r["review_note"], text, tags
                        )
                    except Exception:
                        pass
                conn.execute(
                    "UPDATE reflection SET kind = ?, title = ?, text = ?, tags = ? "
                    "WHERE id = ?",
                    (kind, review_title, text, tags, child["id"]),
                )
        elif child is not None:
            # 다시 볼 날짜가 없어졌으면 사본과 그 구글 일정을 지운다.
            if child["gcal_event_id"]:
                try:
                    gcal_write.delete_event(child["gcal_event_id"])
                except Exception:
                    pass
            conn.execute("DELETE FROM reflection WHERE id = ?", (child["id"],))
    return JSONResponse({"ok": True})


@app.post("/reflect/delete/{item_id}")
def reflect_delete(item_id: int):
    """기록을 삭제하고 캘린더 이벤트도 함께 지운다. 원본을 지우면 다시보기 사본도,
    사본을 지우면 원본의 '다시 볼 날짜'를 함께 정리한다."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT id, gcal_event_id, review_gcal_event_id, source_id "
            "FROM reflection WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not r:
            return JSONResponse({"ok": True})
        for eid in (r["gcal_event_id"], r["review_gcal_event_id"]):
            if eid:
                try:
                    gcal_write.delete_event(eid)
                except Exception:
                    pass
        if r["source_id"]:
            conn.execute(
                "UPDATE reflection SET review_date = NULL WHERE id = ?", (r["source_id"],)
            )
        else:
            for c in conn.execute(
                "SELECT id, gcal_event_id FROM reflection WHERE source_id = ?", (item_id,)
            ).fetchall():
                if c["gcal_event_id"]:
                    try:
                        gcal_write.delete_event(c["gcal_event_id"])
                    except Exception:
                        pass
                conn.execute("DELETE FROM reflection WHERE id = ?", (c["id"],))
        conn.execute("DELETE FROM reflection WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True})


@app.post("/reflect/review-note/{item_id}")
async def reflect_review_note(item_id: int, request: Request):
    """다시보기 내용을 저장하고, 사본 캘린더 이벤트에 다시보기 내용을 우선 반영한다."""
    form = await request.form()
    note = (form.get("note") or "").strip()
    with get_conn() as conn:
        conn.execute(
            "UPDATE reflection SET review_note = ? WHERE id = ?", (note, item_id)
        )
        orig = conn.execute(
            "SELECT kind, title, text, tags FROM reflection WHERE id = ?", (item_id,)
        ).fetchone()
        child = conn.execute(
            "SELECT gcal_event_id FROM reflection WHERE source_id = ?", (item_id,)
        ).fetchone()
    # 사본(다시보기) 캘린더 이벤트 설명을 '다시보기 내용 우선 + 원본'으로 갱신(있을 때만).
    if orig and child and child["gcal_event_id"] and gcal_write.enabled():
        try:
            gcal_write.update_review_copy(
                child["gcal_event_id"], orig["kind"],
                f"다시보기: {(orig['title'] or '').strip()}",
                note, orig["text"] or "", orig["tags"] or "",
            )
        except Exception:
            pass
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
