# SQLite DB를 .sql 덤프로 백업하고 오래된 덤프를 정리하는 일별 스크립트 (cron 또는 launchd로 실행)
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import BACKUP_DIR, CLOUD_BACKUP_DIR, DB_PATH  # noqa: E402

KEEP_DAYS = 30  # 이보다 오래된 일별 덤프는 자동 삭제(로컬·클라우드 모두). 무한 누적 방지.
_NAME_RE = re.compile(r"^blocks-(\d{8})\.sql$")


def _rotate(target: Path, now: datetime):
    """target 폴더의 blocks-YYYYMMDD.sql 중 KEEP_DAYS보다 오래된 것을 지운다."""
    removed = 0
    for f in target.glob("blocks-*.sql"):
        m = _NAME_RE.match(f.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            continue
        if (now - d).days > KEEP_DAYS:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"[rotate] removed {removed} old dump(s) in {target}")


def dump():
    if not DB_PATH.exists():
        print(f"[skip] DB not found: {DB_PATH}")
        return
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    for target in (BACKUP_DIR, CLOUD_BACKUP_DIR):
        target.mkdir(parents=True, exist_ok=True)
        out = target / f"blocks-{today}.sql"
        with sqlite3.connect(DB_PATH) as conn, out.open("w", encoding="utf-8") as fp:
            for line in conn.iterdump():
                fp.write(f"{line}\n")
        print(f"[ok] dumped -> {out}")
        _rotate(target, now)


if __name__ == "__main__":
    dump()
