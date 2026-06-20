# 서비스계정으로 구글 캘린더 '고민/결심'에 고민·감상 이벤트를 생성·삭제하는 쓰기 연동 모듈
import os
from datetime import date, timedelta

from app.config import GCAL_SA_KEYFILE, GCAL_WRITE_CALENDAR_ID

# events 범위만 요청한다(캘린더 자체 생성/삭제 권한은 필요 없음).
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    _HAS_LIB = True
except Exception:  # 라이브러리 미설치 시 캘린더 쓰기만 비활성, 앱·DB 저장은 정상.
    _HAS_LIB = False

_service = None


def enabled() -> bool:
    return bool(
        GCAL_WRITE_CALENDAR_ID
        and GCAL_SA_KEYFILE
        and _HAS_LIB
        and os.path.exists(GCAL_SA_KEYFILE)
    )


def _svc():
    """서비스계정 자격증명으로 Calendar 서비스를 만들고 캐시한다. 비활성이면 None."""
    global _service
    if _service is not None:
        return _service
    if not enabled():
        return None
    creds = service_account.Credentials.from_service_account_file(
        GCAL_SA_KEYFILE, scopes=_SCOPES
    )
    _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def status() -> dict:
    """헬스체크용. 라이브러리·설정·실제 캘린더 접근 가능 여부."""
    if not _HAS_LIB:
        return {"enabled": False, "reason": "라이브러리 미설치(google-api-python-client)"}
    if not GCAL_WRITE_CALENDAR_ID:
        return {"enabled": False, "reason": "GCAL_WRITE_CALENDAR_ID 미설정"}
    if not GCAL_SA_KEYFILE or not os.path.exists(GCAL_SA_KEYFILE):
        return {"enabled": False, "reason": "서비스계정 키파일 없음"}
    # events 범위로 접근 확인(calendars.get은 더 넓은 scope가 필요해 events.list로 점검).
    try:
        _svc().events().list(
            calendarId=GCAL_WRITE_CALENDAR_ID, maxResults=1
        ).execute()
        return {"enabled": True, "calendar": GCAL_WRITE_CALENDAR_ID}
    except Exception as e:
        return {"enabled": False, "reason": str(e)[:160]}


def _next_day(d: str) -> str:
    """종일 이벤트의 end.date는 종료 다음날(배타적)이라 하루 더한다."""
    y, m, dd = (int(x) for x in d.split("-"))
    return (date(y, m, dd) + timedelta(days=1)).isoformat()


def create_event(kind: str, text: str, tags: str, event_date: str) -> str | None:
    """[종류] 접두어 제목 + 태그 본문으로 종일 이벤트를 만들고 event id를 돌려준다.

    extendedProperties에 표식을 남겨 캘린더에서 6block 기록만 추려보기 쉽게 한다.
    """
    svc = _svc()
    if svc is None:
        return None
    first_line = (text.strip().splitlines() or [""])[0][:80]
    desc = text.strip()
    if tags:
        desc += f"\n\n태그: {tags}"
    desc += "\n\n(6block 고민/감상 기록)"
    body = {
        "summary": f"[{kind}] {first_line}",
        "description": desc,
        "start": {"date": event_date},
        "end": {"date": _next_day(event_date)},
        "extendedProperties": {"private": {"sixblock": "reflection", "kind": kind}},
    }
    ev = svc.events().insert(calendarId=GCAL_WRITE_CALENDAR_ID, body=body).execute()
    return ev.get("id")


def delete_event(event_id: str) -> bool:
    """이벤트를 삭제한다. 성공 여부를 돌려준다(없거나 비활성이면 False)."""
    svc = _svc()
    if svc is None or not event_id:
        return False
    try:
        svc.events().delete(
            calendarId=GCAL_WRITE_CALENDAR_ID, eventId=event_id
        ).execute()
        return True
    except Exception:
        return False
