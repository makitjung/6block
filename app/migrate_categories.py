# 기존 카테고리(모임·코어1/2/3)를 새 6종(코어·점검·업무·약속·휴식·기타)으로 무손실 이전하는 1회용 마이그레이션
import shutil
import sqlite3
from datetime import datetime

from app.config import BACKUP_DIR, CATEGORIES, DB_PATH

# 옛 이름 → 새 이름. id를 보존하며 슬롯/블록 참조를 새 이름으로 옮긴다.
REMAP = {"코어1": "코어", "코어2": "코어", "코어3": "코어", "모임": "약속"}


def _col_exists(conn, table, col):
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def _id_of(conn, name):
    r = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    return r[0] if r else None


def migrate():
    if not DB_PATH.exists():
        print(f"[skip] DB 없음: {DB_PATH}")
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"blocks-cats-{stamp}.db"
    shutil.copy2(DB_PATH, backup)
    print(f"[backup] {backup}")

    conn = sqlite3.connect(DB_PATH)
    try:
        has_block_cat = _col_exists(conn, "blocks", "category_id")
        # 1) 옛 이름 → 새 이름 이전. 대상이 없으면 이름만 변경(id 보존),
        #    이미 있으면 슬롯/블록을 대상으로 재지정 후 옛 항목 비활성화.
        for src, dst in REMAP.items():
            src_id = _id_of(conn, src)
            if src_id is None:
                continue
            dst_id = _id_of(conn, dst)
            if dst_id is None:
                conn.execute(
                    "UPDATE categories SET name = ? WHERE id = ?", (dst, src_id)
                )
            else:
                conn.execute(
                    "UPDATE slots SET category_id = ? WHERE category_id = ?",
                    (dst_id, src_id),
                )
                if has_block_cat:
                    conn.execute(
                        "UPDATE blocks SET category_id = ? WHERE category_id = ?",
                        (dst_id, src_id),
                    )
                conn.execute(
                    "UPDATE categories SET is_active = 0 WHERE id = ?", (src_id,)
                )
        # 2) 목표 6종의 색·표시순서·활성 보정(없으면 생성)
        for order, (name, color) in enumerate(CATEGORIES):
            conn.execute(
                """
                INSERT INTO categories (name, color, display_order, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(name) DO UPDATE SET
                    color = excluded.color,
                    display_order = excluded.display_order,
                    is_active = 1
                """,
                (name, color, order),
            )
        # 3) 목표에 없는 이름은 비활성(드롭다운에서 숨김, 데이터는 보존)
        names = tuple(n for n, _ in CATEGORIES)
        ph = ",".join("?" * len(names))
        conn.execute(
            f"UPDATE categories SET is_active = 0 WHERE name NOT IN ({ph})", names
        )
        conn.commit()
    finally:
        conn.close()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    print("[done] categories:")
    for r in conn.execute(
        "SELECT id, name, color, display_order, is_active FROM categories "
        "ORDER BY is_active DESC, display_order"
    ):
        print("  ", dict(r))
    conn.close()


if __name__ == "__main__":
    migrate()
