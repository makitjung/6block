# SQLite 연결과 스키마 초기화, 누락 컬럼 자동 마이그레이션을 담당하는 데이터 액세스 헬퍼
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import CATEGORIES, DB_PATH

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        _seed_categories(conn)
        conn.commit()


def _seed_categories(conn: sqlite3.Connection):
    """카테고리가 비어 있으면 기본 6종을 넣는다(기존 데이터는 건드리지 않음)."""
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]:
        return
    for order, (name, color) in enumerate(CATEGORIES):
        conn.execute(
            "INSERT INTO categories (name, color, display_order, is_active) "
            "VALUES (?, ?, ?, 1)",
            (name, color, order),
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
    # 버퍼 블록 이름 변경(점심·기타→점심, 이동·휴식→저녁)을 기존 데이터에 멱등 반영
    conn.execute("UPDATE blocks SET block_label = '점심' WHERE block_label = '점심·기타'")
    conn.execute("UPDATE blocks SET block_label = '저녁' WHERE block_label = '이동·휴식'")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
