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
    -- 1이면 고정 할일(routine_rule)이 채운 칸. 사람이 계획한 것이 아니므로 통계에서 빼고,
    -- 다시 적용할 때 덮어써도 된다. 사람이 그 칸을 고치면 0으로 풀린다.
    is_routine INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(date, slot_index)
);

CREATE INDEX IF NOT EXISTS idx_slots_date ON slots(date);
-- 한 블록에 딸린 슬롯을 찾는 길(내일로 넘기기·수집함 배정·분석의 블록별 실행 판정).
-- 없으면 그때마다 slots 전체를 훑는다(3천행에서 21배, 1만2천행에서 34배 느렸다).
CREATE INDEX IF NOT EXISTS idx_slots_block ON slots(block_id);

CREATE TABLE IF NOT EXISTS daily_meta (
    date TEXT PRIMARY KEY,
    today_goal TEXT,
    daily_plan TEXT,
    memo TEXT,
    vow TEXT,
    gratitude TEXT,
    day_review TEXT,        -- 하루 마감의 '하루 평가'(그날 총평, 마크다운 목록 그대로)
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
    masked INTEGER NOT NULL DEFAULT 0,   -- 1이면 주간·오늘 탭에서 뺀다(장기 간트에는 그대로 그린다)
    sort_order INTEGER,                  -- 간트 세로 순서(손으로 정한 값). NULL이면 자동 배치
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

-- 템플릿. 한 표가 다섯 종류를 모두 담는다(표 이름은 옛 '구분 템플릿'에서 왔다).
--   S 세션시간 · T 고정할일 · N 블록이름 · C 구분 · W 주간
-- 화면 이름은 kind + no 로 짓는다(S1 · T2 · N1 · C1). 주간만 별도명칭을 붙여 W1(집중주)
-- 처럼 쓰고, 그 명칭이 name 칸이다. no 는 그 종류 안에서 만들 때 매기고 지운 번호는
-- 다시 쓰지 않는다 — 중간을 지웠을 때 남은 것의 번호가 밀리면 무엇을 골라 뒀는지
-- 알 수 없게 되기 때문이다.
-- 종류마다 쓰는 칸이 다르다. S 는 times_common·times_wd, N 은 block_names,
-- T 는 routine_rule, C 는 cat_template_cell·cat_template_slot 을 쓰고,
-- W 는 아무 내용도 없이 s_id·t_id·n_id·c_id 로 넷을 골라 묶기만 한다(안 고른 것은 NULL).
CREATE TABLE IF NOT EXISTS cat_template (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'W',      -- S | T | N | C | W
    no INTEGER NOT NULL DEFAULT 1,       -- 그 종류 안의 번호. S1 의 1
    name TEXT NOT NULL,                  -- 주간(W)의 별도명칭. 나머지 종류는 빈 문자열
    display_order INTEGER NOT NULL DEFAULT 0,
    -- 세션시간(S). 설정 화면과 같은 모양이다. 공통은 길이 8 JSON 배열 [{start,end}...],
    -- 요일 덮어쓰기는 {"0": [...], ...} 로 덮어쓴 요일만.
    times_common TEXT,
    times_wd TEXT,
    -- 블록 이름(N). {"B1": "이름", ...} 로 적은 것만.
    block_names TEXT,
    -- 주간(W)이 고른 것. 지운 템플릿을 고르고 있었으면 NULL 이 되어 그 부분만 빠진다.
    s_id INTEGER REFERENCES cat_template(id) ON DELETE SET NULL,
    t_id INTEGER REFERENCES cat_template(id) ON DELETE SET NULL,
    n_id INTEGER REFERENCES cat_template(id) ON DELETE SET NULL,
    c_id INTEGER REFERENCES cat_template(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

-- 같은 종류에 같은 번호가 둘 생기지 않게. 표 안 제약이 아니라 인덱스로 두는 이유는,
-- 옛 DB 를 마이그레이션할 때도 똑같이 걸리게 하기 위해서다(ALTER 로는 제약을 못 더한다).
CREATE UNIQUE INDEX IF NOT EXISTS idx_cat_template_kind_no ON cat_template(kind, no);

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

-- 템플릿의 칸 단위 구분: (템플릿, 요일 0~6, 코어블록, 블록 안 몇 번째 세션) → 구분.
-- p 는 1부터 세는 세션 번호다(B1p4 = 1블록의 네 번째 30분 칸). 시각으로 잡지 않으므로
-- 요일마다 블록 시작시각이 달라도 같은 자리를 가리킨다. 비어 있으면 블록 구분을 상속한다.
CREATE TABLE IF NOT EXISTS cat_template_slot (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES cat_template(id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL,            -- 0=월 ~ 6=일 (date.weekday())
    block_label TEXT NOT NULL,           -- B1..B6
    p INTEGER NOT NULL,                  -- 1부터. B1p4 의 4
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    UNIQUE(template_id, weekday, block_label, p)
);

CREATE INDEX IF NOT EXISTS idx_cat_template_slot ON cat_template_slot(template_id);

-- 그 날에만 쓰는 세션시간. 주간 탭 날짜 머리의 ⋯ 에서 세션시간 템플릿을 고르면 적힌다.
-- 날짜의 시간표를 정하는 차례는 이 표 → week_block_time → 설정 요일 → 설정 공통이다.
CREATE TABLE IF NOT EXISTS day_block_time (
    date TEXT PRIMARY KEY,               -- YYYY-MM-DD
    times TEXT NOT NULL,                 -- 길이 8 JSON 배열 [{start,end}...]
    updated_at TEXT NOT NULL
);

-- 어느 주·어느 날에 무슨 템플릿을 걸어 두었는지. 화면이 '지금 걸린 것'을 골라 둔 채로
-- 뜨게 하려고 적어 둔다(적용 자체는 이미 blocks·slots 에 반영돼 있다).
CREATE TABLE IF NOT EXISTS tpl_applied (
    scope TEXT NOT NULL,                 -- week | day
    key TEXT NOT NULL,                   -- 그 주 월요일 또는 날짜 YYYY-MM-DD
    kind TEXT NOT NULL,                  -- S | T | N | C | W
    tpl_id INTEGER REFERENCES cat_template(id) ON DELETE CASCADE,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, key, kind)
);

-- 그 주에만 쓰는 세션시간. 주간 탭에서 세션시간을 담은 템플릿을 고르면 여기에 적힌다.
-- times 는 7요일을 모두 풀어 둔 {"0": [{start,end} × 8], ... "6": [...]} 이다.
-- 풀어서 적는 이유는, 나중에 설정의 공통 시간을 고쳐도 이미 적용해 둔 주가 조용히
-- 달라지지 않게 하기 위해서다. 이 표에 줄이 없는 주는 설정값을 그대로 따른다.
CREATE TABLE IF NOT EXISTS week_block_time (
    week_start TEXT PRIMARY KEY,         -- 그 주 월요일 YYYY-MM-DD
    times TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 고정할일(T) 템플릿의 규칙. 그 템플릿을 고르면 이 규칙대로 30분 칸을 채운다.
-- 한 줄이 '요일 여러 개 × 시작시각 × 칸 수'라서 스무 줄이면 한 주의 고정 일과가 다 덮인다
-- (요일 7 × 하루 31칸 = 217칸짜리 격자를 만들지 않는 이유).
CREATE TABLE IF NOT EXISTS routine_rule (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES cat_template(id) ON DELETE CASCADE,
    weekdays TEXT NOT NULL DEFAULT '',   -- 적용 요일 '0,1,4' (0=월 ~ 6=일). 비면 적용하지 않는다
    start_time TEXT NOT NULL,            -- 'HH:MM'. 그 시각에 슬롯이 없는 날은 건너뛴다
    span INTEGER NOT NULL DEFAULT 1,     -- 시작 칸부터 이어서 채울 칸 수(1~4, 블록 경계는 넘어도 된다)
    do_text TEXT NOT NULL DEFAULT '',
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    display_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_routine_rule ON routine_rule(template_id);
