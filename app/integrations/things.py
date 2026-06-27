# Things3 'Today' 목록을 AppleScript로 읽고 쓰는(할일 추가) 연동 모듈 (macOS 전용)
import subprocess
import sys
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


def _run(script: str, timeout: int = 8):
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


def enabled() -> bool:
    """할일 쓰기는 macOS에서만(AppleScript). 권한 미승인 시 add_todo가 실패로 알린다."""
    return sys.platform == "darwin"


# 새 할일을 만들어 Today로 예약한다(이름은 argv로 전달해 따옴표·줄바꿈 escape 회피).
_ADD_SCRIPT = (
    "on run argv\n"
    "    set theName to item 1 of argv\n"
    '    tell application "Things3"\n'
    "        set t to make new to do with properties {name:theName}\n"
    "        schedule t for (current date)\n"
    "    end tell\n"
    '    return "ok"\n'
    "end run"
)


def add_todo(title: str) -> bool:
    """Things3에 할일을 만들고 오늘(Today)로 예약한다. 성공 여부 반환."""
    title = (title or "").strip()
    if not title or not enabled():
        return False
    try:
        r = subprocess.run(
            ["osascript", "-e", _ADD_SCRIPT, title],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return False
    if r.returncode == 0:
        _cache["items"] = None  # 다음 폴링에서 새 할일이 바로 보이도록 캐시 무효화
        return True
    return False
