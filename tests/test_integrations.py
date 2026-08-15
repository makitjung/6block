# 외부 연동 고장 주입 테스트. 구글·Things3·AI 를 실제로 부르지 않고 실패 상황만 흉내 내
# 그때 앱이 화면을 500 으로 떨구지 않고 조용히 넘어가는지 본다.
import io
import subprocess
import urllib.error

import pytest

import app.config as cfg
import app.integrations.ai as ai
import app.integrations.gcal as gcal
import app.integrations.things as things

ICS_GOOD = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//test//EN
BEGIN:VEVENT
UID:1@test
DTSTAMP:20260815T000000Z
DTSTART:20260815T010000Z
DTEND:20260815T020000Z
SUMMARY:TEST-EVENT
END:VEVENT
END:VCALENDAR
"""


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return 200


@pytest.fixture(autouse=True)
def 캐시비우기():
    gcal._cache.clear()
    things._cache["items"] = None
    things._cache["at"] = 0.0
    yield
    gcal._cache.clear()
    things._cache["items"] = None


# -- 구글 캘린더 -------------------------------------------------------------


def _use_calendars(monkeypatch, urls):
    cals = [{"name": f"C{i}", "color": "yellow", "url": u} for i, u in enumerate(urls)]
    monkeypatch.setattr(cfg, "GCAL_CALENDARS", cals)
    monkeypatch.setattr(gcal, "GCAL_CALENDARS", cals)


def test_캘린더가_안_켜졌으면_빈_목록(monkeypatch):
    _use_calendars(monkeypatch, [])
    assert gcal.events_for_range(__import__("datetime").date(2026, 8, 15),
                                 __import__("datetime").date(2026, 8, 15)) == {}


@pytest.mark.parametrize("boom", [
    urllib.error.URLError("연결 실패"),
    urllib.error.HTTPError("u", 404, "not found", {}, None),
    TimeoutError("느림"),
    OSError("네트워크 없음"),
])
def test_캘린더_주소가_죽어도_예외가_새지_않는다(monkeypatch, boom):
    import datetime

    _use_calendars(monkeypatch, ["https://example.invalid/a.ics"])

    def 터짐(*a, **k):
        raise boom

    monkeypatch.setattr(gcal.urllib.request, "urlopen", 터짐)
    d = datetime.date(2026, 8, 15)
    assert gcal.events_for_range(d, d) == {}


def test_ics_가_쓰레기여도_예외가_새지_않는다(monkeypatch):
    import datetime

    _use_calendars(monkeypatch, ["https://example.invalid/a.ics"])
    monkeypatch.setattr(gcal.urllib.request, "urlopen",
                        lambda *a, **k: _Resp("이건 ics 가 아니다".encode()))
    d = datetime.date(2026, 8, 15)
    assert gcal.events_for_range(d, d) == {}


def test_캘린더_하나가_깨져도_나머지는_나온다(monkeypatch):
    import datetime

    _use_calendars(monkeypatch, ["https://a.invalid/x.ics", "https://b.invalid/y.ics"])

    def 응답(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "a.invalid" in url:
            raise urllib.error.URLError("첫 캘린더 죽음")
        return _Resp(ICS_GOOD)

    monkeypatch.setattr(gcal.urllib.request, "urlopen", 응답)
    d = datetime.date(2026, 8, 15)
    out = gcal.events_for_range(d, d)
    assert out, "앞 캘린더가 죽으면 뒤 캘린더 일정까지 사라진다"
    assert any(e["title"] == "TEST-EVENT" for evs in out.values() for e in evs)


def test_캘린더가_죽어도_오늘_화면은_열린다(client, monkeypatch, real_integrations):
    _use_calendars(monkeypatch, ["https://example.invalid/a.ics"])

    def 터짐(*a, **k):
        raise urllib.error.URLError("연결 실패")

    monkeypatch.setattr(gcal.urllib.request, "urlopen", 터짐)
    assert client.get("/today").status_code == 200
    assert client.get("/week").status_code == 200


# -- Things3 (AppleScript) ---------------------------------------------------


def test_osascript_가_실패하면_빈_목록(monkeypatch, real_integrations):
    import datetime

    monkeypatch.setattr(things.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "권한 없음"))
    assert things.today_tasks(datetime.date.today()) == []


def test_osascript_가_타임아웃해도_빈_목록(monkeypatch, real_integrations):
    import datetime

    def 느림(*a, **k):
        raise subprocess.TimeoutExpired("osascript", 8)

    monkeypatch.setattr(things.subprocess, "run", 느림)
    assert things.today_tasks(datetime.date.today()) == []


def test_things_가_죽어도_오늘_화면은_열린다(client, monkeypatch, real_integrations):
    def 터짐(*a, **k):
        raise OSError("osascript 없음")

    monkeypatch.setattr(things.subprocess, "run", 터짐)
    assert client.get("/today").status_code == 200


def test_할일_제목은_따옴표_세미콜론이_있어도_명령이_되지_않는다(monkeypatch, real_integrations):
    """제목을 스크립트 문자열에 끼워 넣으면 명령 주입이 된다. argv 로 넘겨야 안전하다."""
    잡힌인자 = {}

    def 가짜(args, **k):
        잡힌인자["args"] = args
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(things.subprocess, "run", 가짜)
    악의적제목 = '" & (do shell script "touch /tmp/pwned") & "'
    assert things.add_todo(악의적제목) is True
    args = 잡힌인자["args"]
    assert args[-1] == 악의적제목, "제목이 argv 마지막 인자로 그대로 넘어가야 한다"
    assert 악의적제목 not in args[2], "제목이 AppleScript 본문에 끼어들었다(명령 주입 가능)"


def test_평범한_할일_한_줄을_제대로_읽는다(monkeypatch, real_integrations):
    monkeypatch.setattr(things, "_run", lambda *a, **k: (0, "장보기\t집안일, 급함\tABC123\n"))
    items = things._today_names()
    assert items == [{"name": "장보기", "tags": ["집안일", "급함"], "id": "ABC123"}]


def test_태그와_id_가_없어도_읽는다(monkeypatch, real_integrations):
    monkeypatch.setattr(things, "_run", lambda *a, **k: (0, "제목만\n\n  \n"))
    items = things._today_names()
    assert items == [{"name": "제목만", "tags": [], "id": ""}]


def test_빈_제목_할일은_보내지_않는다(monkeypatch, real_integrations):
    불렸나 = {"n": 0}

    def 세기(*a, **k):
        불렸나["n"] += 1
        return subprocess.CompletedProcess(a, 0, "ok", "")

    monkeypatch.setattr(things.subprocess, "run", 세기)
    assert things.add_todo("   ") is False
    assert 불렸나["n"] == 0


# -- AI ---------------------------------------------------------------------


def test_AI가_설정_안_되어_있으면_None(fresh_db, real_integrations):
    assert ai.enabled() is False
    assert ai.complete("s", "u") is None


def _AI켜기(monkeypatch):
    monkeypatch.setattr(ai, "AI_API_KEY", "sk-테스트")
    import app.db as db
    db.set_setting("ai_base_url", "https://ai.invalid/v1")
    db.set_setting("ai_model", "m")


@pytest.mark.parametrize("payload", [
    b"{}",
    b'{"choices": []}',
    b'{"choices": null}',          # TypeError: NoneType 은 인덱싱이 안 된다
    b'{"choices": "text"}',        # TypeError: 문자열 인덱스는 정수여야 한다
    b'{"choices": 7}',             # TypeError
    b"[1, 2, 3]",                  # 최상위가 배열
    b'"\\uadf8\\ub0e5 \\ubb38\\uc790\\uc5f4"',   # 최상위가 문자열
    b'{"choices": [{"message": {"content": 12345}}]}',   # AttributeError: strip 없음
    b'{"choices": [{}]}',
    b'{"choices": [{"message": {}}]}',
    b'{"choices": [{"message": {"content": null}}]}',
    "이건 JSON 이 아니다".encode(),
    b"",
])
def test_AI_응답이_이상해도_예외가_새지_않는다(fresh_db, monkeypatch, real_integrations, payload):
    _AI켜기(monkeypatch)
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: _Resp(payload))
    assert ai.complete("s", "u") is None


@pytest.mark.parametrize("boom", [
    urllib.error.URLError("연결 실패"),
    urllib.error.HTTPError("u", 500, "server error", {}, None),
    TimeoutError("느림"),
])
def test_AI_호출이_실패해도_None(fresh_db, monkeypatch, real_integrations, boom):
    _AI켜기(monkeypatch)

    def 터짐(*a, **k):
        raise boom

    monkeypatch.setattr(ai.urllib.request, "urlopen", 터짐)
    assert ai.complete("s", "u") is None


def test_AI_상태에_키_값이_들어가지_않는다(fresh_db, monkeypatch, real_integrations):
    _AI켜기(monkeypatch)
    st = ai.status()
    assert st["has_key"] is True
    assert "sk-테스트" not in str(st), "상태 응답에 키 값이 그대로 들어 있다"


def test_AI가_꺼져_있어도_주간_자동세분화가_동작한다(client):
    """AI 미설정이면 규칙기반으로 떨어져야 한다. 여기서 500 이면 폴백이 없는 것이다."""
    from app.common import week_start
    import datetime

    monday = week_start(datetime.date.today()).strftime("%Y-%m-%d")
    client.get("/week")
    res = client.post("/week/decompose-themes", data={"week_start": monday})
    assert res.status_code in (200, 400), res.text


def test_AI가_꺼져_있어도_분석_AI_요약이_동작한다(client):
    res = client.post("/analytics/ai", data={"rng": "7"})
    assert res.status_code in (200, 400), res.text


@pytest.mark.parametrize("payload", [
    b'{"choices": null}',
    b'{"choices": "text"}',
    b"[1, 2, 3]",
    b"<html>502 Bad Gateway</html>",
    b"",
])
def test_AI_주소를_잘못_적어_두어도_화면이_500이_되지_않는다(
        client, monkeypatch, real_integrations, payload):
    """엉뚱한 JSON API 를 AI 주소로 적어 둔 상태. 규칙기반으로 넘어가야 한다."""
    import app.db as db
    from app.common import week_start

    monkeypatch.setattr(ai, "AI_API_KEY", "sk-테스트")
    db.set_setting("ai_base_url", "https://ai.invalid/v1")
    db.set_setting("ai_model", "m")
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: _Resp(payload))

    import datetime

    monday = week_start(datetime.date.today()).strftime("%Y-%m-%d")
    client.get("/week")
    assert client.post("/week/decompose-themes",
                       data={"week_start": monday}).status_code != 500
    assert client.post("/analytics/ai", data={"rng": "7"}).status_code != 500
