# 구글 캘린더 비공개 iCal 주소를 받아 날짜별 일정으로 파싱·캐시하는 연동 모듈
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import GCAL_ICAL_URL

KST = ZoneInfo("Asia/Seoul")
_CACHE_TTL = 600  # 초. 같은 .ics를 10분 동안 재사용한다.
_cache: dict = {"at": 0.0, "cal": None}

try:
    import icalendar
    import recurring_ical_events

    _HAS_ICAL = True
except Exception:  # 라이브러리 미설치 시 캘린더만 비활성, 앱은 정상.
    _HAS_ICAL = False


def enabled() -> bool:
    return bool(GCAL_ICAL_URL) and _HAS_ICAL


def status() -> dict:
    """헬스체크용. 주소 설정·도달 여부·VEVENT 개수."""
    if not GCAL_ICAL_URL:
        return {"enabled": False, "reason": "URL 미설정"}
    if not _HAS_ICAL:
        return {"enabled": False, "reason": "라이브러리 미설치"}
    try:
        req = urllib.request.Request(GCAL_ICAL_URL, headers={"User-Agent": "6block/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            raw = resp.read()
        return {"enabled": True, "reachable": True, "http": code,
                "vevents": raw.count(b"BEGIN:VEVENT")}
    except urllib.error.HTTPError as e:
        return {"enabled": True, "reachable": False, "http": e.code,
                "reason": "주소 접근 불가 (공개 설정 또는 비공개 주소 확인)"}
    except Exception as e:
        return {"enabled": True, "reachable": False, "reason": str(e)[:120]}


def _load_calendar():
    """비공개 주소에서 .ics를 받아 파싱한 Calendar를 TTL 캐시로 반환."""
    now = time.time()
    if _cache["cal"] is not None and (now - _cache["at"]) < _CACHE_TTL:
        return _cache["cal"]
    try:
        req = urllib.request.Request(
            GCAL_ICAL_URL, headers={"User-Agent": "6block/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        cal = icalendar.Calendar.from_ical(raw)
    except Exception:
        return _cache["cal"]  # 실패 시 이전 캐시(없으면 None) 유지.
    _cache["cal"] = cal
    _cache["at"] = now
    return cal


def events_for_range(start: date, end: date) -> dict[str, list[dict]]:
    """[start, end] 구간의 날짜별 일정. dict['YYYY-MM-DD'] -> [event...]."""
    result: dict[str, list[dict]] = {}
    if not enabled():
        return result
    cal = _load_calendar()
    if cal is None:
        return result
    try:
        occurrences = recurring_ical_events.of(cal).between(
            start, end + timedelta(days=1)
        )
    except Exception:
        return result
    for comp in occurrences:
        ev = _normalize(comp)
        if ev:
            result.setdefault(ev["date"], []).append(ev)
    for items in result.values():
        items.sort(key=lambda e: (not e["all_day"], e["start_min"] if e["start_min"] is not None else -1))
    return result


def events_for_date(target: date) -> list[dict]:
    return events_for_range(target, target).get(target.strftime("%Y-%m-%d"), [])


def _to_kst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _normalize(comp) -> dict | None:
    summary = str(comp.get("SUMMARY", "")).strip() or "(제목 없음)"
    location = str(comp.get("LOCATION", "")).strip() or None
    dtstart = comp.get("DTSTART")
    if dtstart is None:
        return None
    sv = dtstart.dt
    all_day = not isinstance(sv, datetime)
    if all_day:
        return {
            "date": sv.strftime("%Y-%m-%d"),
            "title": summary,
            "location": location,
            "all_day": True,
            "start": None,
            "end": None,
            "start_min": None,
        }
    sdt = _to_kst(sv)
    dtend = comp.get("DTEND")
    edt = _to_kst(dtend.dt) if dtend is not None and isinstance(dtend.dt, datetime) else sdt
    return {
        "date": sdt.strftime("%Y-%m-%d"),
        "title": summary,
        "location": location,
        "all_day": False,
        "start": sdt.strftime("%H:%M"),
        "end": edt.strftime("%H:%M"),
        "start_min": sdt.hour * 60 + sdt.minute,
    }
