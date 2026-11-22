import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event
from types import SimpleNamespace

import pytest

from app import db
from app.common import today_str
from app.integrations import gcal_write, reflection_sync
from app.routes import reflect, settings
from scripts import backup


def test_reflect_renders_current_template_with_previous_server_context(client, monkeypatch):
    current_context = reflect._reflect_ctx

    def previous_context(*args, **kwargs):
        ctx = current_context(*args, **kwargs)
        for key in ("sync_status", "page", "pages", "total", "tag", "focus"):
            del ctx[key]
        ctx["gcal_write_on"] = True
        return ctx

    assert client.post("/reflect/add", data={"kind": "고민", "title": "검색할 기록"}).json()["ok"]
    monkeypatch.setattr(reflect, "_reflect_ctx", previous_context)

    for path in ("/reflect", "/reflect?q=검색할"):
        response = client.get(path)
        assert response.status_code == 200
        assert "검색할 기록" in response.text
        assert 'id="rf-sync-status"' in response.text
        assert 'data-page="1"' in response.text


def test_day_review_shows_original_once_and_legacy_copy_link_edits_original(client):
    due_date = (date.fromisoformat(today_str()) + timedelta(days=1)).isoformat()
    created = client.post("/reflect/add", data={
        "kind": "고민", "title": "다시 볼 기록", "text": "원본", "review_date": due_date,
    }).json()
    assert created["ok"]
    original = created["id"]
    with db.get_conn() as conn:
        copy_id = conn.execute("SELECT id FROM reflection WHERE source_id = ?", (original,)).fetchone()[0]

    day = client.get(f"/day/{due_date}")
    assert day.text.count('class="due-item"') == 1
    assert f'data-reflect-id="{original}"' in day.text
    assert client.post(f"/reflect/review-note/{copy_id}", data={"note": "오늘 다시 읽음"}).json()["ok"]
    with db.get_conn() as conn:
        assert conn.execute("SELECT review_note FROM reflection WHERE id = ?", (original,)).fetchone()[0] == "오늘 다시 읽음"


class Calendar:
    """Google 서비스와 같은 지연·실패 경계를 가진 메모리 캘린더."""

    def __init__(self):
        self.items = {}
        self.list_override = None
        self.fail_update = self.fail_delete = self.fail_list = False
        self.update_entered = Event()
        self.finish_update = None

    def events(self):
        return self

    def insert(self, *, calendarId, body):
        def insert():
            self.items[body["id"]] = copy.deepcopy(body)
            return self.items[body["id"]]
        return SimpleNamespace(execute=insert)

    def patch(self, *, calendarId, eventId, body):
        def patch():
            self.update_entered.set()
            if self.finish_update is not None:
                assert self.finish_update.wait(4)
            if self.fail_update:
                raise OSError("연결 중단")
            self.items[eventId].update(copy.deepcopy(body))
            return self.items[eventId]
        return SimpleNamespace(execute=patch)

    def delete(self, *, calendarId, eventId):
        def delete():
            if self.fail_delete:
                raise OSError("연결 중단")
            self.items.pop(eventId, None)
        return SimpleNamespace(execute=delete)

    def list(self, **query):
        def list_items():
            if self.fail_list:
                raise OSError("연결 중단")
            items = self.items.values() if self.list_override is None else self.list_override
            return {"items": [copy.deepcopy(it) for it in items]}
        return SimpleNamespace(execute=list_items)


@pytest.fixture
def calendar(monkeypatch):
    remote = Calendar()
    monkeypatch.setattr(gcal_write, "enabled", lambda: True)
    monkeypatch.setattr(gcal_write, "GCAL_WRITE_CALENDAR_ID", "fixture-reflect")
    monkeypatch.setattr(gcal_write, "_svc", lambda: remote)
    monkeypatch.setattr(gcal_write, "_list_cache", {"at": 0.0, "key": None, "items": None})
    monkeypatch.setattr(reflection_sync, "_last_events", None)
    monkeypatch.setattr(reflection_sync, "_last_pull", "")
    monkeypatch.setattr(reflection_sync, "_retry_pull_at", 0.0)
    return remote


def _local_archive(client):
    return client.get("/reflect/api/items?sync=0").json()["items"]


def test_reflection_edit_and_delete_survive_failed_gcal_sync(client, calendar):
    entry = client.post("/reflect/add", data={"title": "기존", "text": "처음 기록"}).json()
    item_id = entry["id"]
    event = next(iter(calendar.items.values()))
    event_id = event["id"]
    stale = copy.deepcopy(event)
    calendar.fail_update = True
    response = client.post(f"/reflect/update/{item_id}", data={
        "title": "바꾼 제목", "text": "바꾼 내용\n#본문", "tags": "#태그", "event_date": "2026-11-10",
    })
    assert response.json()["sync_state"] == "pending"
    assert client.get("/reflect/list?force=1").json()["sync"]["failed"] == 1
    item, = _local_archive(client)
    assert (item["title"], item["text"], item["event_date"], item["synced"]) == (
        "바꾼 제목", "바꾼 내용\n#본문", "2026-11-10", 0,
    )

    calendar.fail_update = False
    assert client.post(f"/reflect/sync/{item_id}").json()["synced"]
    assert calendar.items[event_id]["start"] == {"date": "2026-11-10"}
    calendar.list_override = [stale]
    assert client.get("/reflect/list?force=1").json()["sync"]["state"] == "ok"
    assert _local_archive(client)[0]["event_date"] == "2026-11-10"
    calendar.list_override = None
    assert client.get("/reflect/list?force=1").json()["sync"]["state"] == "ok"
    assert _local_archive(client)[0]["text"] == "바꾼 내용\n#본문"

    calendar.fail_delete = True
    assert client.post(f"/reflect/delete/{item_id}").json()["sync_state"] == "pending"
    assert client.get("/reflect/list?force=1").json()["sync"]["failed"] == 1
    assert _local_archive(client) == []
    calendar.fail_delete = False
    assert client.get("/reflect/list?force=1").json()["sync"]["pending"] == 0
    assert calendar.items == {}
    assert _local_archive(client) == []
    calendar.fail_list = True
    assert client.get("/reflect/list?force=1").json()["sync"]["state"] == "error"


def test_slow_reflection_update_does_not_lock_sqlite_and_newer_revision_wins(client, calendar):
    item_id = client.post("/reflect/add", data={"title": "기존", "text": "기록"}).json()["id"]
    calendar.finish_update = Event()
    with ThreadPoolExecutor(max_workers=1) as runner:
        first = runner.submit(client.post, f"/reflect/update/{item_id}",
                              data={"title": "첫 수정", "text": "첫 본문"})
        try:
            assert calendar.update_entered.wait(2)
            assert client.post("/inbox/add", data={"text": "동시에 저장"}).status_code == 200
            assert client.post(f"/reflect/update/{item_id}", data={
                "title": "둘째 수정", "text": "둘째 본문",
            }).json()["ok"]
        finally:
            calendar.finish_update.set()
        assert first.result(timeout=5).json()["ok"]
    assert _local_archive(client)[0]["title"] == "둘째 수정"
    assert client.get("/reflect/list?force=1").json()["sync"]["pending"] == 0
    assert next(iter(calendar.items.values()))["summary"] == "[고민] 둘째 수정"


def test_archive_search_filters_all_records_before_pagination(client):
    with db.get_conn() as conn:
        rows = [("감사", f"최근 기록 {i}", "내용", "#최근", "2026-11-20", "2026-11-20T08:00:00+09:00")
                for i in range(501)]
        rows.append(("결정", "아주 오래된 우주 기록", "기록", "#나중", "2020-01-01", "2020-01-01T08:00:00+09:00"))
        conn.executemany(
            "INSERT INTO reflection (kind, title, text, tags, event_date, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    api = client.get("/reflect/api/items?sync=0").json()
    assert len(api["items"]) == 500 and api["total"] == 502
    second = client.get("/reflect/api/items?sync=0&page=2").json()
    assert second["items"][-1]["title"] == "아주 오래된 우주 기록"
    page = client.get("/reflect/list", params={"q": "오래된", "kind": "결정", "tag": "나중", "sync": 0}).json()
    assert page["total"] == 1 and "아주 오래된 우주 기록" in page["list_html"]
    older_id = second["items"][-1]["id"]
    jump = client.get("/reflect/list", params={"focus": older_id, "sync": 0}).json()
    assert jump["page"] == jump["pages"] and f'id="rf-card-{older_id}"' in jump["list_html"]


def test_reflection_add_replays_same_request_as_one_record(client, calendar):
    data = {"title": "중복 방지", "text": "기록", "request_key": "fixture-request"}
    first = client.post("/reflect/add", data=data).json()
    retry = client.post("/reflect/add", data=data).json()
    assert retry["id"] == first["id"]
    assert len(_local_archive(client)) == len(calendar.items) == 1


def test_existing_v9_database_gains_sync_tables_and_keeps_rows(tmp_path, monkeypatch):
    db_file = tmp_path / "previous.db"
    with sqlite3.connect(db_file) as conn:
        conn.executescript(
            "CREATE TABLE reflection (id INTEGER PRIMARY KEY, kind TEXT, title TEXT, text TEXT, "
            "tags TEXT, event_date TEXT, review_date TEXT, review_note TEXT, source_id INTEGER, "
            "uid TEXT, created_at TEXT, gcal_event_id TEXT, synced INTEGER DEFAULT 0);"
            "INSERT INTO reflection (kind, title, text, event_date) "
            "VALUES ('감사', '기존 제목', '보존 대상', '2025-01-01'); PRAGMA user_version = 9;"
        )
    with monkeypatch.context() as patch:
        patch.setattr(db, "DB_PATH", db_file)
        db.init_db()
        with db.get_conn() as conn:
            assert conn.execute("SELECT text FROM reflection").fetchone()[0] == "보존 대상"
            assert conn.execute("SELECT COUNT(*) FROM reflection_sync_job").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM reflection_add_request").fetchone()[0] == 0


def test_backup_staged_restore_and_failed_same_day_replacement(client, tmp_path, monkeypatch):
    local, secondary = tmp_path / "local", tmp_path / "secondary"
    monkeypatch.setattr(backup, "BACKUP_DIR", local)
    monkeypatch.setattr(backup, "CLOUD_BACKUP_DIR", secondary)
    monkeypatch.setattr(settings, "BACKUP_DIR", local)
    monkeypatch.setattr(settings, "CLOUD_BACKUP_DIR", secondary)
    monkeypatch.setattr(backup, "_trim_logs", lambda: None)
    assert client.post("/settings/backup").json()["ok"]
    file_name = "blocks-" + today_str().replace("-", "") + ".sql"
    local_file, other_file = local / file_name, secondary / file_name
    original = local_file.read_bytes()
    assert original == other_file.read_bytes()
    with sqlite3.connect(":memory:") as restored:
        restored.executescript(original.decode())
        assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    assert client.post("/inbox/add", data={"text": "다음 스냅샷"}).json()["ok"]
    replace = type(local_file).replace

    def blocked_replace(source, target):
        if source.parent == secondary:
            raise OSError("보조 폴더 교체 실패")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(type(local_file), "replace", blocked_replace)
        response = client.post("/settings/backup")
    assert response.status_code == 500
    assert [t["ok"] for t in response.json()["targets"]] == [True, False]
    assert local_file.read_bytes() != original and other_file.read_bytes() == original
    assert all(item["verified"] for item in settings._backup_status())
    assert not list(secondary.glob(".blocks-*"))
    local_file.write_text("BEGIN TRANSACTION;\n", encoding="utf-8")
    assert settings._backup_status()[0]["reason"] == "작성 미완료"
