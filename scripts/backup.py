# SQLite DB를 .sql 덤프로 백업하는 일별 스크립트 (cron 또는 launchd로 실행)
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import BACKUP_DIR, CLOUD_BACKUP_DIR, DB_PATH  # noqa: E402


def dump():
    if not DB_PATH.exists():
        print(f"[skip] DB not found: {DB_PATH}")
        return
    today = datetime.now().strftime("%Y%m%d")
    for target in (BACKUP_DIR, CLOUD_BACKUP_DIR):
        target.mkdir(parents=True, exist_ok=True)
        out = target / f"blocks-{today}.sql"
        with sqlite3.connect(DB_PATH) as conn, out.open("w", encoding="utf-8") as fp:
            for line in conn.iterdump():
                fp.write(f"{line}\n")
        print(f"[ok] dumped -> {out}")


if __name__ == "__main__":
    dump()
