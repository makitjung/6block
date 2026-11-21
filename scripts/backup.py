# SQLite DB를 .sql 덤프로 백업하고, 오래된 덤프와 불어난 로그를 정리하는 일별 스크립트
# (launchd io.6block.backup 이 매일 23시에 실행)
import fcntl
import os
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from app.config import BACKUP_DIR, CLOUD_BACKUP_DIR, DB_PATH  # noqa: E402

KEEP_DAYS = 30  # 이보다 오래된 일별 덤프는 자동 삭제(로컬·클라우드 모두). 무한 누적 방지.
RESTORE_MARKER = "-- 6block restore checked"
KST = ZoneInfo("Asia/Seoul")
_NAME_RE = re.compile(r"^blocks-(\d{8})\.sql$")
# 손으로 뜬 일회성 스냅샷(스키마를 건드리기 전 안전용). 이름에 붙은 날짜로 같이 걷어낸다.
# 이게 없으면 blocks-cats-20260608-092802.db 같은 파일이 영원히 남는다.
_SNAP_RE = re.compile(r"^blocks-.*?(\d{8})(?:-\d{6})?\.db$")

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
    """target 폴더에서 KEEP_DAYS보다 오래된 백업을 지운다.

    일별 덤프(blocks-YYYYMMDD.sql)와 손으로 뜬 일회성 스냅샷(blocks-*-YYYYMMDD*.db)이
    대상이다. 이름에서 날짜를 못 읽는 파일은 건드리지 않는다.
    """
    removed = 0
    for f in list(target.glob("blocks-*.sql")) + list(target.glob("blocks-*.db")):
        m = _NAME_RE.match(f.name) or _SNAP_RE.match(f.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            continue
        if (now.date() - d.date()).days > KEEP_DAYS:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"[rotate] removed {removed} old backup(s) in {target}")


def _purge_empty_days():
    """덤프를 뜨기 전에, 아무것도 안 적힌 채 쌓인 옛·먼 미래 날짜의 골격을 지운다.

    달력을 앞뒤로 넘겨 보기만 해도 그 날짜의 블록 8행·슬롯 30여 행이 생긴다.
    판정은 app.common.purge_empty_days 하나만 쓴다(기준이 갈리면 사람이 적어 둔
    것을 지우게 된다). 적어 둔 것이 있는 날과 오늘 ±180일은 건드리지 않는다.
    """
    try:
        from app.common import purge_empty_days
        from app.db import get_conn

        with get_conn() as conn:
            n = purge_empty_days(conn)
        if n:
            print(f"[purge] removed {n} empty day skeleton(s)")
    except Exception as e:
        print(f"[purge] skip: {e}")


def backup_targets(local: Path, secondary: Path):
    cloud_label = "OneDrive" if any(p.startswith("OneDrive-") for p in secondary.parts) else "보조 폴더"
    return (("로컬", local), (cloud_label, secondary))


def dump_state(path: Path) -> tuple[bool, bool]:
    """덤프가 끝까지 작성됐는지와 복원 시험 표시가 있는지 확인한다."""
    with path.open("rb") as fp:
        fp.seek(max(os.fstat(fp.fileno()).st_size - 256, 0))
        tail = fp.read().rstrip()
    checked = tail.endswith(b"COMMIT;\n" + RESTORE_MARKER.encode())
    return checked or tail.endswith(b"COMMIT;"), checked


def _checked_dump(workdir: Path) -> Path:
    source_uri = f"{DB_PATH.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source, closing(
        sqlite3.connect(workdir / "snapshot.db")
    ) as snapshot:
        source.backup(snapshot)
        sql_file = workdir / "dump.sql"
        with sql_file.open("w", encoding="utf-8") as fp:
            for line in snapshot.iterdump():
                fp.write(f"{line}\n")

    sql = sql_file.read_text(encoding="utf-8")
    if not sql.rstrip().endswith("COMMIT;"):
        raise sqlite3.DatabaseError("백업 덤프 작성이 끝나지 않았습니다")
    with closing(sqlite3.connect(workdir / "restore.db")) as restored:
        restored.executescript(sql)
        if restored.in_transaction or restored.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise sqlite3.DatabaseError("백업 덤프 복원 검사에 실패했습니다")
        if restored.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.DatabaseError("백업 덤프의 참조 무결성 검사에 실패했습니다")
    with sql_file.open("a", encoding="utf-8") as fp:
        fp.write(f"{RESTORE_MARKER}\n")
    return sql_file


def _publish_dump(source: Path, destination: Path):
    """정상 덤프를 같은 디렉터리의 임시 파일에서 준비한 뒤 한 번에 교체한다."""
    temp = None
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix=".blocks-", delete=False) as out:
            temp = Path(out.name)
            with source.open("rb") as fp:
                shutil.copyfileobj(fp, out)
            out.flush()
            os.fsync(out.fileno())
        temp.replace(destination)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def dump() -> list[dict]:
    if not DB_PATH.is_file():
        raise FileNotFoundError("백업할 데이터베이스가 없습니다")
    results = []
    # 수동 백업과 launchd 백업이 겹쳐도 먼저 읽은 스냅샷으로 새 덤프를 덮지 않게 한다.
    with (DB_PATH.parent / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = datetime.now(KST)
        with TemporaryDirectory(prefix="6block-backup-") as work:
            checked = _checked_dump(Path(work))
            for label, target in backup_targets(BACKUP_DIR, CLOUD_BACKUP_DIR):
                out = target / f"blocks-{now:%Y%m%d}.sql"
                try:
                    target.mkdir(parents=True, exist_ok=True)
                    _publish_dump(checked, out)
                except OSError as exc:
                    print(f"[error] dump -> {out}: {exc}", file=sys.stderr)
                    results.append({"label": label, "ok": False, "error": str(exc)})
                    continue
                print(f"[ok] dumped and restore checked -> {out}")
                results.append({"label": label, "ok": True, "name": out.name})
                _rotate(target, now)
        if all(r["ok"] for r in results):
            _purge_empty_days()
        _trim_logs()
    return results


if __name__ == "__main__":
    sys.exit(0 if all(r["ok"] for r in dump()) else 1)
