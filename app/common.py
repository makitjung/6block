# 화면(라우터) 모듈이 공통으로 쓰는 것들. 템플릿 엔진, 시간·날짜 도우미,
# 하루 골격 생성, 3칸 입력 처리, 계획 자동 세분화를 모아 둔다.
import json
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Path as PathParam
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.config import DAY_BLOCKS, slots_for_day
from app.db import get_day_blocks, get_settings
from app.integrations import ai

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).parent

# 주소에 오는 행 id(/slot/done/12 의 12). FastAPI 는 자릿수가 아무리 길어도 파이썬 int 로
# 잘 바꿔 주지만, SQLite 는 64비트를 넘는 정수를 못 받아 OverflowError 로 500 이 된다.
# 범위를 벗어나거나 0·음수면 쿼리까지 가기 전에 422 로 거절한다.
SQLITE_MAX_INT = 9223372036854775807
RowId = Annotated[int, PathParam(ge=1, le=SQLITE_MAX_INT)]


def int_id(value) -> int:
    """폼으로 온 '반드시 있어야 하는' 행 id. 못 읽거나 범위를 벗어나면 ValueError.

    부르는 쪽은 이미 (TypeError, ValueError) 를 400 으로 돌려주고 있어 그대로 걸린다.
    범위를 안 보면 큰 수가 쿼리 매개변수로 들어가 sqlite3 가 OverflowError 를 내고
    화면이 500 이 된다(주소로 오는 id 는 RowId 가 같은 일을 한다).
    """
    n = int(value)
    if not (1 <= n <= SQLITE_MAX_INT):
        raise ValueError(f"행 id 범위를 벗어남: {n}")
    return n


def opt_id(value) -> int | None:
    """폼으로 온 '비울 수 있는' 행 id(구분·상위 항목처럼). 비었거나 못 쓸 값이면 None.

    여기서 예외를 내면 칸 하나가 이상하다고 저장 전체가 막힌다. 지정 없음으로 본다.
    """
    try:
        return int_id(value)
    except (TypeError, ValueError):
        return None


async def _off_loop(fn, *args, **kwargs):
    """구글 API·AppleScript·AI 호출처럼 느린 동기 함수를 스레드풀에서 실행한다.

    async 라우트 안에서 그대로 부르면 그 몇 초 동안 이벤트 루프가 멈춰 다른 요청(60초
    실시간 폴링 포함)이 전부 대기한다. 이 함수로 감싸면 그동안에도 서버가 계속 응답한다.
    """
    return await run_in_threadpool(fn, *args, **kwargs)
KO_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
CORE_LABELS = [b[0] for b in DAY_BLOCKS if b[1]]  # B1..B6

templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _ko_weekday(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return KO_WEEKDAYS[d.weekday()]


def _pretty_date(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return f"{d.month}월 {d.day}일 {KO_WEEKDAYS[d.weekday()]}요일"


def _short_date(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return f"{d.month}.{d.day}"


templates.env.filters["ko_weekday"] = _ko_weekday
templates.env.filters["pretty_date"] = _pretty_date
templates.env.filters["short_date"] = _short_date


# ?v= 를 붙여 내보내는 정적 파일. 이 중 하나라도 바뀌면 버전이 올라가 기기가 새로 받는다.
# 여기 빠진 파일에 ?v= 를 달면, 파일을 고쳐도 버전이 그대로라 브라우저가 옛것을 계속 쓴다
# (정적 파일은 ?v= 가 붙으면 1년 캐시라 더욱 그렇다).
# sw.js 도 넣는다. 서비스워커는 /sw.js?v=<이 값> 으로 등록되므로, 여기 없으면 sw.js 만
# 고쳤을 때 등록 주소가 그대로라 새 워커가 곧바로 올라오지 않는다.
VERSIONED_ASSETS = ("app.js", "style.css", "sw.js",
                    "icon.svg", "icon.png", "icon-192.png", "apple-touch-icon.png",
                    # 매니페스트가 ?v= 를 붙여 내보내는 아이콘이라 여기에도 있어야 한다
                    # (main.py manifest 참고). 빠지면 그림을 바꿔도 폰이 1년 동안 옛것을 쓴다.
                    "icon-maskable.svg")
_ASSET_VER_TTL = 10          # 초. 한 페이지를 그리는 동안 stat 을 반복하지 않게만 잡아 둔다.
_asset_ver_cache: tuple[float, str] | None = None


def asset_ver() -> str:
    """정적 파일의 최신 수정시각을 캐시버스팅 쿼리값으로 반환(파일 바뀌면 자동 변경).

    한 페이지에서 파일 수만큼 여러 번 불리므로 결과를 짧게 캐시한다. 코드를 고치고
    최대 10초 뒤에는 새 버전이 나가고, 화면은 /version 으로 그것을 보고 스스로 새로고침한다.
    """
    global _asset_ver_cache
    now = time.monotonic()
    if _asset_ver_cache and (now - _asset_ver_cache[0]) < _ASSET_VER_TTL:
        return _asset_ver_cache[1]
    mtimes = []
    for name in VERSIONED_ASSETS:
        try:
            mtimes.append((BASE_DIR / "static" / name).stat().st_mtime)
        except OSError:
            pass
    ver = str(int(max(mtimes))) if mtimes else "1"
    _asset_ver_cache = (now, ver)
    return ver


# 화면(JS)에서 실제로 쓰는 설정만 페이지에 싣는다. 예전에는 app_settings 전체를 내보내
# 캘린더 ID·AI 주소까지 모든 페이지 소스에 남았다.
CLIENT_SETTING_KEYS = (
    "pomo_auto", "pomo_end_alarm", "collapse_blocks",
    "pomo_start_sound", "pomo_start_sec", "pomo_end_sound", "pomo_end_sec",
)


def _client_settings() -> dict:
    """브라우저 JS가 읽는 설정만 추린 dict."""
    s = get_settings()
    return {k: s.get(k) for k in CLIENT_SETTING_KEYS}


templates.env.globals["asset_ver"] = asset_ver
templates.env.globals["get_settings"] = get_settings
templates.env.globals["client_settings"] = _client_settings


def today_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _weekday_of(date_str: str) -> int:
    """'YYYY-MM-DD'의 요일(0=월 ~ 6=일). 요일별 세션 시간을 고르는 데 쓴다."""
    return datetime.strptime(date_str, "%Y-%m-%d").date().weekday()


def _skeleton_matches_config(conn, date_str: str) -> bool:
    """DB의 그날 블록 골격이 현재 효과적 설정(요일별 시간 편집 반영)과 정확히 같은지."""
    have = [
        (r["block_label"], r["start_time"], r["end_time"])
        for r in conn.execute(
            "SELECT block_label, start_time, end_time FROM blocks "
            "WHERE date = ? ORDER BY block_order",
            (date_str,),
        )
    ]
    want = [
        (label, start, end)
        for (label, _core, start, end) in get_day_blocks(_weekday_of(date_str))
    ]
    return have == want


def _day_has_content(conn, date_str: str) -> bool:
    """그날에 사용자가 입력한 내용이 있는지(슬롯 do·한 일·구분·완료·주계획 연결,
    블록 plan·see·이름·구분·장소·주계획 연결).

    여기서 빠뜨린 칸은 세션 시간을 바꿀 때 골격 재생성으로 조용히 사라진다.
    blocks·slots 에 사용자가 채우는 컬럼을 새로 만들면 반드시 여기에도 넣는다.
    """
    if conn.execute(
        "SELECT 1 FROM slots WHERE date = ? AND ("
        # 고정 할일만 들어 있는 칸은 사용자 입력이 아니다. 그래야 설정에서 블록 시간을
        # 바꿨을 때 그 날이 새 시간표로 다시 만들어진다(고정 할일은 템플릿을 다시 고르면 복구).
        "(is_routine = 0 AND (TRIM(COALESCE(do_text,'')) != '' OR category_id IS NOT NULL)) "
        "OR TRIM(COALESCE(did_text,'')) != '' OR done = 1 "
        # 그 주 할 일에 이어 둔 슬롯도 사용자 입력이다.
        "OR TRIM(COALESCE(wk_todo,'')) != '') LIMIT 1",
        (date_str,),
    ).fetchone():
        return True
    return bool(
        conn.execute(
            "SELECT 1 FROM blocks WHERE date = ? AND ("
            "TRIM(COALESCE(plan_text,'')) != '' OR TRIM(COALESCE(see_text,'')) != '' "
            # 점심·저녁 버퍼 블록은 기본 구분이 '기타'로 자동 세팅되므로 사용자 내용이 아니다.
            # 코어 블록의 구분만 사용자 내용으로 친다(안 그러면 모든 날이 '내용 있음'이 돼
            # 세션 시간 변경이 빈 날에도 반영되지 않는다).
            "OR (is_core = 1 AND category_id IS NOT NULL) OR TRIM(COALESCE(name,'')) != '' "
            # 장소만 지정해 둔 날도 사용자 입력이다(빠지면 시간 변경 시 골격 재생성으로 유실된다).
            "OR TRIM(COALESCE(location,'')) != '' "
            # 그 주 할 일에 이어 둔 블록도 마찬가지다.
            "OR TRIM(COALESCE(wk_todo,'')) != '') LIMIT 1",
            (date_str,),
        ).fetchone()
    )


# '내용이 있는 슬롯'인지 판정하는 SQL 조각. 슬롯 별칭이 s 인 쿼리에 그대로 끼워 넣는다.
# 주간·분석의 '기록된 시간'과 구분별 분포가 이 하나를 같이 쓴다(두 화면 숫자가 갈리지 않게).
# 계획(do_text)·한 일(did_text)·완료 체크 중 하나라도 있으면 기록으로 본다. 다만 고정
# 할일이 채워 넣은 칸은 사람이 적은 것이 아니라, 체크했거나 한 일을 적었을 때만 센다
# (_day_has_content 와 같은 기준).
SLOT_HAS_CONTENT = (
    "((s.is_routine = 0 AND TRIM(COALESCE(s.do_text, '')) != '') "
    "OR TRIM(COALESCE(s.did_text, '')) != '' OR s.done = 1)"
)


def ensure_day_skeleton(conn, date_str: str):
    """블록·슬롯이 없으면 생성한다. 설정이 바뀌었고 입력이 없는 날은 새 배치로 자동 재생성한다."""
    if conn.execute(
        "SELECT 1 FROM blocks WHERE date = ? LIMIT 1", (date_str,)
    ).fetchone():
        # 골격이 현재 설정과 같거나, 사용자가 입력한 내용이 있으면 그대로 둔다.
        if _skeleton_matches_config(conn, date_str) or _day_has_content(conn, date_str):
            return
        # 설정이 바뀌었고 입력이 없는 날은 옛 골격을 지우고 새 배치로 다시 만든다.
        conn.execute("DELETE FROM slots WHERE date = ?", (date_str,))
        conn.execute("DELETE FROM blocks WHERE date = ?", (date_str,))
    now = datetime.now(KST).isoformat(timespec="seconds")
    day_blocks = get_day_blocks(_weekday_of(date_str))
    # 점심·저녁 버퍼 블록은 기본 구분을 '기타'로 시드해 시간 분포 통계에 잡히게 한다.
    etc_row = conn.execute(
        "SELECT id FROM categories WHERE name = '기타' LIMIT 1"
    ).fetchone()
    etc_id = etc_row["id"] if etc_row else None
    default_cat = {"점심": etc_id, "저녁": etc_id}
    # 같은 날짜를 두 요청이 동시에 처음 열면 둘 다 '골격 없음'을 보고 둘 다 넣으려 든다.
    # 그러면 UNIQUE(date, block_order) 에 걸려 진 쪽이 500 이 됐다(폰과 맥이 함께 열려
    # 있고 자정을 넘겨 새 날짜를 폴링할 때가 그 자리다. 10개를 동시에 던지면 9개가 500).
    # 넣기는 ON CONFLICT DO NOTHING 으로, 슬롯이 물 블록 id 는 넣은 뒤에 다시 읽어서
    # 잡는다. 그러면 내가 만든 것이든 상대가 만든 것이든 같은 결과가 된다.
    for order, (label, is_core, start, end) in enumerate(day_blocks):
        conn.execute(
            """
            INSERT INTO blocks (date, block_order, block_label, is_core,
                                start_time, end_time, category_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, block_order) DO NOTHING
            """,
            (date_str, order, label, 1 if is_core else 0, start, end,
             default_cat.get(label), now),
        )
    block_ids = {
        r["block_label"]: r["id"]
        for r in conn.execute(
            "SELECT id, block_label FROM blocks WHERE date = ? ORDER BY block_order",
            (date_str,),
        )
    }
    for slot_idx, label, s_t, e_t in slots_for_day(day_blocks):
        if label not in block_ids:
            continue          # 상대가 지우는 중이면 다음 요청이 마저 만든다
        conn.execute(
            """
            INSERT INTO slots (date, block_id, slot_index, start_time, end_time,
                               updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, slot_index) DO NOTHING
            """,
            (date_str, block_ids[label], slot_idx, s_t, e_t, now),
        )


def purge_empty_days(conn, keep_days: int = 180) -> int:
    """적어 둔 내용이 하나도 없는 날의 빈 골격을 지운다. 지운 날 수를 돌려준다.

    화면을 여는 것만으로 그 날짜의 블록 8행과 슬롯 30여 행이 생긴다(ensure_day_skeleton).
    달력을 앞뒤로 넘겨 보기만 해도 쌓이므로, 실제로 2026-08-24 기준 블록이 있는 날짜
    106일 중 31일(29%)이 아무것도 안 적힌 껍데기였다.

    keep_days 안쪽(오늘 ±180일)은 건드리지 않는다. 그 바깥의 빈 날만 지운다. 지워도
    잃는 것이 없다 — 다시 열면 그때 설정으로 새로 만들어지고, 그것이 세션 시간을
    바꿨을 때 원래 기대하는 동작이다. 판정은 _day_has_content 하나만 쓴다(기준이
    갈리면 사람이 적어 둔 것을 지우게 된다).
    """
    today = datetime.now(KST).date()
    lo = (today - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    hi = (today + timedelta(days=keep_days)).strftime("%Y-%m-%d")
    dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM blocks WHERE date < ? OR date > ?", (lo, hi)
        )
    ]
    removed = 0
    for d in dates:
        if _day_has_content(conn, d):
            continue
        conn.execute("DELETE FROM slots WHERE date = ?", (d,))
        conn.execute("DELETE FROM blocks WHERE date = ?", (d,))
        removed += 1
    return removed


def _name_override(value, inherited: str):
    """블록 이름 입력값을 주간 상속과 비교해 덮어쓰기 값(없으면 None)을 돌려준다.

    비었거나 주간 이름과 같으면 None(주간 값을 따름), 다르면 그 값으로 덮어쓴다.
    """
    v = (value or "").strip()
    return None if (not v or v == inherited) else v


def _split3(s) -> list[str]:
    """줄바꿈으로 저장된 목표/계획을 정확히 3칸으로 분리(빈 칸 유지)."""
    parts = (s or "").split("\n")
    return (parts + ["", "", ""])[:3]


def _join3(form, prefix: str) -> str:
    """폼의 prefix1/2/3 값을 줄바꿈으로 합친다. 각 칸 내부의 줄바꿈은 공백으로 눌러
    3칸 구분(줄바꿈)이 깨지지 않게 한다. 모두 비면 빈 문자열."""
    vals = [
        (form.get(f"{prefix}{i}", "") or "").replace("\r", " ").replace("\n", " ").strip()
        for i in (1, 2, 3)
    ]
    joined = "\n".join(vals)
    return joined if joined.strip() else ""


# 이 앱이 다루는 날짜는 사람의 생애 범위다. 파이썬 date 의 상한(9999-12-31)에 바짝 붙은
# 날짜가 들어오면 형식 검사는 통과한 뒤 그 다음 계산에서 터진다. 하루 화면은 '다음날'(d+1일)을,
# 주간 항목은 '그 주 일요일'(월요일+6일)을 구하는데 9999-12-27 부터는 그 결과가 10000년이 돼
# OverflowError 가 그대로 올라가 화면이 500 이 된다. 계산부마다 try/except 를 다는 대신
# 입구 한 곳에서 걸러 day·week·plan·api 를 한꺼번에 막는다.
MAX_YEAR = 9000


def _parse_date(s) -> date | None:
    """'YYYY-MM-DD' 를 date 로. 형식이 틀리거나 MAX_YEAR 를 넘으면 None."""
    try:
        d = datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return d if d.year <= MAX_YEAR else None


# -- 장기 항목 상하관계 (오늘·주간 공용) --------------------------------------


def lt_tree_order(rows) -> list[dict]:
    """장기 항목을 상위 바로 아래에 하위가 오도록 줄 세우고 depth(겹 단계)를 붙인다.

    상위 기간은 하위를 모두 품으므로, 하위가 뽑힌 목록에는 상위도 반드시 함께 있다.
    그래도 상위가 빠진 줄은 최상위로 올려 목록에서 사라지지 않게 한다.
    들어온 차례(영역 순서)는 그대로 지킨다.
    """
    items = [dict(r) for r in rows]
    ids = {it["id"] for it in items}
    by_parent: dict = {}
    for it in items:
        pid = it["parent_id"] if it["parent_id"] in ids else None
        by_parent.setdefault(pid, []).append(it)
    out: list[dict] = []

    def walk(pid, depth: int):
        for it in by_parent.get(pid, []):
            it["depth"] = depth
            out.append(it)
            walk(it["id"], depth + 1)

    walk(None, 0)
    return out


def lt_leaves(rows) -> list[dict]:
    """장기 항목 줄에서 실제로 손에 잡히는 최하위(하위가 없는) 것만 남긴다.

    상위는 하위의 기간·진척률을 따라가는 묶음일 뿐이라 주간·오늘에는 내려보내지 않는다.
    대신 어느 상위에서 내려온 것인지 알 수 있게 그 상위 제목(parent_title)을 붙인다.
    """
    title_by_id = {r["id"]: r["title"] for r in rows}
    out = []
    for r in lt_tree_order(rows):
        if r["has_children"]:
            continue
        r["parent_title"] = title_by_id.get(r["parent_id"], "")
        out.append(r)
    return out


def is_period_span(start: str, end: str) -> bool:
    """기간이 달력의 한 해·한 분기·한 달과 딱 맞나.

    그렇다면 그것은 '중점' 이다 — 아래에 하위 기간을 만들어 구체적인 할 일을 담는
    자리라, 주간 목록에는 세우지 않는다(2026-08-24, 사용자 요청). 하위가 아직
    없어도 마찬가지다. 하위가 있으면 어차피 lt_leaves() 에서 빠진다.

    Dashboard 의 program_stats.py 에 같은 규칙이 하나 더 있다. 두 프로그램이 같은
    자료로 같은 목록을 그리므로 한쪽만 고치면 두 화면이 어긋난다.
    """
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return False           # 날짜 꼴이 깨진 것은 판단하지 않고 그대로 보낸다
    if s > e or s.year != e.year or s.day != 1:
        return False
    if (s.month, e.month, e.day) == (1, 12, 31):
        return True                                       # 한 해
    last = monthrange(e.year, e.month)[1]
    if s.month in (1, 4, 7, 10) and e.month == s.month + 2:
        return e.day == last                              # 한 분기
    return s.month == e.month and e.day == last           # 한 달


def week_lt_items(conn, week_start_str: str) -> list[dict]:
    """그 주에 걸친 장기 항목(활성 영역만) 중 최하위 것만. 상위 제목을 함께 준다.

    장기 탭에서 '가리기'를 건 항목(masked)은 여기서 뺀다. 누른 막대 하나만 대상이라
    하위는 그대로 남고, 상위를 가려도 has_children 판정은 원래대로여서 상위가 대신
    올라오지 않는다.

    연·분기·달 중점도 뺀다 — is_period_span() 참고. 장기 탭 간트에는 그대로 그린다.
    """
    d0 = datetime.strptime(week_start_str, "%Y-%m-%d").date()
    sunday = (d0 + timedelta(days=6)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT i.id, i.parent_id, i.title, i.start_date, i.end_date, i.progress, "
        "       i.block_label, a.name AS area_name, "
        "       EXISTS(SELECT 1 FROM lt_item c WHERE c.parent_id = i.id) AS has_children "
        "FROM lt_item i JOIN lt_area a ON a.id = i.area_id "
        "WHERE i.start_date <= ? AND i.end_date >= ? AND a.is_active = 1 "
        "AND i.masked = 0 "
        "ORDER BY a.display_order, i.start_date, i.id",
        (sunday, week_start_str),
    ).fetchall()
    return [it for it in lt_leaves(rows)
            if not is_period_span(it["start_date"], it["end_date"])]


def week_todos(conn, week_start_str: str) -> list[dict]:
    """그 주 '목표' 열에 적힌 할 일 목록. 오늘 탭 블록·슬롯을 여기에 잇는다.

    장기 항목마다의 란(key 'lt:<항목id>')과 자유 란 3개(key 'wk:1~3')를 한 줄로 세운다.
    장기 란은 내용이 비어도 항목 자체를 고를 수 있어야 하므로 남기고, 자유 란은 비면 뺀다.
    """
    goals = {
        r["item_id"]: (r["goal_text"] or "").strip()
        for r in conn.execute(
            "SELECT item_id, goal_text FROM weekly_lt_goal WHERE week_start = ?",
            (week_start_str,),
        )
    }
    out = []
    for it in week_lt_items(conn, week_start_str):
        name = f"{it['parent_title']} › {it['title']}" if it["parent_title"] else it["title"]
        g = goals.get(it["id"])
        out.append({"key": f"lt:{it['id']}",
                    "label": f"{name} · {g}" if g else name,
                    # 장기 탭에서 정해 둔 코어블록. 그 블록의 연결 목록에서 위로 올린다.
                    "blocks": (it["block_label"] or "").strip()})
    row = conn.execute(
        "SELECT weekly_goal FROM weekly_meta WHERE week_start = ?", (week_start_str,)
    ).fetchone()
    for i, txt in enumerate(_split3(row["weekly_goal"] if row else ""), start=1):
        if txt.strip():
            out.append({"key": f"wk:{i}", "label": txt.strip()})
    return out


# -- 검색어 처리 (분석·고결감 공용) ------------------------------------------


def _like_pattern(q: str) -> str:
    """LIKE 검색어를 안전하게 만든다. %, _ 는 사용자가 친 글자 그대로 찾도록 이스케이프한다."""
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


# -- 자동 세분화 (규칙기반 기본 + 선택적 AI) --------------------------------


def _rule_distribute(parent_text: str, n: int) -> list[str]:
    """부모 텍스트를 자식 n개 내용으로 나눈다. 여러 줄이면 분배, 한 줄이면 참고로 복제."""
    lines = [ln.strip() for ln in (parent_text or "").splitlines() if ln.strip()]
    if not lines:
        return [""] * n
    if len(lines) == 1:
        return [lines[0]] * n
    buckets: list[list[str]] = [[] for _ in range(n)]
    for i, ln in enumerate(lines):
        buckets[i % n].append(ln)
    return ["\n".join(b) for b in buckets]


def _ai_split(parent_text: str, labels: list[str], area_name: str,
              parent_label: str) -> list[str] | None:
    """AI로 상위 계획을 각 자식 기간(labels)별 내용으로 나눈다. 실패·미설정 시 None."""
    n = len(labels)
    system = ("당신은 개인 시간관리 코치입니다. 상위 계획을 하위 기간별 구체적 "
              "실행 항목으로 나눕니다. 한국어로 간결하게 답합니다.")
    user = (
        f"영역: {area_name or '(없음)'}\n"
        f"상위({parent_label}) 계획:\n{parent_text}\n\n"
        f"이 계획을 다음 {n}개 기간에 나눠, 각 기간에 할 구체적 항목 1~3개를 "
        f"제시하세요: {', '.join(labels)}.\n"
        f"반드시 길이 {n}의 JSON 문자열 배열만 출력하세요. "
        "각 원소는 그 기간 내용이며 여러 항목은 줄바꿈으로 구분합니다."
    )
    reply = ai.complete(system, user, max_tokens=800, temperature=0.5)
    if not reply:
        return None
    try:
        arr = json.loads(reply[reply.index("["):reply.rindex("]") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(arr, list) or not arr:
        return None
    arr = [str(x).strip() for x in arr]
    return (arr + [""] * n)[:n]
