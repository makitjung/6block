# SQLite DB를 .sql 덤프로 백업하고, 오래된 덤프와 불어난 로그를 정리하는 일별 스크립트
# (launchd io.6block.backup 이 매일 23시에 실행)
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

# launchd 가 서버·백업 출력을 여기에 계속 덧붙인다. 놔두면 하루 2MB씩 늘어 1년에 700MB가
# 되고, 옛 오류가 잔뜩 섞여 진짜 문제를 찾기도 어려워진다.
LOG_DIR = DB_PATH.parent
LOG_FILES = ("uvicorn.out.log", "uvicorn.err.log", "backup.out.log", "backup.err.log")
LOG_MAX_BYTES = 5 * 1024 * 1024   # 이보다 커지면 잘라 낸다
LOG_KEEP_BYTES = 1 * 1024 * 1024  # 최근 이만큼은 남긴다(최근 며칠 접속 기록은 볼 수 있게)


def _trim_logs():
    """불어난 로그의 앞부분을 버리고 최근 분량만 남긴다(파일을 옮기지 않는다).

    launchd 가 넘겨준 파일을 서버가 append 로 계속 붙잡고 있어서, 이름을 바꾸면 서버는
    이름만 바뀐 그 파일에 계속 쓴다(새 로그가 안 보인다). 같은 파일을 제자리에서 잘라야
    서버가 그대로 이어 쓴다. 자르는 순간 들어온 몇 줄은 잃을 수 있는데, 접속 기록이라 무해하다.
    """
    for name in LOG_FILES:
        path = LOG_DIR / name
        try:
            if not path.exists() or path.stat().st_size <= LOG_MAX_BYTES:
                continue
            # 바이너리로 다룬다. 텍스트 모드는 끝 기준 seek 을 못 하고, 로그에 깨진
            # 바이트가 섞여도 바이너리면 그대로 옮겨 담을 수 있다.
            with path.open("rb+") as f:
                f.seek(-LOG_KEEP_BYTES, 2)
                f.readline()          # 잘린 첫 줄은 버려 줄 단위를 지킨다
                tail = f.read()
                f.seek(0)
                f.write(tail)
                f.truncate()
            print(f"[log] trimmed {path} -> {path.stat().st_size // 1024}KB")
        except OSError as e:
            print(f"[log] skip {path}: {e}")


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
    _trim_logs()


if __name__ == "__main__":
    dump()
