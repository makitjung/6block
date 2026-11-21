"""고결감 기록과 함께 저장한 캘린더 작업을 전송하고 조회 결과를 반영한다."""
import threading
import time
from datetime import datetime, timedelta
from uuid import uuid4

from app.common import KST
from app.db import get_conn, uid_from_created
from app.integrations import gcal_write

_sync_lock = threading.Lock()
_last_pull = ""
_RETRY_SECONDS = 120


def queue_save(conn, item_id, *, move_date=False, manual=False):
    row = conn.execute("SELECT gcal_event_id FROM reflection WHERE id = ?", (item_id,)).fetchone()
    event_id = row["gcal_event_id"]
    if not event_id and not (manual or gcal_write.enabled()):
        return False
    old_job = conn.execute(
        "SELECT * FROM reflection_sync_job WHERE item_id = ?", (item_id,)
    ).fetchone()
    calendar = old_job["calendar_id"] if old_job else gcal_write.GCAL_WRITE_CALENDAR_ID
    operation = "update" if event_id else "create"
    if old_job and not old_job["done"]:
        if old_job["operation"] == "create":
            operation = "create"
        move_date = move_date or bool(old_job["move_date"])
    event_id = event_id or uuid4().hex
    conn.execute(
        "UPDATE reflection SET gcal_event_id = ?, synced = 0 WHERE id = ?", (event_id, item_id)
    )
    _queue(conn, calendar, event_id, item_id, operation, move_date)
    return True


def _queue(conn, calendar, event_id, item_id, operation, move_date=False):
    conn.execute(
        "INSERT INTO reflection_sync_job (calendar_id, event_id, item_id, operation, revision, move_date) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (calendar_id, event_id) DO UPDATE SET "
        "item_id = excluded.item_id, operation = excluded.operation, revision = excluded.revision, "
        "move_date = excluded.move_date, done = 0, confirmed = 0, retry_at = 0, error = ''",
        (calendar, event_id, item_id, operation, uuid4().hex, int(move_date)),
    )


def remove(conn, row, *, remote_deleted=False):
    """원본과 사본을 정리하고 아직 남은 캘린더 이벤트를 삭제 대기열에 넣는다."""
    if row["source_id"]:
        conn.execute("UPDATE reflection SET review_date = NULL WHERE id = ?", (row["source_id"],))
        related = [row]
    else:
        related = [row, *conn.execute("SELECT * FROM reflection WHERE source_id = ?", (row["id"],))]
    queued = False
    for item in related:
        if item["gcal_event_id"]:
            job = conn.execute(
                "SELECT calendar_id FROM reflection_sync_job WHERE item_id = ?", (item["id"],)
            ).fetchone()
            calendar = job["calendar_id"] if job else gcal_write.GCAL_WRITE_CALENDAR_ID
            _queue(conn, calendar, item["gcal_event_id"], None, "delete")
            if remote_deleted and item["id"] == row["id"]:
                conn.execute(
                    "UPDATE reflection_sync_job SET done = 1 WHERE calendar_id = ? AND event_id = ?",
                    (calendar, item["gcal_event_id"]),
                )
            else:
                queued = True
        conn.execute("DELETE FROM reflection WHERE id = ?", (item["id"],))
    return queued


def _send(job):
    operation = job["operation"]
    event_id = job["event_id"]
    if operation == "delete":
        return event_id if gcal_write.delete_event(event_id) else None
    args = [job["kind"], job["title"], job["text"], job["tags"], job["event_date"]]
    if job["source_id"]:
        args.insert(2, job["parent_note"] or "")
        create, update = gcal_write.create_review_copy, gcal_write.update_review_copy
    else:
        create, update = gcal_write.create_event, gcal_write.update_event
    if operation == "create":
        return create(*args, event_id, job["revision"])
    changed = update(event_id, *args, job["revision"], bool(job["move_date"]))
    return event_id if changed else None


def _send_pending(*, force=False, item_id=None):
    calendar = gcal_write.GCAL_WRITE_CALENDAR_ID
    with get_conn() as conn:
        jobs = conn.execute(
            "SELECT j.*, r.kind, r.title, r.text, r.tags, r.event_date, r.source_id, "
            "p.review_note AS parent_note FROM reflection_sync_job j "
            "LEFT JOIN reflection r ON r.id = j.item_id AND r.gcal_event_id = j.event_id "
            "LEFT JOIN reflection p ON p.id = r.source_id "
            "WHERE j.calendar_id = ? AND j.done = 0 AND (? OR j.retry_at <= ?) "
            "AND (? IS NULL OR j.item_id = ?) ORDER BY j.retry_at, j.event_id LIMIT 20",
            (calendar, force, int(time.time()), item_id, item_id),
        ).fetchall()
    for job in jobs:
        try:
            applied_id = _send(job)
        except Exception:
            applied_id = None
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE reflection_sync_job SET done = ?, error = ?, retry_at = ? "
                "WHERE calendar_id = ? AND event_id = ? AND revision = ?",
                (bool(applied_id), "" if applied_id else "캘린더 반영 실패",
                 0 if applied_id else int(time.time()) + _RETRY_SECONDS,
                 calendar, job["event_id"], job["revision"]),
            )
            # 네트워크 요청이 도는 동안 새 저장이 있었다면 새 작업을 그대로 남긴다.
            if not (cur.rowcount and applied_id and job["item_id"]):
                continue
            conn.execute("UPDATE reflection SET synced = 1, gcal_event_id = ? WHERE id = ?",
                         (applied_id, job["item_id"]))
            if applied_id != job["event_id"]:
                conn.execute(
                    "UPDATE reflection_sync_job SET event_id = ? WHERE calendar_id = ? AND event_id = ?",
                    (applied_id, calendar, job["event_id"]),
                )


def _merge(events):
    calendar = gcal_write.GCAL_WRITE_CALENDAR_ID
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # 목록에서 누락된 것만으로는 삭제를 판단할 수 없다. 캘린더가 취소라고 답한 것만 반영한다.
        for event in sorted(events, key=lambda e: not e.get("cancelled")):
            event_id = event["id"]
            job = conn.execute(
                "SELECT * FROM reflection_sync_job WHERE calendar_id = ? AND event_id = ?",
                (calendar, event_id),
            ).fetchone()
            if job and (not job["done"] or job["operation"] == "delete"):
                continue
            row = conn.execute("SELECT * FROM reflection WHERE gcal_event_id = ?", (event_id,)).fetchone()
            if event.get("cancelled"):
                if row and (job is None or job["confirmed"]):
                    remove(conn, row, remote_deleted=True)
                continue
            if job:
                if event.get("revision") != job["revision"]:
                    continue
                if not job["confirmed"]:
                    conn.execute(
                        "UPDATE reflection_sync_job SET confirmed = 1 WHERE calendar_id = ? AND event_id = ?",
                        (calendar, event_id),
                    )
            if row and row["source_id"]:
                continue
            values = [event[k] for k in ("kind", "title", "content", "tags", "date")]
            if row:
                # Google에 보낸 제목 길이와 태그 표기 차이로 로컬 원문을 다시 덮지 않는다.
                tags_equal = gcal_write._hashtags(row["tags"]) == gcal_write._hashtags(values[3])
                if tags_equal:
                    values[3] = row["tags"]
                if job and values[1] == (row["title"] or "")[:120]:
                    values[1] = row["title"]
                if tuple(values) != tuple(row[k] for k in ("kind", "title", "text", "tags", "event_date")):
                    conn.execute("UPDATE reflection SET kind=?, title=?, text=?, tags=?, event_date=? WHERE id=?",
                                 (*values, row["id"]))
            else:
                conn.execute(
                    "INSERT INTO reflection (kind, title, text, tags, event_date, created_at, "
                    "gcal_event_id, synced, uid) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (*values, now, event_id, uid_from_created(now)),
                )


def status(conn):
    row = conn.execute("SELECT COUNT(*) AS pending, COUNT(NULLIF(error, '')) AS failed "
                       "FROM reflection_sync_job WHERE done = 0").fetchone()
    return {**dict(row), "last_pull": _last_pull}


def run(*, force=False, item_id=None, pull=False):
    """한 번에 한 묶음만 전송한다. 네트워크 중에는 SQLite 연결을 잡지 않는다."""
    global _last_pull
    if not gcal_write.enabled() and item_id is None:
        return {"state": "off"}
    if not _sync_lock.acquire(blocking=False):
        return {"state": "busy"}
    try:
        _send_pending(force=force, item_id=item_id)
        if pull:
            try:
                if force:
                    gcal_write.invalidate_cache()
                today = datetime.now(KST).date()
                window = timedelta(days=730)
                events = gcal_write.list_reflection_events(today - window, today + window, strict=True)
                _merge(events)
                _last_pull = datetime.now(KST).isoformat(timespec="seconds")
            except Exception:
                return {"state": "error", "error": "캘린더 기록을 가져오지 못했습니다"}
        return {"state": "ok"}
    finally:
        _sync_lock.release()
