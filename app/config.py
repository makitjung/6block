# 하루 8블록(코어 6 + 버퍼 2)과 30분 슬롯, 카테고리, 외부 연동 환경값을 정의하는 설정 파일
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 프로젝트 루트의 .env에서 시크릿(구글 캘린더 비공개 주소 등)을 읽는다. 없으면 무시.
load_dotenv(PROJECT_ROOT / ".env")

DB_PATH = Path.home() / "6block-data" / "blocks.db"
BACKUP_DIR = Path.home() / "6block-data" / "backups"

# 구글 캘린더 iCal 주소. 계획=GCAL_ICAL_URL(노랑), 모임/여행=GCAL_ICAL_URL_2(빨강).
GCAL_ICAL_URL = os.getenv("GCAL_ICAL_URL", "").strip()
GCAL_ICAL_URL_2 = os.getenv("GCAL_ICAL_URL_2", "").strip()

# 캘린더별 이름·색. url이 빈 것은 제외한다. color는 style.css의 --cal-* 토큰과 일치.
GCAL_CALENDARS = [
    c
    for c in (
        {"name": "계획", "color": "yellow", "url": GCAL_ICAL_URL},
        {"name": "모임/여행", "color": "red", "url": GCAL_ICAL_URL_2},
    )
    if c["url"]
]

# (block_label, is_core, start, end)
DAY_BLOCKS = [
    ("B1",   True,  "07:30", "09:30"),
    ("B2",   True,  "09:30", "11:30"),
    ("점심", False, "11:30", "12:30"),
    ("B3",   True,  "12:30", "14:30"),
    ("B4",   True,  "14:30", "16:30"),
    ("저녁", False, "16:30", "19:00"),
    ("B5",   True,  "19:00", "21:00"),
    ("B6",   True,  "21:00", "23:00"),
]

# (name, color) — 색은 라이트 기준 hex이며 실제 표시는 테마별 톤 변수(--tone-*)로 칠한다
CATEGORIES = [
    ("코어", "#1a73e8"),
    ("점검", "#188038"),
    ("업무", "#202124"),
    ("약속", "#d93025"),
    ("휴식", "#202124"),
    ("기타", "#202124"),
]

# 카테고리 이름 → 색 톤. 코어 파랑, 점검 녹색, 약속 빨강, 업무·휴식·기타 검정.
CAT_TONE = {
    "코어": "blue", "점검": "green",
    "약속": "red",
    "업무": "black", "휴식": "black", "기타": "black",
}


def cat_tone(name: str) -> str:
    """카테고리 이름의 기본 색 톤을 돌려준다(신규 시드·폴백용). 모르면 black."""
    return CAT_TONE.get(name, "black")


# 구분 색 팔레트(키 → 한글 이름). 각 키는 style.css의 --tone-* 토큰과 1:1로 대응한다.
# 설정 탭에서 카테고리 색을 이 중 하나로 고른다.
TONES = [
    ("blue", "파랑"), ("green", "녹색"), ("red", "빨강"), ("black", "검정"),
    ("yellow", "노랑"), ("orange", "주황"), ("purple", "보라"), ("teal", "청록"),
]
TONE_KEYS = {k for k, _name in TONES}

# 동작 설정 기본값(app_settings 시드·폴백용). 키 → 기본값(문자열).
DEFAULT_SETTINGS = {
    "start_view": "today",      # 시작 화면: today | week
    "default_theme": "light",   # 기본 테마: light | dark
    "pomo_auto": "0",           # 포모도로 자동 모드 기본값(0/1)
    "pomo_warn5": "1",          # 종료 5분 전 알람(0/1)
    "collapse_blocks": "1",     # 오늘 화면 '현재 블록만 보기' 기본값(0/1)
}


def hhmm_to_min(hhmm: str) -> int:
    """'HH:MM' 문자열을 자정 기준 분으로 변환."""
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def slots_for_day():
    """하루 30분 단위 슬롯 리스트. (slot_index, block_label, start_time, end_time)."""
    out = []
    idx = 0
    for label, _core, start, end in DAY_BLOCKS:
        cur = hhmm_to_min(start)
        end_min = hhmm_to_min(end)
        while cur < end_min:
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


# 주간 KPI 분모를 설정에서 파생한다(블록·슬롯 구성을 바꾸면 통계가 자동으로 맞춰진다).
CORE_BLOCK_COUNT = sum(1 for _label, is_core, _s, _e in DAY_BLOCKS if is_core)
WEEK_CORE_BLOCKS = CORE_BLOCK_COUNT * 7          # 코어 PLAN 사용 분모(주 7일)
WEEK_TOTAL_HOURS = len(slots_for_day()) * 0.5 * 7  # 기록된 시간 분모(주 전체 슬롯 시간)
