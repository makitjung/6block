# 고결감(고민·결정·감사) 화면과 구글 캘린더 양방향 동기화를 담당하는 라우터
import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.common import KST, _like_pattern, _off_loop, templates, today_str
from app.db import get_conn, uid_from_created
from app.integrations import gcal_write

router = APIRouter()


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
                    "review_date, created_at, gcal_event_id, synced, uid) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (ev["kind"], ev["title"], ev["content"], ev["tags"], ev["date"],
                     None, now, eid, uid_from_created(now)),
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
        where.append("(title LIKE ? ESCAPE '\\' OR text LIKE ? ESCAPE '\\' "
                     "OR tags LIKE ? ESCAPE '\\')")
        params += [_like_pattern(q)] * 3
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


@router.get("/reflect")
def reflect_view(request: Request, q: str = "", kind: str = ""):
    _import_gcal_reflections()  # 구글 캘린더에서 만든 것도 탭에 보이게(양방향)
    # 검색어는 화면에서 유사검색(클라이언트)으로 거른다. 서버는 종류만 걸러 폴링과 같은 집합을
    # 그려 지문이 일치하게 하고, q는 검색창 채우기에만 쓴다.
    ctx = _reflect_ctx("", kind)
    ctx["q"] = (q or "").strip()
    ctx["request"] = request
    ctx["sig"] = _reflect_sig(ctx)
    return templates.TemplateResponse("reflect.html", ctx)


@router.get("/reflect/list")
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


@router.get("/reflect/api/items")
def reflect_api_items(q: str = "", kind: str = "", sync: int = 1):
    """외부 앱(Record 고결감 탭)용 JSON 목록. HTML 대신 구조화된 항목을 돌려준다.
    sync=1 이면 조회 전에 구글 캘린더에서 만든 것도 가져와 반영한다."""
    if sync:
        _import_gcal_reflections()
    ctx = _reflect_ctx(q, kind)
    keys = ("id", "uid", "kind", "title", "text", "tags", "event_date", "review_date",
            "review_note", "created_at", "source_id", "synced", "review_child_id")
    return JSONResponse({
        "ok": True,
        "kinds": list(REFLECT_KINDS),
        "items": [{k: it.get(k) for k in keys} for it in ctx["items"]],
        "upcoming": [{k: u.get(k) for k in ("id", "uid", "title", "review_date")}
                     for u in ctx["upcoming_reviews"]],
        "tags": ctx["all_tags"],
        "gcal_on": ctx["gcal_write_on"],
    })


@router.post("/reflect/add")
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
        event_id = await _off_loop(gcal_write.create_event, kind, title, text, tags, event_date)
    except Exception:
        event_id = None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reflection (kind, title, text, tags, event_date, review_date, "
            "created_at, gcal_event_id, synced, uid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, title, text, tags, event_date, review_date, now, event_id,
             1 if event_id else 0, uid_from_created(now)),
        )
        new_id = cur.lastrowid
        # 다시 볼 날짜가 있으면 별도 '다시보기' 항목을 생성한다(원본과 독립 삭제 가능).
        if review_date and review_date != event_date:
            review_title = f"다시보기: {title}"
            try:
                rev_event_id = await _off_loop(gcal_write.create_event,
                    kind, review_title, text, tags, review_date
                )
            except Exception:
                rev_event_id = None
            conn.execute(
                "INSERT INTO reflection (kind, title, text, tags, event_date, "
                "created_at, gcal_event_id, source_id, synced, uid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, review_title, text, tags, review_date, now,
                 rev_event_id, new_id, 1 if rev_event_id else 0, uid_from_created(now)),
            )
    return JSONResponse({"ok": True, "id": new_id, "synced": bool(event_id)})


@router.post("/reflect/sync/{item_id}")
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


@router.post("/reflect/update/{item_id}")
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
                await _off_loop(gcal_write.update_event, r["gcal_event_id"], kind, title, text, tags)
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
                    rev_eid = await _off_loop(gcal_write.create_review_copy,
                        kind, review_title, r["review_note"], text, tags, review_date
                    )
                except Exception:
                    rev_eid = None
                conn.execute(
                    "INSERT INTO reflection (kind, title, text, tags, event_date, "
                    "created_at, gcal_event_id, source_id, synced, uid) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (kind, review_title, text, tags, review_date, now, rev_eid,
                     item_id, 1 if rev_eid else 0, uid_from_created(now)),
                )
            elif (child["event_date"] or "") != review_date:
                # 날짜가 바뀌면 구글 일정도 옮긴다(삭제 후 그날로 재생성).
                if child["gcal_event_id"]:
                    try:
                        await _off_loop(gcal_write.delete_event, child["gcal_event_id"])
                    except Exception:
                        pass
                try:
                    rev_eid = await _off_loop(gcal_write.create_review_copy,
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
                        await _off_loop(gcal_write.update_review_copy,
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
                    await _off_loop(gcal_write.delete_event, child["gcal_event_id"])
                except Exception:
                    pass
            conn.execute("DELETE FROM reflection WHERE id = ?", (child["id"],))
    return JSONResponse({"ok": True})


@router.post("/reflect/delete/{item_id}")
def reflect_delete(item_id: int):
    """기록을 삭제하고 캘린더 이벤트도 함께 지운다. 원본을 지우면 다시보기 사본도,
    사본을 지우면 원본의 '다시 볼 날짜'를 함께 정리한다."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT id, gcal_event_id, source_id FROM reflection WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not r:
            return JSONResponse({"ok": True})
        if r["gcal_event_id"]:
            try:
                gcal_write.delete_event(r["gcal_event_id"])
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


@router.post("/reflect/review-note/{item_id}")
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
            await _off_loop(gcal_write.update_review_copy,
                child["gcal_event_id"], orig["kind"],
                f"다시보기: {(orig['title'] or '').strip()}",
                note, orig["text"] or "", orig["tags"] or "",
            )
        except Exception:
            pass
    return JSONResponse({"ok": True})
