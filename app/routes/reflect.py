# 고결감(고민·결정·감사) 아카이브와 캘린더 동기화.
import hashlib
import math
import re
import unicodedata
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse

from app.common import KST, SQLITE_MAX_INT, RowId, _like_pattern, _parse_date, templates, today_str
from app.db import get_conn, uid_from_created
from app.integrations import gcal_write, reflection_sync

router = APIRouter()
REFLECT_KINDS = ("고민", "결정", "감사")
PAGE_SIZE = 36


def _reflect_title(title, text) -> str:
    """제목이 비면 내용 첫 줄을 제목에 쓴다."""
    return (title or "").strip() or ((text or "").strip().splitlines() or [""])[0][:120]


def _tag_keys(tags):
    return [t.lstrip("#") for t in re.split(r"[,\s]+", tags or "") if t.strip("#")]


def _normalized(text):
    return unicodedata.normalize("NFC", text or "").lower()


def _contains_letters(token, text):
    if token in text:
        return True
    letters = iter(text)
    return all(letter in letters for letter in token)


def _reflect_ctx(q="", kind="", tag="", page=1, limit=PAGE_SIZE, focus=None, *, fuzzy=False):
    """날짜 내림차순 목록에 종류·검색·태그를 먼저 적용하고 그 결과를 페이지로 나눈다."""
    q = q.strip()
    kind = kind if kind in REFLECT_KINDS else ""
    tag = _normalized(tag.strip().lstrip("#"))
    where = []
    params = []
    if kind:
        where.append("r.kind = ?")
        params.append(kind)
    tokens = _normalized(q).split()
    if fuzzy and tokens:
        where.append("archive_search(r.title, r.text, r.tags, p.review_note)")
    elif q:
        where.append("(r.title LIKE ? ESCAPE '\\' OR r.text LIKE ? ESCAPE '\\' "
                     "OR r.tags LIKE ? ESCAPE '\\')")
        params.extend([_like_pattern(q)] * 3)
    if tag:
        where.append("archive_tag(r.tags)")
    base = " FROM reflection r LEFT JOIN reflection p ON p.id = r.source_id"
    filters = " WHERE " + " AND ".join(where) if where else ""
    today = today_str()
    with get_conn() as conn:
        if fuzzy and tokens:
            def matches(*parts):
                text = _normalized(" ".join(p for p in parts if p))
                return all(_contains_letters(token, text) for token in tokens)
            conn.create_function("archive_search", 4, matches, deterministic=True)
        if tag:
            conn.create_function("archive_tag", 1,
                                 lambda value: tag in _tag_keys(_normalized(value)),
                                 deterministic=True)
        total = conn.execute("SELECT COUNT(*)" + base + filters, params).fetchone()[0]
        pages = max(1, math.ceil(total / limit))
        if focus:
            target = conn.execute("SELECT r.event_date, r.id" + base + filters +
                                  (" AND " if filters else " WHERE ") + "r.id = ?",
                                  (*params, focus)).fetchone()
            if target:
                before = conn.execute(
                    "SELECT COUNT(*)" + base + filters + (" AND " if filters else " WHERE ") +
                    "(r.event_date, r.id) > (?, ?)", (*params, target["event_date"], target["id"])
                ).fetchone()[0]
                page = before // limit + 1
        page = min(page, pages)
        rows = conn.execute(
            "SELECT r.*, p.review_note AS parent_review_note, p.title AS parent_title, "
            "p.text AS parent_text, p.kind AS parent_kind, p.tags AS parent_tags, "
            "p.event_date AS parent_event_date, p.review_date AS parent_review_date, "
            "(SELECT id FROM reflection WHERE source_id = r.id LIMIT 1) AS review_child_id, "
            "j.error AS sync_error, j.done AS sync_done, j.calendar_id AS sync_calendar" +
            base + " LEFT JOIN reflection_sync_job j ON j.item_id = r.id AND j.event_id = r.gcal_event_id" +
            filters + " ORDER BY r.event_date DESC, r.id DESC LIMIT ? OFFSET ?",
            (*params, limit, (page - 1) * limit),
        ).fetchall()
        upcoming = conn.execute(
            "SELECT id, uid, kind, title, text, review_date FROM reflection "
            "WHERE source_id IS NULL AND review_date > ? ORDER BY review_date ASC LIMIT 30",
            (today,),
        ).fetchall()
        tag_rows = conn.execute("SELECT DISTINCT tags FROM reflection WHERE COALESCE(tags, '') != ''")
        all_tags = sorted({"#" + t for row in tag_rows for t in _tag_keys(row["tags"])},
                          key=_normalized)
        sync_status = reflection_sync.status(conn)
    items = []
    enabled = gcal_write.enabled()
    for r in rows:
        item = dict(r)
        if not enabled:
            item["sync_state"] = "off"
        elif r["sync_calendar"] is not None and r["sync_calendar"] != gcal_write.GCAL_WRITE_CALENDAR_ID:
            item["sync_state"] = "calendar_changed"
        elif r["synced"]:
            item["sync_state"] = "synced"
        elif r["sync_error"]:
            item["sync_state"] = "failed"
        elif r["sync_done"] == 0:
            item["sync_state"] = "pending"
        else:
            item["sync_state"] = "local"
        items.append(item)
    return {
        "items": items, "kinds": REFLECT_KINDS, "q": q, "kind": kind, "tag": tag, "today": today,
        "gcal_write_on": enabled, "upcoming_reviews": [dict(r) for r in upcoming],
        "all_tags": all_tags, "sync_status": sync_status,
        "page": page, "limit": limit, "pages": pages, "total": total, "focus": focus,
    }


def _reflect_sig(ctx):
    data = (
        ctx["q"], ctx["kind"], ctx["tag"], ctx["page"], ctx["pages"], ctx["total"],
        ctx["items"], ctx["upcoming_reviews"], ctx["all_tags"],
    )
    return hashlib.sha256(repr(data).encode()).hexdigest()


@router.get("/reflect")
def reflect_view(request: Request, background: BackgroundTasks, q: str = "", kind: str = "",
                 tag: str = "", page: int = Query(1, ge=1),
                 focus: int | None = Query(None, ge=1, le=SQLITE_MAX_INT)):
    if gcal_write.enabled():
        background.add_task(reflection_sync.run, pull=True)
    ctx = _reflect_ctx(q, kind, tag, page, focus=focus, fuzzy=True)
    ctx["sig"] = _reflect_sig(ctx)
    return templates.TemplateResponse(request, "reflect.html", ctx)


@router.get("/reflect/list")
def reflect_list(q: str = "", kind: str = "", force: int = 0, tag: str = "",
                 page: int = Query(1, ge=1), limit: int = Query(PAGE_SIZE, ge=1, le=120),
                 focus: int | None = Query(None, ge=1, le=SQLITE_MAX_INT), sync: int = 1):
    pulled = reflection_sync.run(force=bool(force), pull=True) if sync or force else {}
    ctx = _reflect_ctx(q, kind, tag, page, limit, focus, fuzzy=True)
    env = templates.env
    return JSONResponse({
        "ok": True, "sig": _reflect_sig(ctx),
        "list_html": env.get_template("_reflect_list.html").render(ctx),
        "upcoming_html": env.get_template("_reflect_upcoming.html").render(ctx),
        "page": ctx["page"], "pages": ctx["pages"], "total": ctx["total"], "tags": ctx["all_tags"],
        "sync": {**ctx["sync_status"], **pulled},
    })


@router.get("/reflect/api/items")
def reflect_api_items(q: str = "", kind: str = "", sync: int = 1, force: int = 0,
                      page: int = Query(1, ge=1), limit: int = Query(500, ge=1, le=500)):
    """Record에서 쓰는 JSON 필드와 기본 반환 건수는 두고, 페이지 요청도 받는다."""
    pulled = reflection_sync.run(force=bool(force), pull=True) if sync else {}
    ctx = _reflect_ctx(q, kind, page=page, limit=limit)
    keys = ("id", "uid", "kind", "title", "text", "tags", "event_date", "review_date",
            "review_note", "created_at", "source_id", "synced", "review_child_id")
    return JSONResponse({
        "ok": True, "kinds": list(REFLECT_KINDS),
        "items": [{k: it.get(k) for k in keys} for it in ctx["items"]],
        "upcoming": [{k: u.get(k) for k in ("id", "uid", "title", "review_date")}
                     for u in ctx["upcoming_reviews"]],
        "tags": ctx["all_tags"], "gcal_on": ctx["gcal_write_on"],
        "page": ctx["page"], "pages": ctx["pages"], "total": ctx["total"],
        "sync_status": {**ctx["sync_status"], **pulled},
    })


def _insert_record(conn, kind, title, text, tags, event_date, now, *, review_date=None, source_id=None):
    return conn.execute(
        "INSERT INTO reflection (kind, title, text, tags, event_date, review_date, "
        "created_at, source_id, uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, title, text, tags, event_date, review_date, now, source_id, uid_from_created(now)),
    ).lastrowid


def _save_review_copy(conn, item_id, kind, title, text, tags, event_date, review_date, now):
    child = conn.execute("SELECT * FROM reflection WHERE source_id = ?", (item_id,)).fetchone()
    if not review_date or review_date == event_date:
        return reflection_sync.remove(conn, child) if child else False
    title = f"다시보기: {title}"
    if child is None:
        child_id = _insert_record(conn, kind, title, text, tags, review_date, now, source_id=item_id)
        return reflection_sync.queue_save(conn, child_id)
    conn.execute("UPDATE reflection SET kind=?, title=?, text=?, tags=?, event_date=? WHERE id=?",
                 (kind, title, text, tags, review_date, child["id"]))
    return reflection_sync.queue_save(conn, child["id"], move_date=(child["event_date"] != review_date))


def _save_response(background, queued, **result):
    if queued:
        background.add_task(reflection_sync.run)
    return JSONResponse({"ok": True, "sync_state": "pending" if queued else "local", **result})


@router.post("/reflect/add")
async def reflect_add(request: Request, background: BackgroundTasks):
    form = await request.form()
    kind = form.get("kind") if form.get("kind") in REFLECT_KINDS else "고민"
    title = (form.get("title") or "").strip()
    text = (form.get("text") or "").strip()
    tags = (form.get("tags") or "").strip()
    review_date = _parse_date(form.get("review_date"))
    if not title and not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    title = _reflect_title(title, text)
    event_date = today_str()
    review_date = review_date.isoformat() if review_date else None
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        item_id = _insert_record(conn, kind, title, text, tags, event_date, now, review_date=review_date)
        queued = reflection_sync.queue_save(conn, item_id)
        copy_queued = _save_review_copy(conn, item_id, kind, title, text, tags, event_date, review_date, now)
    return _save_response(background, queued or copy_queued, id=item_id, synced=False)


@router.post("/reflect/sync/{item_id}")
def reflect_sync(item_id: RowId):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM reflection WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "기록이 없습니다"}, status_code=404)
        if row["synced"] and row["gcal_event_id"]:
            return JSONResponse({"ok": True, "synced": True})
        job = conn.execute("SELECT calendar_id FROM reflection_sync_job WHERE item_id = ?", (item_id,)).fetchone()
        if job and job["calendar_id"] != gcal_write.GCAL_WRITE_CALENDAR_ID:
            return JSONResponse({"ok": False, "synced": False, "error": "캘린더 설정이 변경되었습니다"})
        if job is None:
            reflection_sync.queue_save(conn, item_id, manual=True)
    outcome = reflection_sync.run(force=True, item_id=item_id)
    with get_conn() as conn:
        row = conn.execute("SELECT synced FROM reflection WHERE id = ?", (item_id,)).fetchone()
    synced = bool(row and row["synced"])
    error = "" if synced else ("동기화 중입니다" if outcome["state"] == "busy" else "캘린더 반영 실패")
    return JSONResponse({"ok": synced, "synced": synced, "error": error})


@router.post("/reflect/update/{item_id}")
async def reflect_update(item_id: RowId, request: Request, background: BackgroundTasks):
    form = await request.form()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM reflection WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False}, status_code=404)
        if row["source_id"]:
            return JSONResponse({"ok": False, "error": "copy"}, status_code=400)
        kind = form.get("kind") if form.get("kind") in REFLECT_KINDS else row["kind"]
        title = (form.get("title") or "").strip()
        text = (form.get("text") or "").strip()
        tags = (form.get("tags") or "").strip()
        title = _reflect_title(title, text)
        if not title and not text:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        event_raw = (form.get("event_date") or "").strip()
        event_date = _parse_date(event_raw)
        if event_raw and event_date is None:
            return JSONResponse({"ok": False, "error": "기록일을 확인하세요"}, status_code=400)
        event_date = event_date.isoformat() if event_date else row["event_date"]
        review_date = _parse_date(form.get("review_date"))
        review_date = review_date.isoformat() if review_date else None
        note = (form.get("review_note") or "").strip() if "review_note" in form else row["review_note"]
        conn.execute(
            "UPDATE reflection SET kind=?, title=?, text=?, tags=?, review_date=?, "
            "event_date=?, review_note=? WHERE id=?",
            (kind, title, text, tags, review_date, event_date, note, item_id),
        )
        queued = reflection_sync.queue_save(conn, item_id, move_date=(event_date != row["event_date"]))
        copy_queued = _save_review_copy(conn, item_id, kind, title, text, tags, event_date, review_date, now)
    return _save_response(background, queued or copy_queued)


@router.post("/reflect/delete/{item_id}")
def reflect_delete(item_id: RowId, background: BackgroundTasks):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM reflection WHERE id = ?", (item_id,)).fetchone()
        queued = reflection_sync.remove(conn, row) if row else False
    return _save_response(background, queued)


@router.post("/reflect/review-note/{item_id}")
async def reflect_review_note(item_id: RowId, request: Request, background: BackgroundTasks):
    note = ((await request.form()).get("note") or "").strip()
    with get_conn() as conn:
        cur = conn.execute("UPDATE reflection SET review_note = ? WHERE id = ?", (note, item_id))
        if not cur.rowcount:
            return JSONResponse({"ok": False}, status_code=404)
        child = conn.execute("SELECT id FROM reflection WHERE source_id = ?", (item_id,)).fetchone()
        queued = reflection_sync.queue_save(conn, child["id"]) if child else False
    return _save_response(background, queued)
