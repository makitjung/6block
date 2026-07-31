# Things3 'Today' 목록을 AppleScript로 읽고 쓰는(할일 추가) 연동 모듈 (macOS 전용)
import subprocess
import sys
import threading
import time
from datetime import date

_CACHE_TTL = 20  # 초. 폴링과 함께 Things Today를 거의 실시간으로 반영.
_cache: dict = {"at": 0.0, "items": None}
_refreshing = False
_refresh_lock = threading.Lock()

# Today 항목을 '이름<TAB>태그<TAB>id' 한 줄씩으로 직렬화해 반환(제목 안의 쉼표 문제 회피).
# 태그는 Things에서 쉼표로 구분된 문자열로 오고, 태그가 없으면 빈칸이다.
# id 는 오늘 탭 주간 띠의 할일 칩을 things:///show?id= 로 걸어 Things3 를 여는 데 쓴다.
_SCRIPT = (
    'set out to ""\n'
    'tell application "Things3"\n'
    '    repeat with t in to dos of list "Today"\n'
    '        set tg to ""\n'
    '        try\n'
    '            set tg to tag names of t\n'
    '        end try\n'
    '        set out to out & (name of t) & tab & tg & tab & (id of t) & linefeed\n'
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
    items = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\t")
        name = parts[0].strip()
        if not name:
            continue
        tagstr = parts[1] if len(parts) > 1 else ""
        tid = parts[2].strip() if len(parts) > 2 else ""
        tags = [t.strip() for t in tagstr.split(",") if t.strip()]
        items.append({"name": name, "tags": tags, "id": tid})
    return items


def _fetch_into_cache():
    """AppleScript로 Today를 읽어 캐시에 넣는다. 실패하면 이전 캐시를 그대로 둔다."""
    fetched = _today_names()
    if fetched is not None:
        _cache["items"] = fetched
        _cache["at"] = time.time()
    return fetched


def _refresh_later():
    """뒤에서 한 번만 새로 읽는다(겹쳐 도는 것을 막는다)."""
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    def run():
        global _refreshing
        try:
            _fetch_into_cache()
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=run, daemon=True).start()


def today_tasks(target: date, include_overdue: bool = True) -> list[dict]:
    """Things3 'Today' 목록을 반환한다. (제목만; 시간/마감 없음)

    Things의 Today는 실제 오늘에만 의미가 있어 다른 날짜는 빈 목록.
    캐시가 만료됐으면 있는 것을 그대로 주고 새것은 뒤에서 읽는다. osascript 한 번이
    0.5초쯤 걸려서, 예전에는 20초마다 그 시간을 화면 여는 사람이 기다렸다.
    캐시가 아예 없을 때(기동 직후·할일 추가 직후)만 기다린다.
    AppleScript 실패(권한 미승인 등) 시 직전 캐시 또는 빈 목록을 준다.
    """
    if target != date.today():
        return []
    if _cache["items"] is not None:
        if (time.time() - _cache["at"]) >= _CACHE_TTL:
            _refresh_later()
        names = _cache["items"]
    else:
        names = _fetch_into_cache() or []
    return [
        {"title": it["name"], "time": None, "time_min": None,
         "deadline": None, "overdue": False, "tags": it["tags"],
         "id": it.get("id", "")}
        for it in names
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


# 새 할일을 Inbox 에 만든다(이름은 argv로 전달해 따옴표·줄바꿈 escape 회피).
# 적을 때는 아직 언제 할지 모르는 것이라 수집함으로 넣고, 언제 할지는 Things3 에서 정한다.
# 화면에 보여 주는 목록은 종전대로 Today 다(그래서 방금 적은 것은 바로 보이지 않는다).
_ADD_SCRIPT = (
    "on run argv\n"
    "    set theName to item 1 of argv\n"
    '    tell application "Things3"\n'
    "        make new to do with properties {name:theName} "
    'at beginning of list "Inbox"\n'
    "    end tell\n"
    '    return "ok"\n'
    "end run"
)


def add_todo(title: str) -> bool:
    """Things3 Inbox에 할일을 만든다. 성공 여부 반환."""
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
    # Inbox 로 들어가므로 Today 목록은 달라지지 않는다. 예전에는 여기서 캐시를 비웠는데,
    # 이제는 다음 요청이 공연히 AppleScript(0.5초)를 기다리기만 해서 그대로 둔다.
    return r.returncode == 0
