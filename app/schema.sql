-- 6블록 카테고리, 블록/슬롯, 일/주 메타, 주간 블록 테마, GTD 수집함을 저장하는 단일 스키마
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL,
    tone TEXT NOT NULL DEFAULT 'black',
    display_order INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    block_order INTEGER NOT NULL,
    block_label TEXT NOT NULL,
    is_core INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    plan_text TEXT,
    see_text TEXT,
    name TEXT,
    category_id INTEGER REFERENCES categories(id),
    updated_at TEXT NOT NULL,
    UNIQUE(date, block_order)
);

CREATE INDEX IF NOT EXISTS idx_blocks_date ON blocks(date);

CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    block_id INTEGER NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    slot_index INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    do_text TEXT,
    did_text TEXT,
    category_id INTEGER REFERENCES categories(id),
    done INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(date, slot_index)
);

CREATE INDEX IF NOT EXISTS idx_slots_date ON slots(date);

CREATE TABLE IF NOT EXISTS daily_meta (
    date TEXT PRIMARY KEY,
    today_goal TEXT,
    daily_plan TEXT,
    memo TEXT,
    vow TEXT
);

CREATE TABLE IF NOT EXISTS weekly_meta (
    week_start TEXT PRIMARY KEY,
    weekly_goal TEXT,
    appointments TEXT,
    vow TEXT,
    memo TEXT
);

-- B1-B6 주간 테마 (한 주 동안 그 블록이 의미하는 바)
CREATE TABLE IF NOT EXISTS weekly_block_themes (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL,
    block_label TEXT NOT NULL,
    theme_text TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(week_start, block_label)
);

CREATE INDEX IF NOT EXISTS idx_weekly_themes_week ON weekly_block_themes(week_start);

-- GTD 빠른 수집함. 폰(안드로이드/아이폰)에서 떠오르는 생각을 즉시 적어둔다.
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_inbox_done ON inbox(done, id);

-- 앱 동작 설정(시작 화면·기본 테마·포모도로 기본값 등)을 담는 키-값 저장소
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 장기플랜 영역 행(프로젝트·투자·학습·여가·기타). 설정처럼 추가·순서변경·숨김 가능.
CREATE TABLE IF NOT EXISTS lt_area (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

-- 장기플랜 칸. 단위(연/분기/월/주) × 기간키 × 영역에 계획 텍스트를 저장한다.
-- period_key: 연 '2026', 분기 '2026-Q2', 월 '2026-06', 주 '2026-06-15'(그 주 월요일).
CREATE TABLE IF NOT EXISTS lt_plan (
    id INTEGER PRIMARY KEY,
    level TEXT NOT NULL,
    period_key TEXT NOT NULL,
    area_id INTEGER NOT NULL REFERENCES lt_area(id) ON DELETE CASCADE,
    content TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(level, period_key, area_id)
);

CREATE INDEX IF NOT EXISTS idx_lt_plan_lookup ON lt_plan(level, period_key);

-- 반복되는 고민·감상·결심을 기록하고 구글 캘린더 '고민/결심'에 반영한다.
CREATE TABLE IF NOT EXISTS reflection (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                  -- 고민 | 감상 | 결심
    text TEXT NOT NULL,
    tags TEXT,                           -- 공백/쉼표로 구분한 태그(나중에 찾기 쉽게)
    event_date TEXT NOT NULL,            -- 기록일 YYYY-MM-DD (자동 입력)
    review_date TEXT,                    -- 다시 볼 날짜 YYYY-MM-DD (입력할 때만 저장)
    created_at TEXT NOT NULL,
    gcal_event_id TEXT,                  -- 생성된 구글 캘린더 이벤트 id(삭제·중복방지용)
    synced INTEGER NOT NULL DEFAULT 0    -- 캘린더 반영 성공 여부
);

CREATE INDEX IF NOT EXISTS idx_reflection_kind ON reflection(kind, id);

-- 요일별 컨셉(월~일). 그 요일을 어떤 컨셉으로 보낼지. 오늘 각 블록 오른쪽에 표시한다.
CREATE TABLE IF NOT EXISTS weekday_concept (
    weekday INTEGER PRIMARY KEY,         -- 0=월 ~ 6=일 (date.weekday())
    text TEXT,
    updated_at TEXT NOT NULL
);
