# SQLite 연결과 스키마 초기화, 누락 컬럼 자동 마이그레이션을 담당하는 데이터 액세스 헬퍼
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import DB_PATH

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection):
    """기존 DB에 누락된 컬럼을 무중단으로 추가한다."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_meta)").fetchall()}
    for new_col in ("vow", "memo"):
        if new_col not in cols:
            conn.execute(f"ALTER TABLE weekly_meta ADD COLUMN {new_col} TEXT")


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
