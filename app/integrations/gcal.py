# 여러 구글 캘린더 iCal 주소를 받아 캘린더별 색을 입혀 날짜별 일정으로 파싱·캐시하는 연동 모듈
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import GCAL_CALENDARS

KST = ZoneInfo("Asia/Seoul")
_CACHE_TTL = 120  # 초. 같은 .ics를 2분 동안 재사용(구글 피드 갱신 지연이 더 큼).
_cache: dict[str, dict] = {}  # url -> {"at": float, "cal": Calendar|None}

try:
    import icalendar
    import recurring_ical_events

    _HAS_ICAL = True
except Exception:  # 라이브러리 미설치 시 캘린더만 비활성, 앱은 정상.
    _HAS_ICAL = False


def enabled() -> bool:
    return bool(GCAL_CALENDARS) and _HAS_ICAL


def status():
    """헬스체크용. 캘린더별 설정·도달 여부·VEVENT 개수."""
    if not GCAL_CALENDARS:
        return {"enabled": False, "reason": "URL 미설정"}
    if not _HAS_ICAL:
        return {"enabled": False, "reason": "라이브러리 미설치"}
    out = []
    for c in GCAL_CALENDARS:
        item = {"name": c["name"], "color": c["color"]}
        try:
            req = urllib.request.Request(c["url"], headers={"User-Agent": "6block/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                item.update(
                    {"reachable": True, "http": resp.getcode(),
                     "vevents": resp.read().count(b"BEGIN:VEVENT")}
                )
        except urllib.error.HTTPError as e:
            item.update({"reachable": False, "http": e.code,
                         "reason": "주소 접근 불가 (공개 설정 또는 비공개 주소 확인)"})
        except Exception as e:
            item.update({"reachable": False, "reason": str(e)[:120]})
        out.append(item)
    return out


def _load_calendar(url: str):
    """한 캘린더 주소에서 .ics를 받아 파싱한 Calendar를 TTL 캐시로 반환."""
    now = time.time()
    slot = _cache.get(url)
    if slot and slot["cal"] is not None and (now - slot["at"]) < _CACHE_TTL:
        return slot["cal"]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "6block/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
        cal = icalendar.Calendar.from_ical(raw)
    except Exception:
        return slot["cal"] if slot else None  # 실패 시 이전 캐시(없으면 None) 유지.
    _cache[url] = {"cal": cal, "at": now}
    return cal


def events_for_range(start: date, end: date) -> dict[str, list[dict]]:
    """[start, end] 구간의 날짜별 일정. dict['YYYY-MM-DD'] -> [event...]. 여러 캘린더 병합."""
    result: dict[str, list[dict]] = {}
    if not enabled():
        return result
    for c in GCAL_CALENDARS:
        cal = _load_calendar(c["url"])
        if cal is None:
            continue
        try:
            occurrences = recurring_ical_events.of(cal).between(
                start, end + timedelta(days=1)
            )
        except Exception:
            continue
        for comp in occurrences:
            ev = _normalize(comp, c["color"], c["name"])
            if ev:
                result.setdefault(ev["date"], []).append(ev)
    for items in result.values():
        items.sort(
            key=lambda e: (not e["all_day"], e["start_min"] if e["start_min"] is not None else -1)
        )
    return result


def events_for_date(target: date) -> list[dict]:
    return events_for_range(target, target).get(target.strftime("%Y-%m-%d"), [])


def _to_kst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _normalize(comp, color: str, cal_name: str) -> dict | None:
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
            "color": color,
            "cal": cal_name,
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
        "color": color,
        "cal": cal_name,
    }
