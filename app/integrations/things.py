# Things3 'Today' 목록을 AppleScript로 읽어오는 연동 모듈 (DB 스키마 버전차에 안전)
import subprocess
import time
from datetime import date

_CACHE_TTL = 20  # 초. 폴링과 함께 Things Today를 거의 실시간으로 반영.
_cache: dict = {"at": 0.0, "items": None}

# Today 항목 이름을 줄바꿈으로 직렬화해 반환 (제목 안의 쉼표 문제 회피)
_SCRIPT = (
    'set out to ""\n'
    'tell application "Things3"\n'
    '    repeat with t in to dos of list "Today"\n'
    '        set out to out & (name of t) & linefeed\n'
    '    end repeat\n'
    "end tell\n"
    "return out"
)


def _run(script: str, timeout: int = 15):
    """osascript 실행. (returncode, stdout) 반환, 실패 시 (None, '')."""
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout
    except Exception:
        return None, ""


def _today_names():
    rc, out = _run(_SCRIPT)
    if rc != 0:
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def today_tasks(target: date, include_overdue: bool = True) -> list[dict]:
    """Things3 'Today' 목록을 반환한다. (제목만; 시간/마감 없음)

    Things의 Today는 실제 오늘에만 의미가 있어 다른 날짜는 빈 목록.
    AppleScript 실패(권한 미승인 등) 시 직전 캐시 또는 빈 목록을 준다.
    """
    if target != date.today():
        return []
    now = time.time()
    if _cache["items"] is not None and (now - _cache["at"]) < _CACHE_TTL:
        names = _cache["items"]
    else:
        fetched = _today_names()
        if fetched is not None:
            _cache["items"] = fetched
            _cache["at"] = now
            names = fetched
        else:
            names = _cache["items"] or []
    return [
        {"title": n, "time": None, "time_min": None, "deadline": None, "overdue": False}
        for n in names
    ]


def status() -> dict:
    """헬스체크용. AppleScript 권한/연결 상태와 Today 개수."""
    rc, out = _run('tell application "Things3" to get count of to dos of list "Today"')
    if rc is None:
        return {"ok": False, "reason": "osascript timeout/error", "today": None}
    if rc != 0:
        return {"ok": False, "reason": "automation not permitted", "today": None}
    try:
        cnt = int(out.strip())
    except ValueError:
        cnt = None
    return {"ok": True, "today": cnt}
