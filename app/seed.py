# 초기 카테고리 데이터를 SQLite에 적재하는 시드 스크립트
from datetime import datetime

from app.config import CATEGORIES
from app.db import get_conn, init_db


def seed_categories():
    init_db()
    with get_conn() as conn:
        for i, (name, color) in enumerate(CATEGORIES):
            conn.execute(
                """
                INSERT INTO categories (name, color, display_order, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(name) DO UPDATE SET
                    color = excluded.color,
                    display_order = excluded.display_order
                """,
                (name, color, i),
            )


if __name__ == "__main__":
    seed_categories()
    print(f"[{datetime.now().isoformat(timespec='seconds')}] categories seeded.")
