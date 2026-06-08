-- 6블록 카테고리, 블록/슬롯, 일/주 메타, 주간 블록 테마, 외부 일정을 저장하는 단일 스키마
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL,
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

-- Phase 3에서 Things3 / 구글 캘린더 연동 시 채울 외부 이벤트 캐시
CREATE TABLE IF NOT EXISTS external_events (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    title TEXT NOT NULL,
    raw_json TEXT,
    synced_at TEXT NOT NULL,
    UNIQUE(source, external_id)
);
