# Things3 로컬 SQLite를 읽기 전용으로 열어 Today 할 일을 가져오는 연동 모듈
import sqlite3
from datetime import date
from functools import lru_cache
from pathlib import Path

from app.config import THINGS_GROUP_DIR


@lru_cache(maxsize=1)
def _db_path() -> str | None:
    """그룹 컨테이너 안에서 메인 Things 데이터베이스 경로를 찾는다."""
    if not THINGS_GROUP_DIR.exists():
        return None
    hits = sorted(THINGS_GROUP_DIR.glob("ThingsData-*/*.thingsdatabase/main.sqlite"))
    # 백업(Backups/) 경로는 제외하고 현재 DB만.
    live = [p for p in hits if "Backups" not in p.parts]
    if not live:
        return None
    return str(live[0])


def _pack(d: date) -> int:
    """Things3의 startDate/deadline 정수 인코딩. (year<<16)|(month<<12)|(day<<7)."""
    return (d.year << 16) | (d.month << 12) | (d.day << 7)


def _unpack_md(v: int) -> str:
    """패킹된 날짜 정수에서 'M/D' 문자열만 추출."""
    m = (v >> 12) & 0xF
    d = (v >> 7) & 0x1F
    return f"{m}/{d}"


def today_tasks(target: date, include_overdue: bool = True) -> list[dict]:
    """대상 날짜의 Things3 'Today' 할 일 목록을 반환한다.

    Today 판정: status=0(미완료), trashed=0, type=0(할 일), start=1,
    startDate <= 대상일. include_overdue=False면 startDate == 대상일만.
    각 항목: {title, time, time_min, deadline, overdue}.
    reminderTime(있으면 자정 기준 초)이 있는 항목만 time이 채워진다.
    """
    path = _db_path()
    if not path or not Path(path).exists():
        return []

    packed = _pack(target)
    date_cond = "AND t.startDate <= ?" if include_overdue else "AND t.startDate = ?"
    sql = f"""
        SELECT t.title, t.startDate, t.deadline, t.reminderTime
        FROM TMTask t
        WHERE t.status = 0 AND t.trashed = 0 AND t.type = 0
          AND t.start = 1 AND t.startDate IS NOT NULL {date_cond}
        ORDER BY (t.reminderTime IS NULL), t.reminderTime, t.todayIndex
    """
    try:
        con = sqlite3.connect(f"file:{path}?immutable=1&mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, (packed,)).fetchall()
        con.close()
    except sqlite3.Error:
        return []

    out: list[dict] = []
    for r in rows:
        title = (r["title"] or "").strip()
        if not title:
            continue
        rt = r["reminderTime"]
        time_str = None
        time_min = None
        if rt is not None and 0 <= int(rt) <= 86400:
            time_min = int(rt) // 60
            time_str = f"{time_min // 60:02d}:{time_min % 60:02d}"
        deadline = _unpack_md(int(r["deadline"])) if r["deadline"] else None
        out.append(
            {
                "title": title,
                "time": time_str,
                "time_min": time_min,
                "deadline": deadline,
                "overdue": int(r["startDate"]) < packed,
            }
        )
    return out
