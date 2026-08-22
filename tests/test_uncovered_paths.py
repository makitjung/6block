# 감사에서 '한 번도 실행되지 않았다'로 남은 사용자 경로만 모아 확인한다(재시작 버튼·삭제 연쇄·캘린더 쓰기 게이트·.env 경로).
import pathlib
import signal
import types

import pytest

import app.db as db
import app.routes.reflect as reflect_routes
import app.routes.settings as settings_routes
from app.integrations import gcal_write

ROOT = pathlib.Path(__file__).resolve().parent.parent


# -- 설정 탭 재시작 버튼 ------------------------------------------------------


def test_재시작_버튼은_1초_뒤_자기_자신에게_SIGTERM_을_보낸다(client, monkeypatch):
    """진짜로 죽이면 테스트 프로세스가 끝나므로 타이머와 kill 을 가로채고 예약 내용만 본다.

    SIGKILL 이 되면 SQLite WAL 이 깨끗이 닫히지 않는다. 신호가 SIGTERM 인지까지 본다.
    """
    booked = {}
    killed = []

    class FakeTimer:
        def __init__(self, interval, fn):
            booked["interval"], booked["fn"] = interval, fn

        def start(self):
            booked["started"] = True

    monkeypatch.setattr(settings_routes, "threading", types.SimpleNamespace(Timer=FakeTimer))
    monkeypatch.setattr(
        settings_routes, "os",
        types.SimpleNamespace(kill=lambda pid, sig: killed.append((pid, sig)), getpid=lambda: 4242),
    )

    r = client.post("/settings/restart")

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert booked["started"] is True, "타이머를 만들고 start() 를 부르지 않으면 재시작이 안 된다"
    assert booked["interval"] == 1.0, "응답보다 먼저 죽으면 화면이 실패로 보인다"
    assert killed == [], "응답을 돌려주기 전에 죽으면 안 된다"

    booked["fn"]()      # 1초 뒤 타이머가 부를 것을 직접 부른다
    assert killed == [(4242, signal.SIGTERM)]


# -- 고결감 삭제 연쇄(구글에서 지운 것을 로컬에 반영하는 길) -------------------


def _add_reflection(conn, *, kind="고민", review_date=None, source_id=None,
                    event_date="2026-08-22") -> int:
    cur = conn.execute(
        "INSERT INTO reflection (kind, title, text, event_date, review_date, source_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, "제목", "본문", event_date, review_date, source_id, "2026-08-22T09:00:00+09:00"),
    )
    return cur.lastrowid


def _ids(conn):
    return [r["id"] for r in conn.execute("SELECT id FROM reflection ORDER BY id").fetchall()]


def test_원본을_지우면_다시보기_사본도_함께_사라진다(conn):
    """구글 캘린더에서 원본을 지웠을 때 부르는 길. 사본이 남으면 지운 글이 화면에 계속 뜬다."""
    src = _add_reflection(conn, review_date="2026-09-01")
    _add_reflection(conn, source_id=src, event_date="2026-09-01")
    row = conn.execute("SELECT * FROM reflection WHERE id = ?", (src,)).fetchone()

    reflect_routes._cascade_local_delete(conn, row)

    assert _ids(conn) == []


def test_사본을_지우면_원본의_다시_볼_날짜가_풀린다(conn):
    """사본만 지웠는데 원본에 날짜가 남아 있으면 다음 동기화가 사본을 다시 만든다."""
    src = _add_reflection(conn, review_date="2026-09-01")
    copy_id = _add_reflection(conn, source_id=src, event_date="2026-09-01")
    row = conn.execute("SELECT * FROM reflection WHERE id = ?", (copy_id,)).fetchone()

    reflect_routes._cascade_local_delete(conn, row)

    assert _ids(conn) == [src]
    left = conn.execute("SELECT review_date FROM reflection WHERE id = ?", (src,)).fetchone()
    assert left["review_date"] is None


def test_사본이_없는_원본을_지워도_다른_기록은_남는다(conn):
    """source_id 로 지우는 문장이 조건 없이 돌면 남의 기록까지 지운다."""
    keep = _add_reflection(conn, kind="감사")
    target = _add_reflection(conn, kind="결정")
    row = conn.execute("SELECT * FROM reflection WHERE id = ?", (target,)).fetchone()

    reflect_routes._cascade_local_delete(conn, row)

    assert _ids(conn) == [keep]


# -- 캘린더 쓰기 게이트(여기가 조용히 False 면 사용자는 안 올라간 걸 모른다) ----


@pytest.fixture
def sa_keyfile(tmp_path):
    """서비스계정 키파일 흉내. 내용은 client_email 하나면 된다."""
    key = tmp_path / "sa.json"
    key.write_text('{"client_email": "6block@example.iam.gserviceaccount.com"}', encoding="utf-8")
    return key


def test_고결감_쓰기는_캘린더ID와_키파일이_다_있어야_켜진다(real_stubbed, monkeypatch, sa_keyfile):
    enabled = real_stubbed["gcal_write.enabled"]
    monkeypatch.setattr(gcal_write, "_HAS_LIB", True)
    monkeypatch.setattr(gcal_write, "GCAL_SA_KEYFILE", str(sa_keyfile))

    monkeypatch.setattr(gcal_write, "GCAL_WRITE_CALENDAR_ID", "")
    assert enabled() is False

    monkeypatch.setattr(gcal_write, "GCAL_WRITE_CALENDAR_ID", "refl@group.calendar.google.com")
    assert enabled() is True

    monkeypatch.setattr(gcal_write, "GCAL_SA_KEYFILE", str(sa_keyfile.parent / "없는키.json"))
    assert enabled() is False, "키파일이 사라지면 켜진 것으로 보이면 안 된다"

    monkeypatch.setattr(gcal_write, "GCAL_SA_KEYFILE", str(sa_keyfile))
    monkeypatch.setattr(gcal_write, "_HAS_LIB", False)
    assert enabled() is False, "구글 라이브러리가 없으면 꺼져야 한다"


def test_성과_캘린더는_설정값이_env_값을_이긴다(real_stubbed, monkeypatch, sa_keyfile, fresh_db):
    write_enabled = real_stubbed["gcal_write.write_enabled"]
    monkeypatch.setattr(gcal_write, "_HAS_LIB", True)
    monkeypatch.setattr(gcal_write, "GCAL_SA_KEYFILE", str(sa_keyfile))
    monkeypatch.setattr(
        gcal_write, "WRITE_CALENDARS",
        {"achieve": ("gcal_achieve_calendar_id", "", "6block 성과 연결테스트")},
    )

    assert write_enabled("achieve") is False, "설정도 .env 도 비면 꺼져 있어야 한다"

    db.set_setting("gcal_achieve_calendar_id", "achieve@group.calendar.google.com")
    assert write_enabled("achieve") is True
    assert gcal_write.calendar_id("achieve") == "achieve@group.calendar.google.com"

    monkeypatch.setattr(
        gcal_write, "WRITE_CALENDARS",
        {"achieve": ("gcal_achieve_calendar_id", "env@group.calendar.google.com", "라벨")},
    )
    assert gcal_write.calendar_id("achieve") == "achieve@group.calendar.google.com"

    db.set_setting("gcal_achieve_calendar_id", "")
    assert gcal_write.calendar_id("achieve") == "env@group.calendar.google.com"


def test_캘린더ID가_있어도_키파일이_없으면_쓰기는_꺼진다(real_stubbed, monkeypatch, tmp_path, fresh_db):
    """키파일 경로만 틀려도 저장은 되고 캘린더만 조용히 안 올라간다. 그 갈림길을 못박는다."""
    write_enabled = real_stubbed["gcal_write.write_enabled"]
    monkeypatch.setattr(gcal_write, "_HAS_LIB", True)
    monkeypatch.setattr(gcal_write, "GCAL_SA_KEYFILE", str(tmp_path / "없는키.json"))
    monkeypatch.setattr(
        gcal_write, "WRITE_CALENDARS",
        {"events": ("gcal_events_calendar_id", "events@group.calendar.google.com", "라벨")},
    )
    assert write_enabled("events") is False


def test_서비스계정_이메일은_키파일에서_읽고_못_읽으면_빈칸(real_stubbed, monkeypatch, sa_keyfile, tmp_path):
    """설정 탭이 이 값을 '이 주소에 캘린더를 공유하세요'로 보여 준다. 터지면 안내가 사라진다."""
    email = real_stubbed["gcal_write.service_account_email"]

    monkeypatch.setattr(gcal_write, "GCAL_SA_KEYFILE", str(sa_keyfile))
    assert email() == "6block@example.iam.gserviceaccount.com"

    monkeypatch.setattr(gcal_write, "GCAL_SA_KEYFILE", str(tmp_path / "없는키.json"))
    assert email() == ""

    broken = tmp_path / "깨진키.json"
    broken.write_text("{ 이건 JSON 이 아니다", encoding="utf-8")
    monkeypatch.setattr(gcal_write, "GCAL_SA_KEYFILE", str(broken))
    assert email() == ""


# -- 설정 탭 .env 편집기가 여는 파일 -------------------------------------------


def test_env_편집기는_프로젝트_루트의_env_를_가리킨다(real_stubbed):
    """테스트에서는 늘 가짜 .env 로 갈아끼워져 있어 진짜 경로 계산이 한 번도 안 돌았다."""
    real_path = real_stubbed["settings._env_file_path"]()

    assert real_path == ROOT / ".env"
    assert real_path.name == ".env"
