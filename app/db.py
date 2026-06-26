# SQLite 연결과 스키마 초기화, 누락 컬럼 자동 마이그레이션을 담당하는 데이터 액세스 헬퍼
import json
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
    cat_tone,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        # WAL은 읽기(60초 폴링)와 쓰기(저장)가 겹쳐도 서로 막지 않게 해 'database is locked'를
        # 줄인다. 파일 헤더에 한 번 기록되면 계속 유지되므로 시작 시 한 번만 켠다.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        _seed_categories(conn)
        _seed_areas(conn)
        _seed_settings(conn)
        conn.commit()


def _seed_categories(conn: sqlite3.Connection):
    """카테고리가 비어 있으면 기본 6종을 넣는다(기존 데이터는 건드리지 않음)."""
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]:
        return
    for order, (name, color) in enumerate(CATEGORIES):
        conn.execute(
            "INSERT INTO categories (name, color, tone, display_order, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            (name, color, cat_tone(name), order),
        )


def _seed_areas(conn: sqlite3.Connection):
    """장기플랜 영역이 비어 있으면 기본 영역을 넣는다(기존 데이터는 건드리지 않음)."""
    if conn.execute("SELECT COUNT(*) FROM lt_area").fetchone()[0]:
        return
    for order, name in enumerate(LT_AREAS):
        conn.execute(
            "INSERT INTO lt_area (name, display_order, is_active) VALUES (?, ?, 1)",
            (name, order),
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
    """기존 DB에 누락된 컬럼을 무중단으로 추가한다."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_meta)").fetchall()}
    for new_col in ("vow", "memo"):
        if new_col not in cols:
            conn.execute(f"ALTER TABLE weekly_meta ADD COLUMN {new_col} TEXT")
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
    # 버퍼 블록 이름 변경(점심·기타→점심, 이동·휴식→저녁)을 기존 데이터에 멱등 반영
    conn.execute("UPDATE blocks SET block_label = '점심' WHERE block_label = '점심·기타'")
    conn.execute("UPDATE blocks SET block_label = '저녁' WHERE block_label = '이동·휴식'")
    # B4 마지막 30분(16:30) 슬롯을 같은 날 저녁 블록으로 이동하고 경계를 16:30으로 맞춘다.
    # 슬롯 데이터(do/did/cat/done)는 그대로 두고 소속 블록(block_id)만 옮기므로 무손실·멱등이다.
    conn.execute(
        "UPDATE slots SET block_id = ("
        "    SELECT e.id FROM blocks e WHERE e.date = slots.date AND e.block_label = '저녁'"
        ") "
        "WHERE start_time = '16:30' "
        "  AND block_id IN (SELECT b.id FROM blocks b WHERE b.block_label = 'B4') "
        "  AND EXISTS (SELECT 1 FROM blocks e2 WHERE e2.date = slots.date AND e2.block_label = '저녁')"
    )
    conn.execute("UPDATE blocks SET end_time = '16:30' WHERE block_label = 'B4' AND end_time = '17:00'")
    conn.execute("UPDATE blocks SET start_time = '16:30' WHERE block_label = '저녁' AND start_time = '17:00'")
    # 고민·감상 '다시 볼 날짜'(입력할 때만 저장). 없으면 기록일 기준으로만 동작.
    refl_cols = {r[1] for r in conn.execute("PRAGMA table_info(reflection)").fetchall()}
    if refl_cols and "review_date" not in refl_cols:
        conn.execute("ALTER TABLE reflection ADD COLUMN review_date TEXT")
    # 고결감: 제목과 내용 분리(제목→구글 summary, 내용→description). 없으면 추가.
    if refl_cols and "title" not in refl_cols:
        conn.execute("ALTER TABLE reflection ADD COLUMN title TEXT")
    # 종류 명칭 변경(고민·감상·결심 → 고민·결정·감사). 기존 기록을 멱등 일괄 변경.
    if refl_cols:
        conn.execute("UPDATE reflection SET kind = '감사' WHERE kind = '감상'")
        conn.execute("UPDATE reflection SET kind = '결정' WHERE kind = '결심'")


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


# 설정의 시간 오버라이드(app_settings 'day_blocks_times', JSON)를 기본 DAY_BLOCKS 위에 입혀
# 효과적인 8블록 (label, is_core, start, end) 을 돌려준다. 라벨·코어여부·개수는 기본값 고정.
BLOCK_TIMES_KEY = "day_blocks_times"


def get_day_blocks():
    """효과적인 하루 8블록 목록. DB에 저장된 시작·끝 시간 오버라이드를 기본값 위에 입힌다."""
    blocks = [(lbl, core, s, e) for (lbl, core, s, e) in DAY_BLOCKS]
    raw = get_settings().get(BLOCK_TIMES_KEY)
    if not raw:
        return blocks
    try:
        times = json.loads(raw)
    except Exception:
        return blocks
    if not isinstance(times, list) or len(times) != len(DAY_BLOCKS):
        return blocks
    merged = []
    for (lbl, core, ds, de), t in zip(DAY_BLOCKS, times):
        s = (t.get("start") if isinstance(t, dict) else None) or ds
        e = (t.get("end") if isinstance(t, dict) else None) or de
        merged.append((lbl, core, s, e))
    return merged
