# 서비스계정으로 구글 캘린더 '고결감'에 고민·결정·감사 이벤트를 만들고 읽고 고치는 양방향 연동 모듈
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import (
    GCAL_SA_KEYFILE,
    GCAL_WRITE_ACHIEVE_CALENDAR_ID,
    GCAL_WRITE_CALENDAR_ID,
    GCAL_WRITE_EVENTS_CALENDAR_ID,
)

# events 범위만 요청한다(캘린더 자체 생성/삭제 권한은 필요 없음).
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
_KINDS = ("고민", "결정", "감사")
_KIND_ALIAS = {"감상": "감사", "결심": "결정"}  # 옛 명칭 호환
_MARKER = "(6block 고결감)"
_KST = ZoneInfo("Asia/Seoul")

try:
    import google_auth_httplib2
    import httplib2
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    _HAS_LIB = True
except Exception:  # 라이브러리 미설치 시 캘린더 쓰기만 비활성, 앱·DB 저장은 정상.
    _HAS_LIB = False

_services = threading.local()
_list_cache: dict = {"at": 0.0, "key": None, "items": None}
# 고결감 탭을 열어 두면 60초마다 목록을 새로 그린다. 읽기 캐시도 60초면 그때마다 구글을
# 실제로 호출해 하루 1440번이 된다. 캐시를 5분으로 두어 하루 288번으로 줄인다.
# 내가 6block에서 만들고 고치고 지운 것은 그때 캐시를 비우므로 즉시 반영되고,
# 구글 쪽에서 직접 고친 것만 최대 5분 늦게 보인다('지금 동기화' 버튼은 기다리지 않는다).
_LIST_TTL = 300


def _credentials_configured() -> bool:
    return bool(GCAL_SA_KEYFILE and _HAS_LIB and os.path.isfile(GCAL_SA_KEYFILE))


def enabled() -> bool:
    return bool(GCAL_WRITE_CALENDAR_ID and _credentials_configured())


def invalidate_cache():
    """수동 동기화에서 60초 읽기 캐시를 비워 다음 조회가 즉시 구글을 다시 읽게 한다."""
    _list_cache["items"] = None


def _svc():
    """서비스계정 HTTP 연결을 스레드별로 재사용한다."""
    if not _credentials_configured():
        return None
    service = getattr(_services, "calendar", None)
    if service is not None:
        return service
    creds = service_account.Credentials.from_service_account_file(
        GCAL_SA_KEYFILE, scopes=_SCOPES
    )
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=12))
    service = build("calendar", "v3", http=http, cache_discovery=False)
    _services.calendar = service
    return service


def status() -> dict:
    """헬스체크용. 라이브러리·설정·실제 캘린더 접근 가능 여부."""
    if not _HAS_LIB:
        return {"enabled": False, "reason": "라이브러리 미설치(google-api-python-client)"}
    if not GCAL_WRITE_CALENDAR_ID:
        return {"enabled": False, "reason": "GCAL_WRITE_CALENDAR_ID 미설정"}
    if not GCAL_SA_KEYFILE or not os.path.exists(GCAL_SA_KEYFILE):
        return {"enabled": False, "reason": "서비스계정 키파일 없음"}
    try:
        _svc().events().list(calendarId=GCAL_WRITE_CALENDAR_ID, maxResults=1).execute()
        return {"enabled": True, "calendar": GCAL_WRITE_CALENDAR_ID}
    except Exception as e:
        return {"enabled": False, "reason": str(e)[:160]}


def _next_day(d: str) -> str:
    """종일 이벤트의 end.date는 종료 다음날(배타적)이라 하루 더한다.

    date 의 상한(9999-12-31)이면 다음날이 없어 OverflowError 가 난다. 그대로 두면
    고결감 저장 전체가 500 이 되므로, 캘린더에 못 올리는 날짜라고 ValueError 로 알린다.
    부르는 쪽은 이미 예외를 잡아 '캘린더 반영 실패'로 처리하고 기록 자체는 저장한다.
    (평소에는 common._parse_date 가 MAX_YEAR 로 먼저 막아 여기까지 오지 않는다.)
    """
    y, m, dd = (int(x) for x in d.split("-"))
    try:
        return (date(y, m, dd) + timedelta(days=1)).isoformat()
    except OverflowError:
        raise ValueError(f"캘린더에 올릴 수 없는 날짜입니다: {d}") from None


def _hashtags(tags: str) -> str:
    """'진로, 건강' → '#진로 #건강' (구글 캘린더 검색에 걸리도록 해시태그로)."""
    toks = [t.strip().lstrip("#") for t in re.split(r"[,\s]+", tags or "") if t.strip()]
    return " ".join("#" + t for t in toks)


def _build_description(content: str, tags: str) -> str:
    """내용 + 해시태그 + 표식으로 설명란을 만든다(검색·역파싱 가능하게)."""
    parts = []
    if (content or "").strip():
        parts.append(content.strip())
    hs = _hashtags(tags)
    if hs:
        parts.append(hs)
    parts.append(_MARKER)
    return "\n\n".join(parts)


def _norm_kind(kind: str) -> str:
    k = (kind or "").strip()
    k = _KIND_ALIAS.get(k, k)
    return k if k in _KINDS else "고민"


def parse_summary(summary: str):
    """'[종류] 제목' → (kind, title). 형식이 아니면 (고민, 통째 제목).

    종류 자리에는 대괄호가 올 수 없다. 예전 정규식은 (.+?) 가 비탐욕적이라
    '[고민 [부제]] 제목' 에서 첫 ']' 까지만 종류로 보고 '] 제목' 만 제목에 남겨,
    약속한 '통째 제목' 대신 앞부분이 잘린 제목을 돌려줬다.
    제목 안의 대괄호('[결정] [중요] 회의')는 종전대로 그대로 보존된다.
    """
    m = re.match(r"^\s*\[([^\[\]]+)\]\s*(.*)$", summary or "")
    if m:
        return _norm_kind(m.group(1)), m.group(2).strip()
    return "고민", (summary or "").strip()


def parse_description(desc: str, tagged: str | None = None):
    """설명란 → (content, tags). 표식·해시태그 줄을 걷어내 내용을 복원하고 태그를 뽑는다."""
    if not desc:
        return "", ""
    if tagged in ("0", "1") and desc.rstrip().endswith(_MARKER):
        body = desc.rstrip().removesuffix(_MARKER).rstrip()
        if tagged == "1":
            content, _, last = body.rpartition("\n")
            if re.fullmatch(r"(?:#\S+\s*)+", last):
                return content.rstrip(), " ".join(t.lstrip("#") for t in last.split())
        return body, ""
    tags = " ".join(t.lstrip("#") for t in re.findall(r"#\S+", desc))
    body = desc.replace(_MARKER, "")
    kept = [
        ln for ln in body.splitlines()
        if not re.fullmatch(r"\s*(#\S+\s*)+", ln or "")
    ]
    return "\n".join(kept).strip(), tags


def _reflection_body(kind, title, content, tags, event_date=None, revision=""):
    kind = _norm_kind(kind)
    private = {"sixblock": "reflection", "kind": kind}
    if revision:
        private["sixblockRevision"] = revision
        private["sixblockTagged"] = "1" if _hashtags(tags) else "0"
    body = {
        "summary": f"[{kind}] {(title or '').strip()[:120]}",
        "description": _build_description(content, tags),
        "extendedProperties": {"private": private},
    }
    if event_date is not None:
        body["start"] = {"date": event_date}
        body["end"] = {"date": _next_day(event_date)}
    return body


def _http_status(exc):
    return getattr(getattr(exc, "resp", None), "status", None)


def create_event(kind: str, title: str, content: str, tags: str, event_date: str,
                 event_id=None, revision=""):
    """새 종일 이벤트를 만든다. 재시도에서는 같은 이벤트 ID를 계속 쓴다."""
    svc = _svc()
    if svc is None:
        return None
    body = _reflection_body(kind, title, content, tags, event_date, revision)
    if event_id:
        body["id"] = event_id
    try:
        ev = svc.events().insert(calendarId=GCAL_WRITE_CALENDAR_ID, body=body).execute()
    except Exception as exc:
        if not event_id or _http_status(exc) != 409:
            raise
        body.pop("id")
        ev = svc.events().patch(calendarId=GCAL_WRITE_CALENDAR_ID, eventId=event_id,
                                body=body).execute()
    _list_cache["items"] = None  # 캐시 무효화(방금 만든 게 즉시 보이도록)
    return ev.get("id") or event_id


def service_account_email() -> str:
    """캘린더 공유 안내용 서비스계정 이메일(키파일의 client_email)."""
    try:
        import json as _json

        with open(GCAL_SA_KEYFILE, encoding="utf-8") as f:
            return _json.load(f).get("client_email", "")
    except Exception:
        return ""


# 쓰기용 캘린더 두 곳. 오늘 탭에서 만든 '일정'과, 오늘 '달성' 3줄을 올리는 '성과'다.
# 값은 설정 탭(app_settings)에 넣은 것이 우선이고 없으면 .env 를 쓴다.
# 셋째 캘린더가 생기면 여기 한 줄만 늘리면 된다.
WRITE_CALENDARS = {
    "events": ("gcal_events_calendar_id", GCAL_WRITE_EVENTS_CALENDAR_ID, "6block 연결테스트"),
    "achieve": ("gcal_achieve_calendar_id", GCAL_WRITE_ACHIEVE_CALENDAR_ID, "6block 성과 연결테스트"),
}


def calendar_id(which: str) -> str:
    """쓰기용 캘린더 ID. 설정에 넣은 값이 우선, 없으면 .env 값."""
    key, env_value, _label = WRITE_CALENDARS[which]
    try:
        from app.db import get_settings

        v = (get_settings().get(key) or "").strip()
    except Exception:
        v = ""
    return v or env_value


def write_enabled(which: str) -> bool:
    """그 캘린더에 쓸 수 있는지(캘린더 ID + 서비스계정 키파일 + 라이브러리)."""
    return bool(calendar_id(which) and _credentials_configured())


def test_write(which: str) -> dict:
    """그 캘린더에 테스트 이벤트를 만들고 즉시 지워 쓰기 권한을 확인한다.

    캘린더를 서비스계정에 '읽기'로만 공유하면 조회는 되고 쓰기만 실패하므로,
    실제로 한 건 만들어 봐야 연결 여부를 알 수 있다.
    """
    cal = calendar_id(which)
    if not cal:
        return {"ok": False, "error": "캘린더 ID가 비어 있습니다"}
    svc = _svc()
    if svc is None:
        return {"ok": False, "error": "서비스계정 비활성(키파일 확인)"}
    label = WRITE_CALENDARS[which][2]
    d1 = (date.today() + timedelta(days=1)).isoformat()
    d2 = (date.today() + timedelta(days=2)).isoformat()
    try:
        ev = svc.events().insert(
            calendarId=cal,
            body={"summary": f"[{label}] (자동삭제)",
                  "start": {"date": d1}, "end": {"date": d2}},
        ).execute()
        eid = ev.get("id")
    except Exception as e:
        return {"ok": False, "error": "쓰기 실패(공유가 '변경 권한'인지 확인): " + str(e)[:140]}
    try:
        svc.events().delete(calendarId=cal, eventId=eid).execute()
    except Exception:
        return {"ok": True, "warn": "생성됐으나 삭제 실패(테스트 이벤트가 남았을 수 있음)"}
    return {"ok": True}


def create_calendar_event(summary: str, date_str: str, time_hhmm: str | None = None):
    """오늘 탭에서 만든 일정을 일정용 캘린더에 생성한다. 시간 있으면 1시간 블록, 없으면 종일."""
    cal = calendar_id("events")
    svc = _svc()
    if svc is None or not cal:
        return None
    summary = (summary or "").strip()[:200]
    if time_hhmm and re.match(r"^\d{2}:\d{2}$", time_hhmm):
        sm = int(time_hhmm[:2]) * 60 + int(time_hhmm[3:5])
        em = min(sm + 60, 23 * 60 + 59)
        body = {
            "summary": summary,
            "start": {"dateTime": f"{date_str}T{time_hhmm}:00", "timeZone": "Asia/Seoul"},
            "end": {"dateTime": f"{date_str}T{em // 60:02d}:{em % 60:02d}:00",
                    "timeZone": "Asia/Seoul"},
        }
    else:
        body = {
            "summary": summary,
            "start": {"date": date_str},
            "end": {"date": _next_day(date_str)},
        }
    ev = svc.events().insert(calendarId=cal, body=body).execute()
    return ev.get("id")


# ---- '성과' 캘린더: 오늘 달성을 종일 이벤트로 기록(설명란에 1. 2. 3.) -------------


def _achieve_description(items) -> str:
    """달성 항목 리스트를 빈 칸은 빼고 '1. 2. 3.' 형식으로 번호를 다시 매겨 설명란을 만든다."""
    lines = [(x or "").strip() for x in items if (x or "").strip()]
    return "\n".join(f"{i + 1}. {x}" for i, x in enumerate(lines))


def upsert_achievement_event(date_str: str, items, existing_event_id: str | None = None):
    """그날 달성을 성과 캘린더에 종일 이벤트 1개로 만들거나 갱신한다.

    항목이 모두 비면 기존 이벤트를 지우고 None을 돌려준다. 항목이 있으면 event id를 돌려준다.
    존재하던 이벤트가 수동 삭제돼 patch가 실패하면 새로 만든다(중복 방지는 저장된 id로).
    """
    cal = calendar_id("achieve")
    svc = _svc()
    if svc is None or not cal:
        return existing_event_id
    desc = _achieve_description(items)
    if not desc:
        if existing_event_id:
            try:
                svc.events().delete(calendarId=cal, eventId=existing_event_id).execute()
            except Exception:
                pass
        return None
    body = {
        "summary": "성과",
        "description": desc,
        "start": {"date": date_str},
        "end": {"date": _next_day(date_str)},
        "extendedProperties": {"private": {"sixblock": "achievement"}},
    }
    try:
        if existing_event_id:
            ev = svc.events().patch(
                calendarId=cal, eventId=existing_event_id, body=body
            ).execute()
            return ev.get("id") or existing_event_id
        ev = svc.events().insert(calendarId=cal, body=body).execute()
        return ev.get("id")
    except Exception:
        try:
            ev = svc.events().insert(calendarId=cal, body=body).execute()
            return ev.get("id")
        except Exception:
            return existing_event_id


def update_event(event_id: str, kind: str, title: str, content: str, tags: str,
                 event_date: str | None = None, revision="", move_date=True) -> bool:
    """기록을 고치고, 날짜를 변경했으면 캘린더에서도 옮긴다."""
    svc = _svc()
    if svc is None or not event_id:
        return False
    try:
        svc.events().patch(
            calendarId=GCAL_WRITE_CALENDAR_ID,
            eventId=event_id,
            body=_reflection_body(kind, title, content, tags, event_date if move_date else None, revision),
        ).execute()
        _list_cache["items"] = None
        return True
    except Exception as exc:
        if event_date and revision and _http_status(exc) == 404:
            return bool(create_event(kind, title, content, tags, event_date, event_id, revision))
        return False


def _review_copy_content(review_note: str, original_text: str) -> str:
    """다시보기 사본 이벤트 본문. 다시보기 내용을 위에, 원본을 아래에 둔다(다시보기 우선)."""
    note = (review_note or "").strip()
    orig = (original_text or "").strip()
    if note and orig:
        return note + "\n\n── 원본 ──\n" + orig
    return note or orig


def create_review_copy(kind, title, review_note, original_text, tags, event_date, event_id=None,
                       revision=""):
    """다시보기 사본 이벤트를 만든다(설명 = 다시보기 내용 우선 + 원본)."""
    return create_event(
        kind, title, _review_copy_content(review_note, original_text), tags, event_date, event_id, revision
    )


def update_review_copy(event_id, kind, title, review_note, original_text, tags, event_date=None,
                       revision="", move_date=True) -> bool:
    """다시보기 사본 이벤트의 설명을 다시보기 내용 우선으로 갱신한다."""
    return update_event(
        event_id, kind, title, _review_copy_content(review_note, original_text), tags,
        event_date, revision, move_date
    )


def delete_event(event_id: str) -> bool:
    """이벤트를 삭제한다. 성공 여부를 돌려준다(없거나 비활성이면 False)."""
    svc = _svc()
    if svc is None or not event_id:
        return False
    try:
        svc.events().delete(
            calendarId=GCAL_WRITE_CALENDAR_ID, eventId=event_id
        ).execute()
        _list_cache["items"] = None
        return True
    except Exception as exc:
        missing = _http_status(exc) in (404, 410)
        if missing:
            invalidate_cache()
        return missing


def list_reflection_events(start: date, end: date, *, strict=False) -> list[dict]:
    """[start, end] 구간 고결감 캘린더 이벤트를 (id, kind, title, content, tags, date)로 파싱.

    구글에서 직접 만든 일정도 여기로 들어와 탭에 보이게 된다(양방향). 60초 캐시.
    """
    if not enabled():
        return []
    key = (start.isoformat(), end.isoformat(), strict)
    now = time.time()
    if (
        _list_cache["items"] is not None
        and _list_cache["key"] == key
        and (now - _list_cache["at"]) < _LIST_TTL
    ):
        return _list_cache["items"]
    svc = _svc()
    if svc is None:
        if strict:
            raise RuntimeError("캘린더 연결을 확인하세요")
        return []
    time_min = f"{start.isoformat()}T00:00:00+09:00"
    time_max = f"{(end + timedelta(days=1)).isoformat()}T00:00:00+09:00"
    out: list[dict] = []
    page_token = None
    try:
        while True:
            resp = svc.events().list(
                calendarId=GCAL_WRITE_CALENDAR_ID,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                maxResults=2500,
                pageToken=page_token,
                **({"showDeleted": True} if strict else {}),
            ).execute()
            for it in resp.get("items", []):
                ev_id = it.get("id")
                if not ev_id:
                    continue
                private = it.get("extendedProperties", {}).get("private", {})
                revision = private.get("sixblockRevision", "")
                if it.get("status") == "cancelled":
                    if strict:
                        out.append({"id": ev_id, "cancelled": True})
                    continue
                start_obj = it.get("start", {})
                d = start_obj.get("date") or (start_obj.get("dateTime") or "")[:10]
                if start_obj.get("dateTime"):
                    dt = datetime.fromisoformat(start_obj["dateTime"])
                    if dt.tzinfo:
                        d = dt.astimezone(_KST).date().isoformat()
                if not d:
                    continue
                kind, title = parse_summary(it.get("summary", ""))
                content, tags = parse_description(it.get("description", ""), private.get("sixblockTagged"))
                out.append({
                    "id": ev_id, "kind": kind, "title": title,
                    "content": content, "tags": tags, "date": d, "revision": revision,
                })
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        if strict:
            raise
        return _list_cache["items"] or []
    _list_cache.update({"at": now, "key": key, "items": out})
    return out
