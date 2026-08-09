# SQLite 연결과 스키마 초기화, 누락 컬럼 자동 마이그레이션을 담당하는 데이터 액세스 헬퍼
import fcntl
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import (
    CAT_TONE,
    CATEGORIES,
    DAY_BLOCKS,
    DEFAULT_SETTINGS,
    DB_PATH,
    LT_AREAS,
    area_tone,
    cat_tone,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 마이그레이션 판번호. _migrate 에 손댈 때마다 하나 올린다.
# DB 의 PRAGMA user_version 이 이 값이면 _migrate 를 통째로 건너뛴다. 기동 때마다
# PRAGMA table_info 8회와 조건 검사 20여 개를 다시 돌리지 않기 위함이다.
# .sql 덤프에는 user_version 이 담기지 않으므로, 옛 백업을 복원하면 0에서 시작해
# 마이그레이션이 처음부터 한 번 더 돈다(그래서 복원 호환성은 그대로다).
SCHEMA_VERSION = 5


def uid_from_created(created: str | None) -> str:
    """생성시각 문자열로 기록 공용 키(YYYYMMDD-HHMM-난수4, Record FORMAT.md 표준)를 만든다."""
    digits = re.sub(r"\D", "", created or "")[:12].ljust(12, "0")
    return f"{digits[:8]}-{digits[8:12]}-{secrets.token_hex(2)}"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 재시작 시 프로세스가 두 개 이상 겹쳐 뜨면 스키마 초기화·마이그레이션(테이블 재구성 DDL)이
    # 동시에 돌아 데이터가 깨질 수 있다. 파일 락으로 한 프로세스씩만 실행하게 직렬화한다.
    # 뒤에 온 프로세스는 기다렸다가 이미 끝난 마이그레이션을 조건 검사로 건너뛴다.
    lock_file = open(DB_PATH.parent / ".init.lock", "w")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        except OSError:
            pass  # 락 미지원 환경이면 최선 노력으로 진행
        with sqlite3.connect(DB_PATH) as conn:
            # WAL은 읽기(60초 폴링)와 쓰기(저장)가 겹쳐도 서로 막지 않게 해 'database is locked'를
            # 줄인다. 파일 헤더에 한 번 기록되면 계속 유지되므로 시작 시 한 번만 켠다.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            have = conn.execute("PRAGMA user_version").fetchone()[0]
            if have < SCHEMA_VERSION:
                _migrate(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            _seed_categories(conn)
            _seed_areas(conn)
            _seed_settings(conn)
            conn.commit()
    finally:
        lock_file.close()


def _seed_categories(conn: sqlite3.Connection):
    """카테고리가 비어 있으면 기본 6종을 넣는다(기존 데이터는 건드리지 않음)."""
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]:
        return
    for order, name in enumerate(CATEGORIES):
        conn.execute(
            "INSERT INTO categories (name, tone, display_order, is_active) "
            "VALUES (?, ?, ?, 1)",
            (name, cat_tone(name), order),
        )


def _seed_areas(conn: sqlite3.Connection):
    """장기플랜 영역이 비어 있으면 기본 영역을 넣는다(기존 데이터는 건드리지 않음)."""
    if conn.execute("SELECT COUNT(*) FROM lt_area").fetchone()[0]:
        return
    for order, name in enumerate(LT_AREAS):
        conn.execute(
            "INSERT INTO lt_area (name, display_order, is_active, tone) "
            "VALUES (?, ?, 1, ?)",
            (name, order, area_tone(order)),
        )


def _seed_settings(conn: sqlite3.Connection):
    """기본 동작 설정 키가 없으면 기본값으로 채운다(기존 값은 유지)."""
    for key, val in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, val),
        )


def _migrate(conn: sqlite3.Connection):
    """기존 DB에 누락된 컬럼을 추가하고, 더 이상 쓰지 않는 컬럼을 정리한다.

    user_version 이 SCHEMA_VERSION 보다 낮을 때만 불린다. 즉 평소 기동에서는 아예
    돌지 않고, 옛 .sql 덤프를 복원했을 때만 한 번 돈다. 모든 단계는 조건 검사를
    앞에 두어 몇 번을 돌려도 결과가 같다(멱등).

    컬럼 추가는 옛 백업을 복원했을 때도 앱이 뜨도록 남겨 둔다. 이미 반영이 끝난
    일회성 데이터 보정(라벨 이름 변경 등)은 제거했다. 백업은 30일만 보관하므로
    복원 대상이 되는 덤프는 모두 그 보정 이후의 것이다.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_meta)").fetchall()}
    for new_col in ("vow", "memo"):
        if new_col not in cols:
            conn.execute(f"ALTER TABLE weekly_meta ADD COLUMN {new_col} TEXT")
    # 오늘 감사·반성(3줄, 줄바꿈으로 합쳐 저장). 없으면 추가.
    meta_cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_meta)").fetchall()}
    if "gratitude" not in meta_cols:
        conn.execute("ALTER TABLE daily_meta ADD COLUMN gratitude TEXT")
    # 목표·달성·감사반성 각 3줄의 자유 태그(직접 입력, 줄바꿈 3칸). 이전 구분칩 컬럼(*_cats)이
    # 있으면 자유태그(*_tags)로 이름만 바꾸고, 없으면 새로 추가한다. 멱등.
    meta_cols_now = {r[1] for r in conn.execute("PRAGMA table_info(daily_meta)").fetchall()}
    for _old, _new in (("goal_cats", "goal_tags"), ("plan_cats", "plan_tags"),
                       ("grat_cats", "grat_tags")):
        if _new in meta_cols_now:
            continue
        if _old in meta_cols_now:
            conn.execute(f"ALTER TABLE daily_meta RENAME COLUMN {_old} TO {_new}")
        else:
            conn.execute(f"ALTER TABLE daily_meta ADD COLUMN {_new} TEXT")
    # 그날 성과 캘린더 이벤트 id(재저장 시 갱신·중복 방지). 없으면 추가.
    if "achieve_event_id" not in meta_cols:
        conn.execute("ALTER TABLE daily_meta ADD COLUMN achieve_event_id TEXT")
    # 오늘 컨셉 3줄(줄바꿈으로 합쳐 저장). 없으면 추가.
    if "concept" not in meta_cols:
        conn.execute("ALTER TABLE daily_meta ADD COLUMN concept TEXT")
    # 하루 마감의 '하루 평가'(그날 총평 한 칸, 줄바꿈·목록 그대로). 없으면 추가.
    if "day_review" not in meta_cols:
        conn.execute("ALTER TABLE daily_meta ADD COLUMN day_review TEXT")
    # 슬롯 실행 체크박스(DO 완료 여부)
    slot_cols = {r[1] for r in conn.execute("PRAGMA table_info(slots)").fetchall()}
    if "done" not in slot_cols:
        conn.execute("ALTER TABLE slots ADD COLUMN done INTEGER NOT NULL DEFAULT 0")
    # 슬롯 '실제로 한 일'(DO 계획과 별개로 실제 수행 내용 기록)
    if "did_text" not in slot_cols:
        conn.execute("ALTER TABLE slots ADD COLUMN did_text TEXT")
    # 블록 이름 일간 덮어쓰기(NULL이면 주간 이름을 따른다)
    block_cols = {r[1] for r in conn.execute("PRAGMA table_info(blocks)").fetchall()}
    if "name" not in block_cols:
        conn.execute("ALTER TABLE blocks ADD COLUMN name TEXT")
    # 블록 구분(카테고리). NULL이면 미지정.
    if "category_id" not in block_cols:
        conn.execute("ALTER TABLE blocks ADD COLUMN category_id INTEGER")
    # 블록 장소(홈·회사·독서실·카페·기타). NULL이면 미지정.
    if "location" not in block_cols:
        conn.execute("ALTER TABLE blocks ADD COLUMN location TEXT")
    # 카테고리 색 톤 컬럼(설정에서 팔레트 색을 고른다). 없으면 추가하고 기존 행을 기본 톤으로 채운다.
    cat_cols = {r[1] for r in conn.execute("PRAGMA table_info(categories)").fetchall()}
    if "tone" not in cat_cols:
        conn.execute("ALTER TABLE categories ADD COLUMN tone TEXT NOT NULL DEFAULT 'black'")
        for name, tone in CAT_TONE.items():
            conn.execute("UPDATE categories SET tone = ? WHERE name = ?", (tone, name))
    # 옛 color(hex) 컬럼 제거. 색은 tone 하나로만 칠하므로 화면·통계 어디서도 쓰지 않는다.
    if "color" in cat_cols:
        conn.execute("ALTER TABLE categories DROP COLUMN color")
    # 고민·감상 '다시 볼 날짜'(입력할 때만 저장). 없으면 기록일 기준으로만 동작.
    refl_cols = {r[1] for r in conn.execute("PRAGMA table_info(reflection)").fetchall()}
    if refl_cols and "review_date" not in refl_cols:
        conn.execute("ALTER TABLE reflection ADD COLUMN review_date TEXT")
    # 고결감: 제목과 내용 분리(제목→구글 summary, 내용→description). 없으면 추가.
    if refl_cols and "title" not in refl_cols:
        conn.execute("ALTER TABLE reflection ADD COLUMN title TEXT")
    # 고결감 다시볼 날짜에 남기는 메모.
    if refl_cols and "review_note" not in refl_cols:
        conn.execute("ALTER TABLE reflection ADD COLUMN review_note TEXT")
    # 쓰이지 않는 review_gcal_event_id 제거. 다시보기 사본은 자기 행의 gcal_event_id를 쓴다.
    if refl_cols and "review_gcal_event_id" in refl_cols:
        conn.execute("ALTER TABLE reflection DROP COLUMN review_gcal_event_id")
    # 다시보기 항목이 원본과 독립 삭제 가능하도록 출처 ID를 저장한다.
    if refl_cols and "source_id" not in refl_cols:
        conn.execute("ALTER TABLE reflection ADD COLUMN source_id INTEGER")
    # Record 기록 통합용 공용 키(uid). 없으면 추가하고, 빈 행은 생성시각 기반으로 채운다.
    if refl_cols:
        if "uid" not in refl_cols:
            conn.execute("ALTER TABLE reflection ADD COLUMN uid TEXT")
        for r in conn.execute(
            "SELECT id, created_at FROM reflection WHERE uid IS NULL OR uid = ''"
        ).fetchall():
            conn.execute("UPDATE reflection SET uid = ? WHERE id = ?",
                         (uid_from_created(r[1]), r[0]))
    # GTD 명료화: 수집함 항목 상태(''=미분류·next·wait·someday·ref). 없으면 추가.
    inbox_cols = {r[1] for r in conn.execute("PRAGMA table_info(inbox)").fetchall()}
    if inbox_cols and "status" not in inbox_cols:
        conn.execute("ALTER TABLE inbox ADD COLUMN status TEXT NOT NULL DEFAULT ''")
    # 주간 '목표' 열에서 장기 항목마다 따로 적는 그 주 계획. 옛 덤프 복원용으로 남겨 둔다.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_lt_goal (
            id INTEGER PRIMARY KEY,
            week_start TEXT NOT NULL,
            item_id INTEGER NOT NULL REFERENCES lt_item(id) ON DELETE CASCADE,
            goal_text TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(week_start, item_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_weekly_lt_goal_week ON weekly_lt_goal(week_start)"
    )
    # 고정 할일이 채운 칸 표시(통계 제외·재적용 시 덮어쓰기 대상). 없으면 추가.
    if "is_routine" not in slot_cols:
        conn.execute(
            "ALTER TABLE slots ADD COLUMN is_routine INTEGER NOT NULL DEFAULT 0")
    # 구분 템플릿에 딸린 고정 할일 규칙. 옛 덤프 복원용으로 남겨 둔다.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS routine_rule (
            id INTEGER PRIMARY KEY,
            template_id INTEGER NOT NULL REFERENCES cat_template(id) ON DELETE CASCADE,
            weekdays TEXT NOT NULL DEFAULT '',
            start_time TEXT NOT NULL,
            span INTEGER NOT NULL DEFAULT 1,
            do_text TEXT NOT NULL DEFAULT '',
            category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            display_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_routine_rule ON routine_rule(template_id)"
    )
    # 오늘 탭에서 블록·슬롯을 그 주 할 일 중 어느 것에 잇는지(키만 저장, 글은 직접 입력).
    # 블록은 두 시간짜리라 여러 계획을 담을 수 있어 쉼표로 여러 개를 넣는다.
    if "wk_todo" not in block_cols:
        conn.execute("ALTER TABLE blocks ADD COLUMN wk_todo TEXT")
    if "wk_todo" not in slot_cols:
        conn.execute("ALTER TABLE slots ADD COLUMN wk_todo TEXT")
    # 오늘 '목표' 3줄이 각각 이어진 그 주 할 일(줄바꿈 3칸, 비면 연결 없음).
    if "goal_links" not in meta_cols:
        conn.execute("ALTER TABLE daily_meta ADD COLUMN goal_links TEXT")
    # 장기 간트 행이 될 코어블록(B1~B6). NULL이면 상위를 따르고, 상위도 없으면 미지정 행에 그린다.
    item_cols = {r[1] for r in conn.execute("PRAGMA table_info(lt_item)").fetchall()}
    if "block_label" not in item_cols:
        conn.execute("ALTER TABLE lt_item ADD COLUMN block_label TEXT")
    # 간트에서 접어 두는 항목. 1이면 안 그리고 '숨긴 항목 보기'로만 다시 꺼낸다.
    if "hidden" not in item_cols:
        conn.execute(
            "ALTER TABLE lt_item ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    # 주간·오늘 탭에서 빼는 항목. 1이어도 장기 간트에는 그대로 그린다.
    if "masked" not in item_cols:
        conn.execute(
            "ALTER TABLE lt_item ADD COLUMN masked INTEGER NOT NULL DEFAULT 0")
    # 영역 색 톤(막대 색). 없으면 추가하고 기존 영역을 표시 순서대로 팔레트에 배정한다.
    area_cols = {r[1] for r in conn.execute("PRAGMA table_info(lt_area)").fetchall()}
    if "tone" not in area_cols:
        conn.execute("ALTER TABLE lt_area ADD COLUMN tone TEXT NOT NULL DEFAULT 'blue'")
        for i, r in enumerate(conn.execute(
            "SELECT id FROM lt_area ORDER BY display_order, id"
        ).fetchall()):
            conn.execute("UPDATE lt_area SET tone = ? WHERE id = ?", (area_tone(i), r[0]))
    # 요일 컨셉은 주간 블록테마(weekly_block_themes)로 일원화했다. 남은 테이블을 정리한다.
    conn.execute("DROP TABLE IF EXISTS weekday_concept")
    # 장기 계획은 간트(lt_item) 하나로 일원화했다. 표로 적던 lt_plan 은 코드에서 사라졌다.
    # 옛 .sql 덤프에는 아직 들어 있어, 복원하면 되살아나므로 여기서 함께 정리한다.
    conn.execute("DROP INDEX IF EXISTS idx_lt_plan_lookup")
    conn.execute("DROP TABLE IF EXISTS lt_plan")
    # 외부 일정을 DB에 담아 두려다 만 흔적. 캘린더는 받아서 바로 쓰고 저장하지 않는다.
    conn.execute("DROP TABLE IF EXISTS external_events")
    # 점심·저녁 버퍼 블록의 빈 구분을 '기타'로 채운다(주간 시간분포 통계 일관성). 멱등.
    conn.execute(
        "UPDATE blocks SET category_id = (SELECT id FROM categories WHERE name = '기타' LIMIT 1) "
        "WHERE block_label IN ('점심', '저녁') AND category_id IS NULL "
        "AND EXISTS (SELECT 1 FROM categories WHERE name = '기타')"
    )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 쓰기 잠금이 잡혀 있으면 즉시 실패하지 않고 최대 5초까지 기다린다(폴링·저장 경합 대비).
    conn.execute("PRAGMA busy_timeout = 5000")
    # WAL과 함께 쓰면 안전하면서 더 빠르다(OS 충돌 시 마지막 트랜잭션만 손실, 손상 없음).
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 설정은 거의 안 바뀌는데 페이지마다 여러 번 읽히므로 프로세스 메모리에 캐시한다.
# 단일 uvicorn 프로세스 기준으로 일관적이며, set_setting에서 무효화한다.
_settings_cache: dict | None = None


def get_settings() -> dict:
    """모든 동작 설정을 dict로 반환한다(기본값 위에 DB 저장값을 덮어쓴다). 결과는 캐시한다."""
    global _settings_cache
    if _settings_cache is not None:
        return dict(_settings_cache)
    out = dict(DEFAULT_SETTINGS)
    try:
        with get_conn() as conn:
            for r in conn.execute("SELECT key, value FROM app_settings"):
                out[r["key"]] = r["value"]
    except Exception:
        return out  # 실패 시 기본값만 주고 캐시하지 않는다(다음에 재시도).
    _settings_cache = out
    return dict(_settings_cache)


def set_setting(key: str, value: str):
    """설정 한 개를 저장한다(없으면 추가, 있으면 갱신). 저장 후 캐시를 비운다."""
    global _settings_cache
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    _settings_cache = None


# 설정의 시간 오버라이드를 기본 DAY_BLOCKS 위에 입혀 효과적인 8블록
# (label, is_core, start, end) 을 돌려준다. 라벨·코어여부·개수는 기본값 고정.
# 공통(모든 요일 기본) = app_settings 'day_blocks_times' (길이 8 JSON 배열),
# 요일 덮어쓰기 = 'day_blocks_times_wd' ({"0": [...], ... "6": [...]}, 덮어쓴 요일만).
BLOCK_TIMES_KEY = "day_blocks_times"
BLOCK_TIMES_WD_KEY = "day_blocks_times_wd"


def _parse_times(raw):
    """저장값(JSON 문자열 또는 리스트)을 길이 8 리스트로. 형식이 다르면 None."""
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, list) or len(raw) != len(DAY_BLOCKS):
        return None
    return raw


def _apply_times(blocks, times):
    """블록 목록 위에 시간 배열을 입힌다(비어 있는 칸은 원래 값 유지)."""
    if not times:
        return list(blocks)
    merged = []
    for (lbl, core, ds, de), t in zip(blocks, times):
        s = (t.get("start") if isinstance(t, dict) else None) or ds
        e = (t.get("end") if isinstance(t, dict) else None) or de
        merged.append((lbl, core, s, e))
    return merged


def get_weekday_overrides() -> dict:
    """요일 덮어쓰기 전체. {"0": [{start,end}...], ...} 형태이며 덮어쓴 요일만 들어 있다."""
    raw = get_settings().get(BLOCK_TIMES_WD_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# 요일별 컨셉(0=월 ~ 6=일). 설정에서 7칸을 적어 두면 오늘 탭 날짜 옆 괄호에 표시된다.
WEEKDAY_CONCEPTS_KEY = "weekday_concepts"


def get_weekday_concepts() -> list[str]:
    """요일 컨셉 7칸. 저장값이 없거나 형식이 다르면 빈 칸 7개를 돌려준다."""
    raw = get_settings().get(WEEKDAY_CONCEPTS_KEY)
    if not raw:
        return [""] * 7
    try:
        data = json.loads(raw)
    except ValueError:
        return [""] * 7
    if not isinstance(data, list):
        return [""] * 7
    return [str(x or "").strip() for x in (list(data) + [""] * 7)[:7]]


def get_day_blocks(weekday: int | None = None):
    """효과적인 하루 8블록 목록. 공통 시간 위에 그 요일 덮어쓰기가 있으면 덧입힌다.

    weekday 는 date.weekday() (0=월 ~ 6=일). None 이면 공통 시간만 쓴다.
    """
    base = _apply_times(DAY_BLOCKS, _parse_times(get_settings().get(BLOCK_TIMES_KEY)))
    if weekday is None:
        return base
    return _apply_times(base, _parse_times(get_weekday_overrides().get(str(weekday))))
