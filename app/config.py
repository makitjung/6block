# 하루 9블록(코어 6 + 버퍼 3)과 30분 슬롯, 카테고리 상수를 정의하는 설정 파일
from pathlib import Path

DB_PATH = Path.home() / "6block-data" / "blocks.db"
BACKUP_DIR = Path.home() / "6block-data" / "backups"

# (block_label, is_core, start, end)
DAY_BLOCKS = [
    ("B1",         True,  "07:00", "09:30"),
    ("B2",         True,  "09:30", "11:30"),
    ("점심·기타", False, "11:30", "12:30"),
    ("B3",         True,  "12:30", "14:30"),
    ("B4",         True,  "14:30", "17:00"),
    ("이동·휴식", False, "17:00", "19:00"),
    ("B5",         True,  "19:00", "20:00"),
    ("저녁",       False, "20:00", "21:00"),
    ("B6",         True,  "21:00", "22:30"),
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
