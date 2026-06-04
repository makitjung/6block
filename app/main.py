# 오늘/주간 입력과 PWA 서빙, 포모도로 정적 자원을 제공하는 FastAPI 메인 애플리케이션
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import DAY_BLOCKS, slots_for_day
from app.db import get_conn, init_db

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


def today_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def ensure_day_skeleton(conn, date_str: str):
    """해당 날짜의 블록과 30분 슬롯 행이 없으면 생성한다."""
    if conn.execute(
        "SELECT 1 FROM blocks WHERE date = ? LIMIT 1", (date_str,)
    ).fetchone():
        return
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
    return RedirectResponse(url="/today")


@app.get("/today")
def today_view(request: Request):
    return _day_view(request, today_str())


@app.get("/day/{date_str}")
def day_view(request: Request, date_str: str):
    return _day_view(request, date_str)


def _day_view(request: Request, date_str: str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    prev_date = (d - timedelta(days=1)).strftime("%Y-%m-%d")
    next_date = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
        categories = conn.execute(
            "SELECT id, name, color FROM categories "
            "WHERE is_active = 1 ORDER BY display_order"
        ).fetchall()
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

    themes_by_label = {r["block_label"]: r["theme_text"] for r in theme_rows}
    slots_by_block: dict[int, list] = {}
    for s in slots:
        slots_by_block.setdefault(s["block_id"], []).append(s)

    return templates.TemplateResponse(
        "today.html",
        {
            "request": request,
            "date_str": date_str,
            "prev_date": prev_date,
            "next_date": next_date,
            "blocks": blocks,
            "slots_by_block": slots_by_block,
            "categories": categories,
            "meta": meta,
            "themes_by_label": themes_by_label,
        },
    )


@app.post("/save/{date_str}")
async def save_day(date_str: str, request: Request):
    form = await request.form()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
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
            elif prefix == "cat":
                cid = int(val) if val else None
                conn.execute(
                    "UPDATE slots SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, sid),
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
                form.get("today_goal", ""),
                form.get("daily_plan", ""),
                form.get("memo", ""),
                form.get("vow", ""),
            ),
        )
    return RedirectResponse(url=f"/day/{date_str}", status_code=303)


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
            f"SELECT date, block_label, block_order, is_core, plan_text, "
            f"       see_text, start_time, end_time FROM blocks "
            f"WHERE date IN ({placeholders}) ORDER BY date, block_order",
            dates,
        ).fetchall()
        cat_summary = conn.execute(
            f"""
            SELECT c.name, c.color, COUNT(s.id) AS slot_count
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

    blocks_by_date: dict[str, list] = {d: [] for d in dates}
    for r in rows:
        blocks_by_date[r["date"]].append(r)

    themes_by_label = {r["block_label"]: r["theme_text"] for r in theme_rows}
    achieve_pct = round(achieved / plan_total * 100) if plan_total else 0
    used_core_total = 42

    total_slots = sum(r["slot_count"] for r in cat_summary)
    cat_summary_pct = [
        {
            "name": r["name"],
            "color": r["color"],
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
            "cat_summary": cat_summary_pct,
            "used_core": plan_total,
            "total_core": used_core_total,
            "achieve_pct": achieve_pct,
            "wmeta": wmeta,
            "themes_by_label": themes_by_label,
            "core_labels": CORE_LABELS,
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
    return RedirectResponse(url=f"/week/{week_start_str}", status_code=303)


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


@app.get("/api/now")
def api_now():
    """클라이언트가 서버 시각 기준으로 포모도로 정렬할 수 있게 KST를 반환."""
    n = datetime.now(KST)
    return {"iso": n.isoformat(timespec="seconds"), "epoch_ms": int(n.timestamp() * 1000)}
