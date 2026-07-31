-- 6블록 카테고리, 블록/슬롯, 일/주 메타, 주간 블록 테마, GTD 수집함을 저장하는 단일 스키마
-- 색은 tone(팔레트 키) 하나로만 칠한다. 옛 color(hex) 컬럼은 마이그레이션에서 제거된다.
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
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
    location TEXT,
    wk_todo TEXT,               -- 이 블록이 이은 그 주 할 일 키(쉼표로 여러 개)
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
    wk_todo TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(date, slot_index)
);

CREATE INDEX IF NOT EXISTS idx_slots_date ON slots(date);

CREATE TABLE IF NOT EXISTS daily_meta (
    date TEXT PRIMARY KEY,
    today_goal TEXT,
    daily_plan TEXT,
    memo TEXT,
    vow TEXT,
    gratitude TEXT,
    goal_tags TEXT,         -- 오늘 목표 3줄의 자유 태그(직접 입력)를 줄바꿈으로 저장
    plan_tags TEXT,         -- 오늘 달성 3줄의 자유 태그
    grat_tags TEXT,         -- 감사·반성 3줄의 자유 태그
    goal_links TEXT,        -- 목표 3줄이 각각 이은 그 주 할 일 키(줄바꿈 3칸)
    achieve_event_id TEXT   -- 그날 성과 캘린더 종일 이벤트 id(재저장 갱신·중복 방지)
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

-- 주간 '목표' 열에서 장기 항목마다 따로 적는 그 주의 계획.
-- 장기 항목 id로 묶으므로 항목이 늘거나 줄어도 남은 항목의 내용은 그대로다.
CREATE TABLE IF NOT EXISTS weekly_lt_goal (
    id INTEGER PRIMARY KEY,
    week_start TEXT NOT NULL,
    item_id INTEGER NOT NULL REFERENCES lt_item(id) ON DELETE CASCADE,
    goal_text TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(week_start, item_id)
);

CREATE INDEX IF NOT EXISTS idx_weekly_lt_goal_week ON weekly_lt_goal(week_start);

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

-- 장기플랜 영역(프로젝트·투자·학습·여가·기타). 설정처럼 추가·순서변경·숨김 가능.
-- 간트에서 행은 B1~B6 블록이고 영역은 막대 색(tone)으로만 구분한다.
CREATE TABLE IF NOT EXISTS lt_area (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    tone TEXT NOT NULL DEFAULT 'blue'    -- style.css 의 --tone-* 키
);

-- 장기플랜 간트 항목. 영역별 과제를 시작~종료 기간과 진척률(0~100)로 관리한다.
-- parent_id 로 상위(연·분기) 항목과 하위(월·주) 항목을 이어 연·분기·월·주를 한 줄기로 묶는다.
-- 상위 항목의 기간·진척률은 하위가 있으면 하위에서 자동으로 계산해 덮어쓴다.
CREATE TABLE IF NOT EXISTS lt_item (
    id INTEGER PRIMARY KEY,
    area_id INTEGER NOT NULL REFERENCES lt_area(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES lt_item(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    start_date TEXT NOT NULL,            -- YYYY-MM-DD
    end_date TEXT NOT NULL,              -- YYYY-MM-DD (시작일 이상)
    progress INTEGER NOT NULL DEFAULT 0, -- 0~100
    block_label TEXT,                    -- 간트 행이 될 코어블록(B1~B6). 쉼표로 여러 개, 비면 미지정
    hidden INTEGER NOT NULL DEFAULT 0,   -- 1이면 간트에서 접어 둔다('숨긴 항목 보기'로 다시 꺼낸다)
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lt_item_area ON lt_item(area_id, start_date);
CREATE INDEX IF NOT EXISTS idx_lt_item_parent ON lt_item(parent_id);

-- 고결감(고민·결정·감사)을 기록하고 구글 캘린더에 양방향으로 반영한다.
-- '다시 볼 날짜'를 넣으면 그 날짜로 사본 한 줄이 따로 생기고 source_id 로 원본을 가리킨다.
CREATE TABLE IF NOT EXISTS reflection (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                  -- 고민 | 결정 | 감사
    title TEXT,                          -- 구글 캘린더 제목칸
    text TEXT NOT NULL,                  -- 구글 캘린더 설명칸
    tags TEXT,                           -- 공백/쉼표로 구분한 태그(나중에 찾기 쉽게)
    event_date TEXT NOT NULL,            -- 기록일 YYYY-MM-DD (자동 입력)
    review_date TEXT,                    -- 다시 볼 날짜 YYYY-MM-DD (입력할 때만 저장)
    review_note TEXT,                    -- 그날 다시 읽고 남긴 메모
    source_id INTEGER,                   -- 다시보기 사본이면 원본 id (원본이면 NULL)
    uid TEXT,                            -- 기록 공용 키 YYYYMMDD-HHMM-난수4 (Record 통합용)
    created_at TEXT NOT NULL,
    gcal_event_id TEXT,                  -- 생성된 구글 캘린더 이벤트 id(삭제·중복방지용)
    synced INTEGER NOT NULL DEFAULT 0    -- 캘린더 반영 성공 여부
);

CREATE INDEX IF NOT EXISTS idx_reflection_kind ON reflection(kind, id);
-- 오늘 탭 '오늘 다시 볼 고결감'과 주간 리뷰가 날짜로 훑고, 사본은 원본 id 로 찾는다.
CREATE INDEX IF NOT EXISTS idx_reflection_date ON reflection(event_date);
CREATE INDEX IF NOT EXISTS idx_reflection_source ON reflection(source_id);

-- 구분 템플릿(요일 월~일 × 코어블록 구분). 주별로 골라 42칸(7일×6블록) 블록 구분을 일괄 입력한다.
CREATE TABLE IF NOT EXISTS cat_template (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- 템플릿 한 칸: (템플릿, 요일 0~6, 코어블록) → 구분. 비어 있으면 그 칸은 미지정.
CREATE TABLE IF NOT EXISTS cat_template_cell (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES cat_template(id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL,            -- 0=월 ~ 6=일 (date.weekday())
    block_label TEXT NOT NULL,           -- B1..B6
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    UNIQUE(template_id, weekday, block_label)
);

CREATE INDEX IF NOT EXISTS idx_cat_template_cell ON cat_template_cell(template_id);
