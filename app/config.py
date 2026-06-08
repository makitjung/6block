# 하루 8블록(코어 6 + 버퍼 2)과 30분 슬롯, 카테고리, 외부 연동 환경값을 정의하는 설정 파일
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 프로젝트 루트의 .env에서 시크릿(구글 캘린더 비공개 주소 등)을 읽는다. 없으면 무시.
load_dotenv(PROJECT_ROOT / ".env")

DB_PATH = Path.home() / "6block-data" / "blocks.db"
BACKUP_DIR = Path.home() / "6block-data" / "backups"

# 구글 캘린더 비공개 iCal 주소 (.env의 GCAL_ICAL_URL). 비어 있으면 캘린더 연동 비활성.
GCAL_ICAL_URL = os.getenv("GCAL_ICAL_URL", "").strip()

# (block_label, is_core, start, end)
DAY_BLOCKS = [
    ("B1",         True,  "07:30", "09:30"),
    ("B2",         True,  "09:30", "11:30"),
    ("점심·기타", False, "11:30", "12:30"),
    ("B3",         True,  "12:30", "14:30"),
    ("B4",         True,  "14:30", "17:00"),
    ("이동·휴식", False, "17:00", "19:00"),
    ("B5",         True,  "19:00", "21:00"),
    ("B6",         True,  "21:00", "23:00"),
]

# (name, color)
CATEGORIES = [
    ("업무",  "#3B82F6"),
    ("모임",  "#F59E0B"),
    ("휴식",  "#10B981"),
    ("코어1", "#8B5CF6"),
    ("코어2", "#EC4899"),
    ("코어3", "#EF4444"),
    ("점검",  "#0EA5E9"),
    ("기타",  "#6B7280"),
]


def hhmm_to_min(hhmm: str) -> int:
    """'HH:MM' 문자열을 자정 기준 분으로 변환."""
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def slots_for_day():
    """하루 30분 단위 슬롯 리스트. (slot_index, block_label, start_time, end_time)."""
    out = []
    idx = 0
    for label, _core, start, end in DAY_BLOCKS:
        s = int(start[:2]) * 60 + int(start[3:])
        e = int(end[:2]) * 60 + int(end[3:])
        cur = s
        while cur < e:
            nxt = cur + 30
            out.append(
                (
                    idx,
                    label,
                    f"{cur//60:02d}:{cur%60:02d}",
                    f"{nxt//60:02d}:{nxt%60:02d}",
                )
            )
            idx += 1
            cur = nxt
    return out
