# 테스트 대상 인벤토리 (0단계 정찰 산출물)

베이스라인: 기존 tests/ = 676 passed, 3 xfailed (22.65s). 작업트리 clean.
프레임워크: pytest 9.1.1 (이미 설치됨 · 추가 설치 없음). httpx 0.28.1 · starlette TestClient.

| # | 파일 | 종류 | 이름 | 줄 | 역할 |
|---|---|---|---|---|---|
| 1 | app/common.py | fn | `int_id` | 28 | 폼으로 온 '반드시 있어야 하는' 행 id. 못 읽거나 범위를 벗어나면 ValueError. |
| 2 | app/common.py | fn | `opt_id` | 41 | 폼으로 온 '비울 수 있는' 행 id(구분·상위 항목처럼). 비었거나 못 쓸 값이면 None. |
| 3 | app/common.py | fn | `_off_loop` | 52 | 구글 API·AppleScript·AI 호출처럼 느린 동기 함수를 스레드풀에서 실행한다. |
| 4 | app/common.py | fn | `_ko_weekday` | 65 | (문서 없음) |
| 5 | app/common.py | fn | `_pretty_date` | 70 | (문서 없음) |
| 6 | app/common.py | fn | `_short_date` | 75 | (문서 없음) |
| 7 | app/common.py | fn | `asset_ver` | 96 | 정적 파일의 최신 수정시각을 캐시버스팅 쿼리값으로 반환(파일 바뀌면 자동 변경). |
| 8 | app/common.py | fn | `_client_settings` | 125 | 브라우저 JS가 읽는 설정만 추린 dict. |
| 9 | app/common.py | fn | `today_str` | 136 | (문서 없음) |
| 10 | app/common.py | fn | `week_start` | 140 | (문서 없음) |
| 11 | app/common.py | fn | `_weekday_of` | 144 | 'YYYY-MM-DD'의 요일(0=월 ~ 6=일). 요일별 세션 시간을 고르는 데 쓴다. |
| 12 | app/common.py | fn | `_skeleton_matches_config` | 149 | DB의 그날 블록 골격이 현재 효과적 설정(요일별 시간 편집 반영)과 정확히 같은지. |
| 13 | app/common.py | fn | `_day_has_content` | 166 | 그날에 사용자가 입력한 내용이 있는지(슬롯 do·한 일·구분·완료·주계획 연결, |
| 14 | app/common.py | fn | `ensure_day_skeleton` | 212 | 블록·슬롯이 없으면 생성한다. 설정이 바뀌었고 입력이 없는 날은 새 배치로 자동 재생성한다. |
| 15 | app/common.py | fn | `_name_override` | 254 | 블록 이름 입력값을 주간 상속과 비교해 덮어쓰기 값(없으면 None)을 돌려준다. |
| 16 | app/common.py | fn | `_split3` | 263 | 줄바꿈으로 저장된 목표/계획을 정확히 3칸으로 분리(빈 칸 유지). |
| 17 | app/common.py | fn | `_join3` | 269 | 폼의 prefix1/2/3 값을 줄바꿈으로 합친다. 각 칸 내부의 줄바꿈은 공백으로 눌러 |
| 18 | app/common.py | fn | `_parse_date` | 280 | 'YYYY-MM-DD' 를 date 로. 형식이 틀리면 None. |
| 19 | app/common.py | fn | `lt_tree_order` | 291 | 장기 항목을 상위 바로 아래에 하위가 오도록 줄 세우고 depth(겹 단계)를 붙인다. |
| 20 | app/common.py | fn | `lt_leaves` | 316 | 장기 항목 줄에서 실제로 손에 잡히는 최하위(하위가 없는) 것만 남긴다. |
| 21 | app/common.py | fn | `week_lt_items` | 332 | 그 주에 걸친 장기 항목(활성 영역만) 중 최하위 것만. 상위 제목을 함께 준다. |
| 22 | app/common.py | fn | `week_todos` | 354 | 그 주 '목표' 열에 적힌 할 일 목록. 오늘 탭 블록·슬롯을 여기에 잇는다. |
| 23 | app/common.py | fn | `_like_pattern` | 387 | LIKE 검색어를 안전하게 만든다. %, _ 는 사용자가 친 글자 그대로 찾도록 이스케이프한다. |
| 24 | app/common.py | fn | `_rule_distribute` | 396 | 부모 텍스트를 자식 n개 내용으로 나눈다. 여러 줄이면 분배, 한 줄이면 참고로 복제. |
| 25 | app/common.py | fn | `_ai_split` | 409 | AI로 상위 계획을 각 자식 기간(labels)별 내용으로 나눈다. 실패·미설정 시 None. |
| 26 | app/common.py | fn | `walk` | 306 | (문서 없음) |
| 27 | app/config.py | fn | `_detect_cloud_dir` | 15 | OneDrive 오프사이트 백업 루트. SIXBLOCK_CLOUD_DIR 로 지정, 없으면 OneDrive 자동 탐지(맥 교체 대비). |
| 28 | app/config.py | fn | `cat_tone` | 100 | 카테고리 이름의 기본 색 톤을 돌려준다(신규 시드·폴백용). 모르면 black. |
| 29 | app/config.py | fn | `area_tone` | 122 | 장기 영역의 기본 색 톤. 표시 순서대로 팔레트를 돌려 쓴다(색이 겹치지 않게). |
| 30 | app/config.py | fn | `hhmm_to_min` | 166 | 'HH:MM' 문자열을 자정 기준 분으로 변환. |
| 31 | app/config.py | fn | `slots_for_day` | 171 | 하루 30분 단위 슬롯 리스트. (slot_index, block_label, start_time, end_time). |
| 32 | app/db.py | fn | `uid_from_created` | 31 | 생성시각 문자열로 기록 공용 키(YYYYMMDD-HHMM-난수4, Record FORMAT.md 표준)를 만든다. |
| 33 | app/db.py | fn | `init_db` | 37 | (문서 없음) |
| 34 | app/db.py | fn | `_seed_categories` | 65 | 카테고리가 비어 있으면 기본 6종을 넣는다(기존 데이터는 건드리지 않음). |
| 35 | app/db.py | fn | `_seed_areas` | 77 | 장기플랜 영역이 비어 있으면 기본 영역을 넣는다(기존 데이터는 건드리지 않음). |
| 36 | app/db.py | fn | `_seed_settings` | 89 | 기본 동작 설정 키가 없으면 기본값으로 채운다(기존 값은 유지). |
| 37 | app/db.py | fn | `_migrate` | 99 | 기존 DB에 누락된 컬럼을 추가하고, 더 이상 쓰지 않는 컬럼을 정리한다. |
| 38 | app/db.py | fn | `get_conn` | 280 | (문서 없음) |
| 39 | app/db.py | fn | `get_settings` | 303 | 모든 동작 설정을 dict로 반환한다(기본값 위에 DB 저장값을 덮어쓴다). 결과는 캐시한다. |
| 40 | app/db.py | fn | `set_setting` | 319 | 설정 한 개를 저장한다(없으면 추가, 있으면 갱신). 저장 후 캐시를 비운다. |
| 41 | app/db.py | fn | `_parse_times` | 339 | 저장값(JSON 문자열 또는 리스트)을 길이 8 리스트로. 형식이 다르면 None. |
| 42 | app/db.py | fn | `_apply_times` | 353 | 블록 목록 위에 시간 배열을 입힌다(비어 있는 칸은 원래 값 유지). |
| 43 | app/db.py | fn | `get_weekday_overrides` | 365 | 요일 덮어쓰기 전체. {"0": [{start,end}...], ...} 형태이며 덮어쓴 요일만 들어 있다. |
| 44 | app/db.py | fn | `get_weekday_concepts` | 381 | 요일 컨셉 7칸. 저장값이 없거나 형식이 다르면 빈 칸 7개를 돌려준다. |
| 45 | app/db.py | fn | `get_day_blocks` | 395 | 효과적인 하루 8블록 목록. 공통 시간 위에 그 요일 덮어쓰기가 있으면 덧입힌다. |
| 46 | app/integrations/ai.py | fn | `_cfg` | 10 | (api_key, base_url, model). 주소·모델은 설정값이 우선, 없으면 .env 값. 키는 .env만. |
| 47 | app/integrations/ai.py | fn | `enabled` | 18 | 키·주소·모델이 모두 있으면 AI 사용 가능. |
| 48 | app/integrations/ai.py | fn | `status` | 24 | 설정 화면용 상태(키는 존재 여부만 노출, 값은 노출하지 않는다). |
| 49 | app/integrations/ai.py | fn | `complete` | 31 | OpenAI 호환 chat/completions 1회 호출. 실패·미설정이면 None(호출측이 규칙기반으로 폴백). |
| 50 | app/integrations/gcal.py | fn | `enabled` | 27 | (문서 없음) |
| 51 | app/integrations/gcal.py | fn | `status` | 31 | 헬스체크용. 캘린더별 설정·도달 여부·VEVENT 개수. |
| 52 | app/integrations/gcal.py | fn | `_fetch` | 56 | .ics를 받아 파싱해 캐시에 넣는다. 실패하면 이전 캐시를 그대로 둔다. |
| 53 | app/integrations/gcal.py | fn | `_refresh_later` | 70 | 뒤에서 한 번만 새로 받는다(같은 주소가 겹쳐 도는 것을 막는다). |
| 54 | app/integrations/gcal.py | fn | `_load_calendar` | 87 | 캘린더를 캐시에서 준다. 만료됐으면 있는 것을 그대로 주고 새것은 뒤에서 받는다. |
| 55 | app/integrations/gcal.py | fn | `events_for_range` | 103 | [start, end] 구간의 날짜별 일정. dict['YYYY-MM-DD'] -> [event...]. 여러 캘린더 병합. |
| 56 | app/integrations/gcal.py | fn | `events_for_date` | 129 | (문서 없음) |
| 57 | app/integrations/gcal.py | fn | `_to_kst` | 133 | (문서 없음) |
| 58 | app/integrations/gcal.py | fn | `_normalize` | 139 | (문서 없음) |
| 59 | app/integrations/gcal.py | fn | `run` | 77 | (문서 없음) |
| 60 | app/integrations/gcal_write.py | fn | `enabled` | 37 | (문서 없음) |
| 61 | app/integrations/gcal_write.py | fn | `invalidate_cache` | 46 | 수동 동기화에서 60초 읽기 캐시를 비워 다음 조회가 즉시 구글을 다시 읽게 한다. |
| 62 | app/integrations/gcal_write.py | fn | `_svc` | 51 | 서비스계정 자격증명으로 Calendar 서비스를 만들고 캐시한다. 비활성이면 None. |
| 63 | app/integrations/gcal_write.py | fn | `status` | 65 | 헬스체크용. 라이브러리·설정·실제 캘린더 접근 가능 여부. |
| 64 | app/integrations/gcal_write.py | fn | `_next_day` | 80 | 종일 이벤트의 end.date는 종료 다음날(배타적)이라 하루 더한다. |
| 65 | app/integrations/gcal_write.py | fn | `_hashtags` | 86 | '진로, 건강' → '#진로 #건강' (구글 캘린더 검색에 걸리도록 해시태그로). |
| 66 | app/integrations/gcal_write.py | fn | `_build_description` | 92 | 내용 + 해시태그 + 표식으로 설명란을 만든다(검색·역파싱 가능하게). |
| 67 | app/integrations/gcal_write.py | fn | `_norm_kind` | 104 | (문서 없음) |
| 68 | app/integrations/gcal_write.py | fn | `parse_summary` | 110 | '[종류] 제목' → (kind, title). 형식이 아니면 (고민, 통째 제목). |
| 69 | app/integrations/gcal_write.py | fn | `parse_description` | 118 | 설명란 → (content, tags). 표식·해시태그 줄을 걷어내 내용을 복원하고 태그를 뽑는다. |
| 70 | app/integrations/gcal_write.py | fn | `create_event` | 131 | '[종류] 제목' 요약 + 내용/해시태그 설명으로 종일 이벤트를 만들고 event id를 돌려준다. |
| 71 | app/integrations/gcal_write.py | fn | `service_account_email` | 150 | 캘린더 공유 안내용 서비스계정 이메일(키파일의 client_email). |
| 72 | app/integrations/gcal_write.py | fn | `calendar_id` | 170 | 쓰기용 캘린더 ID. 설정에 넣은 값이 우선, 없으면 .env 값. |
| 73 | app/integrations/gcal_write.py | fn | `write_enabled` | 182 | 그 캘린더에 쓸 수 있는지(캘린더 ID + 서비스계정 키파일 + 라이브러리). |
| 74 | app/integrations/gcal_write.py | fn | `test_write` | 192 | 그 캘린더에 테스트 이벤트를 만들고 즉시 지워 쓰기 권한을 확인한다. |
| 75 | app/integrations/gcal_write.py | fn | `create_calendar_event` | 223 | 오늘 탭에서 만든 일정을 일정용 캘린더에 생성한다. 시간 있으면 1시간 블록, 없으면 종일. |
| 76 | app/integrations/gcal_write.py | fn | `_achieve_description` | 252 | 달성 항목 리스트를 빈 칸은 빼고 '1. 2. 3.' 형식으로 번호를 다시 매겨 설명란을 만든다. |
| 77 | app/integrations/gcal_write.py | fn | `upsert_achievement_event` | 258 | 그날 달성을 성과 캘린더에 종일 이벤트 1개로 만들거나 갱신한다. |
| 78 | app/integrations/gcal_write.py | fn | `update_event` | 299 | 이벤트의 요약·설명을 현재 종류/제목/내용/태그로 갱신한다(종류 변경·제목 정정용). |
| 79 | app/integrations/gcal_write.py | fn | `_review_copy_content` | 321 | 다시보기 사본 이벤트 본문. 다시보기 내용을 위에, 원본을 아래에 둔다(다시보기 우선). |
| 80 | app/integrations/gcal_write.py | fn | `create_review_copy` | 330 | 다시보기 사본 이벤트를 만든다(설명 = 다시보기 내용 우선 + 원본). |
| 81 | app/integrations/gcal_write.py | fn | `update_review_copy` | 337 | 다시보기 사본 이벤트의 설명을 다시보기 내용 우선으로 갱신한다. |
| 82 | app/integrations/gcal_write.py | fn | `delete_event` | 344 | 이벤트를 삭제한다. 성공 여부를 돌려준다(없거나 비활성이면 False). |
| 83 | app/integrations/gcal_write.py | fn | `list_reflection_events` | 359 | [start, end] 구간 고결감 캘린더 이벤트를 (id, kind, title, content, tags, date)로 파싱. |
| 84 | app/integrations/things.py | fn | `_run` | 31 | osascript 실행. (returncode, stdout) 반환, 실패 시 (None, ''). |
| 85 | app/integrations/things.py | fn | `_today_names` | 45 | (문서 없음) |
| 86 | app/integrations/things.py | fn | `_fetch_into_cache` | 66 | AppleScript로 Today를 읽어 캐시에 넣는다. 실패하면 이전 캐시를 그대로 둔다. |
| 87 | app/integrations/things.py | fn | `_refresh_later` | 75 | 뒤에서 한 번만 새로 읽는다(겹쳐 도는 것을 막는다). |
| 88 | app/integrations/things.py | fn | `today_tasks` | 94 | Things3 'Today' 목록을 반환한다. (제목만; 시간/마감 없음) |
| 89 | app/integrations/things.py | fn | `status` | 119 | 헬스체크용. AppleScript 권한/연결 상태와 Today 개수. |
| 90 | app/integrations/things.py | fn | `enabled` | 133 | 할일 쓰기는 macOS에서만(AppleScript). 권한 미승인 시 add_todo가 실패로 알린다. |
| 91 | app/integrations/things.py | fn | `add_todo` | 153 | Things3 Inbox에 할일을 만든다. 성공 여부 반환. |
| 92 | app/integrations/things.py | fn | `run` | 83 | (문서 없음) |
| 93 | app/main.py | fn | `_warm_caches` | 19 | 구글 캘린더·Things 캐시를 미리 채운다. |
| 94 | app/main.py | fn | `lifespan` | 37 | (문서 없음) |
| 95 | app/main.py | fn | `_netloc_key` | 60 | '호스트:포트'로 정규화한다(기본 포트는 생략해 http://a 와 a:80 을 같게 본다). |
| 96 | app/main.py | fn | `_origin_allowed` | 69 | 요청 출처(Origin/Referer)가 이 서버 자신인지. 설정의 추가 허용 호스트도 인정한다. |
| 97 | app/main.py | MW | `csrf_origin_guard` | 82 | 쓰기 요청(POST 등)이 다른 사이트에서 온 것이면 막는다. |
| 98 | app/main.py | MW | `cache_headers` | 95 | HTML은 늘 다시 받고, 버전이 붙은 정적 파일은 오래 캐시한다. |
| 99 | app/main.py | EP | `get('/')` | 127 | (문서 없음) |
| 100 | app/main.py | EP | `get('/version')` | 136 | 지금 서버가 내보내는 app.js/style.css 버전. 화면이 옛 코드를 들고 있는지 스스로 판단해 |
| 101 | app/main.py | EP | `get('/sw.js')` | 143 | (문서 없음) |
| 102 | app/main.py | EP | `get('/manifest.webmanifest')` | 155 | (문서 없음) |
| 103 | app/main.py | EP | `get('/favicon.ico')` | 165 | (문서 없음) |
| 104 | app/main.py | EP | `get('/apple-touch-icon.png')` | 172 | (문서 없음) |
| 105 | app/main.py | EP | `get('/api/now')` | 178 | 클라이언트가 서버 시각 기준으로 포모도로 정렬할 수 있게 KST를 반환. |
| 106 | app/routes/analytics.py | fn | `_calc_streak` | 26 | 오늘(기록 없으면 어제)부터 거꾸로 연속으로 기록이 있는 날 수를 센다. |
| 107 | app/routes/analytics.py | fn | `_on_this_day` | 40 | 예전 오늘(어제·1주·한 달·1년 전)의 한 일(슬롯)과 고결감을 모아 회고용으로 돌려준다. |
| 108 | app/routes/analytics.py | fn | `_build_insights` | 68 | 축적 데이터에서 규칙기반 개선점 문장을 만든다(근거가 충분한 항목만). |
| 109 | app/routes/analytics.py | fn | `_ai_insights` | 109 | AI로 지표를 요약한 짧은 개선 제안. 실패·미설정 시 None. |
| 110 | app/routes/analytics.py | fn | `_exec_funnel` | 124 | 실행 퍼널: 코어 블록 계획(구분) → 슬롯 구체화(DO) → 슬롯 실행(done·한일) 3단계 비율과, |
| 111 | app/routes/analytics.py | fn | `_analytics_data` | 174 | 분석 지표를 한 번에 계산한다. 화면(/analytics)과 AI 제안(/analytics/ai)이 함께 쓴다. |
| 112 | app/routes/analytics.py | EP | `get('/analytics')` | 313 | (문서 없음) |
| 113 | app/routes/analytics.py | EP | `post('/analytics/ai')` | 332 | 분석 화면의 'AI 제안 받기' 버튼. 누를 때만 AI를 호출한다(로드마다 부르지 않는다). |
| 114 | app/routes/analytics.py | fn | `_search_records` | 346 | 슬롯 DO·한일과 블록 PLAN·SEE·이름을 날짜를 가로질러 찾아 (slots, blocks) 반환. |
| 115 | app/routes/day.py | fn | `_hidden_task_titles` | 31 | 설정에서 정한 '화면에 안 보이게 할 할일 제목'(쉼표 구분). 제목이 정확히 같은 것만. |
| 116 | app/routes/day.py | EP | `get('/today')` | 38 | (문서 없음) |
| 117 | app/routes/day.py | EP | `get('/day/{date_str}')` | 43 | (문서 없음) |
| 118 | app/routes/day.py | fn | `_distribute` | 51 | 시각이 있는 항목을 시작 분 기준으로 해당 블록에 배치한다. |
| 119 | app/routes/day.py | fn | `_day_agenda` | 75 | 그날의 캘린더 일정·Things Today를 모으고 시간 항목을 블록에 배치한다. |
| 120 | app/routes/day.py | fn | `_lt_columns` | 119 | 오늘 띠 항목을 영역별로 묶어 최대 세 열로 나눈다. |
| 121 | app/routes/day.py | fn | `_lt_items_on` | 136 | 그 날짜에 걸친 장기 항목 중 최하위 것만. 오늘 탭 위 띠에 쓴다. |
| 122 | app/routes/day.py | fn | `_day_view` | 177 | (문서 없음) |
| 123 | app/routes/day.py | EP | `post('/save/day/{date_str}')` | 336 | (문서 없음) |
| 124 | app/routes/day.py | fn | `_merge3` | 504 | 3칸 중 폼에 온 칸만 갈아끼워 줄바꿈으로 합쳐 저장한다. |
| 125 | app/routes/day.py | EP | `post('/save/field')` | 528 | 한 필드만 즉시 저장한다. entity=block|slot|meta, id, field, value 를 받는다. |
| 126 | app/routes/day.py | EP | `post('/inbox/add')` | 705 | (문서 없음) |
| 127 | app/routes/day.py | EP | `post('/inbox/done/{item_id}')` | 720 | (문서 없음) |
| 128 | app/routes/day.py | EP | `post('/inbox/delete/{item_id}')` | 727 | 수집함 항목을 완전히 삭제한다(정리 ✓와 달리 DB에서 지움). |
| 129 | app/routes/day.py | EP | `post('/things/add')` | 738 | 오늘 탭에서 입력한 할일을 Things3 Today에 만든다(macOS AppleScript). |
| 130 | app/routes/day.py | EP | `post('/gcal/event/add')` | 754 | 오늘 탭에서 입력한 일정을 일정용 구글 캘린더에 만든다(서비스계정). |
| 131 | app/routes/day.py | EP | `post('/inbox/assign')` | 777 | 수집함 항목을 한 블록의 PLAN 끝에 한 줄로 옮기고 수집함에서는 정리한다(GTD 정리 단계). |
| 132 | app/routes/day.py | EP | `post('/block/rollover')` | 804 | 이 블록에 적어 둔 것을 내일 같은 블록으로 복사한다(미룬 계획 이월). |
| 133 | app/routes/day.py | EP | `post('/meta/tomorrow-goal')` | 902 | 하루 마감에서 적은 '내일 가장 중요한 일'을 다음 날 목표 1번에 저장한다. |
| 134 | app/routes/day.py | EP | `post('/slot/done/{slot_id}')` | 933 | DO 옆 체크박스. 즉시 저장(폼 저장과 별개). |
| 135 | app/routes/day.py | EP | `get('/api/day/{date_str}')` | 947 | 현재 캘린더·Things 아젠다를 JSON으로. 클라이언트가 주기적으로 폴링해 갱신. |
| 136 | app/routes/day.py | fn | `on_screen` | 826 | 화면에서 온 값이 있으면 그것, 없으면 저장값. |
| 137 | app/routes/plan.py | fn | `_parse_anchor` | 25 | anchor 쿼리(YYYY-MM-DD)를 date로. 비었거나 잘못되면 오늘(KST). |
| 138 | app/routes/plan.py | fn | `_month_last` | 33 | 그 달의 마지막 날. |
| 139 | app/routes/plan.py | fn | `_plan_columns` | 38 | (열 목록, 헤더 라벨). 보고 있는 기간이 왼쪽에서 두 번째 열에 오도록 앞에 하나를 더 둔다. |
| 140 | app/routes/plan.py | fn | `_span_header` | 118 | 보이는 기간 전체를 한 줄로. 해가 같으면 해를 한 번만 적는다. |
| 141 | app/routes/plan.py | fn | `_plan_nav` | 126 | 이전/다음 anchor(YYYY-MM-DD 문자열) 쌍. 그 단위 하나만큼만 옮긴다. |
| 142 | app/routes/plan.py | fn | `_plan_breadcrumb` | 140 | 연>분기>월>주 경로. 각 단위는 anchor가 속한 기간 라벨 + 그 단위로 가는 링크. |
| 143 | app/routes/plan.py | fn | `_lt_rollup` | 164 | 상위 사슬을 하위에 맞춘다. 기간은 하위를 모두 품도록 넓히고, 진척률은 하위 평균을 따른다. |
| 144 | app/routes/plan.py | fn | `_assign_lanes` | 204 | 한 줄 안에서 막대를 위아래 칸에 나눠 담는다. 쓴 칸 수(최소 MIN_LANES)를 돌려준다. |
| 145 | app/routes/plan.py | fn | `_lt_apply_delta` | 288 | 상위 기간이 움직인 만큼 하위 사슬의 시작·종료도 같이 민다. |
| 146 | app/routes/plan.py | fn | `_lt_descendants` | 332 | 그 항목 아래의 모든 하위 항목 id(깊이 무관). |
| 147 | app/routes/plan.py | fn | `_lt_root` | 344 | 그 항목이 속한 뿌리(최상위) 항목 id. 세로 순서는 뿌리 단위로만 매긴다. |
| 148 | app/routes/plan.py | fn | `_lt_rollup_parent` | 358 | 상위를 잃거나 얻은 쪽의 사슬을 남은 자식 기준으로 다시 계산한다. |
| 149 | app/routes/plan.py | fn | `_add_months` | 369 | 달을 n개 더한 날짜. 그 달에 없는 날(1/31 + 1개월)은 말일로 맞춘다. |
| 150 | app/routes/plan.py | fn | `_block_rows` | 376 | 간트 왼쪽에 세울 행. 코어블록 B1~B6 + 블록을 정하지 않은 항목이 모이는 '미지정' 한 줄. |
| 151 | app/routes/plan.py | fn | `_split_blocks` | 384 | 저장된 블록 값('B1,B5')을 코어블록 목록으로. 모르는 값·중복은 버리고 B1→B6 순으로 맞춘다. |
| 152 | app/routes/plan.py | fn | `_clean_blocks` | 393 | 폼에서 온 블록 값을 저장형('B1,B5')으로. 하나도 못 알아보면 ''(미지정). |
| 153 | app/routes/plan.py | fn | `_gantt_blocks` | 398 | 블록(B1~B6·미지정)별 간트 행 목록. 그 블록으로 배정된 막대가 한 줄에 모두 들어간다. |
| 154 | app/routes/plan.py | EP | `post('/plan/item/add')` | 500 | 간트 항목을 만든다. parent_id 를 주면 그 항목의 하위로 붙고 영역을 물려받는다. |
| 155 | app/routes/plan.py | EP | `post('/plan/item/update')` | 551 | 간트 항목의 제목·기간·진척률·블록을 고친다(보낸 값만 바꾼다). |
| 156 | app/routes/plan.py | EP | `post('/plan/item/shift')` | 626 | 계획 막대를 끈 만큼(일 단위) 좌우로 옮긴다. 기간 길이는 그대로다. |
| 157 | app/routes/plan.py | EP | `post('/plan/item/resize')` | 664 | 막대의 한쪽 끝(edge=start|end)만 끈 만큼(일 단위) 늘리거나 줄인다. |
| 158 | app/routes/plan.py | EP | `post('/plan/item/order')` | 708 | 막대의 세로 순서를 바꾼다. 옮길 막대(id)를 기준 막대(peer) 위(before)나 아래(after)에 둔다. |
| 159 | app/routes/plan.py | EP | `post('/plan/item/reparent')` | 765 | 막대를 다른 막대의 하위로 넣거나(parent_id), 영역에 놓아 최상위로 뺀다(area_id). |
| 160 | app/routes/plan.py | EP | `post('/plan/item/delete')` | 831 | 간트 항목을 지운다. 하위 항목도 함께 지워지고 상위 기간은 다시 계산된다. |
| 161 | app/routes/plan.py | EP | `get('/plan')` | 859 | (문서 없음) |
| 162 | app/routes/plan.py | EP | `post('/plan/area/add')` | 950 | (문서 없음) |
| 163 | app/routes/plan.py | EP | `post('/plan/area/update')` | 974 | 영역 이름이나 막대 색(tone)을 바꾼다. 보낸 값만 고친다. |
| 164 | app/routes/plan.py | EP | `post('/plan/area/move')` | 998 | (문서 없음) |
| 165 | app/routes/plan.py | EP | `post('/plan/area/delete')` | 1025 | 영역을 숨김 처리(소프트 삭제)한다. 그 영역의 계획 내용은 보존된다. |
| 166 | app/routes/plan.py | fn | `root_of` | 216 | (문서 없음) |
| 167 | app/routes/plan.py | fn | `open_at` | 229 | 그 칸이 어느 묶음에도 잡혀 있지 않고 기간도 안 겹치는가. |
| 168 | app/routes/plan.py | fn | `put` | 237 | (문서 없음) |
| 169 | app/routes/plan.py | fn | `overlaps` | 427 | (문서 없음) |
| 170 | app/routes/plan.py | fn | `bar` | 435 | (문서 없음) |
| 171 | app/routes/plan.py | fn | `walk` | 472 | (문서 없음) |
| 172 | app/routes/reflect.py | fn | `_reflect_title` | 20 | 제목이 비면 내용 첫 줄에서 만든다(구글 summary가 비지 않게). |
| 173 | app/routes/reflect.py | fn | `_cascade_local_delete` | 28 | 로컬 reflection 한 줄을 지우면서 짝(원본↔다시보기 사본) 관계를 정리한다. |
| 174 | app/routes/reflect.py | fn | `_import_gcal_reflections` | 40 | 고결감 캘린더와 로컬을 맞춘다(추가·수정·삭제). 구글에서 직접 만들거나 고치거나 지운 것을 |
| 175 | app/routes/reflect.py | fn | `_reflect_ctx` | 107 | 고결감 화면·부분갱신이 함께 쓰는 컨텍스트(목록·미도래·태그)를 만든다. |
| 176 | app/routes/reflect.py | fn | `_reflect_sig` | 182 | 목록·미도래의 현재 상태 지문. 자동 폴링에서 변화 없으면 화면을 건드리지 않게 비교한다. |
| 177 | app/routes/reflect.py | EP | `get('/reflect')` | 196 | (문서 없음) |
| 178 | app/routes/reflect.py | EP | `get('/reflect/list')` | 208 | 자동 폴링·수동 동기화용 부분 응답. 목록·미도래 HTML과 변경감지 지문을 돌려준다. |
| 179 | app/routes/reflect.py | EP | `get('/reflect/api/items')` | 222 | 외부 앱(Record 고결감 탭)용 JSON 목록. HTML 대신 구조화된 항목을 돌려준다. |
| 180 | app/routes/reflect.py | EP | `post('/reflect/add')` | 242 | (문서 없음) |
| 181 | app/routes/reflect.py | EP | `post('/reflect/sync/{item_id}')` | 288 | 캘린더 반영에 실패했던 항목을 다시 시도한다. |
| 182 | app/routes/reflect.py | EP | `post('/reflect/update/{item_id}')` | 313 | 종류·제목·내용·태그·다시 볼 날짜를 수정하고, 구글 이벤트와 다시보기 사본까지 함께 맞춘다. |
| 183 | app/routes/reflect.py | EP | `post('/reflect/delete/{item_id}')` | 410 | 기록을 삭제하고 캘린더 이벤트도 함께 지운다. 원본을 지우면 다시보기 사본도, |
| 184 | app/routes/reflect.py | EP | `post('/reflect/review-note/{item_id}')` | 444 | 다시보기 내용을 저장하고, 사본 캘린더 이벤트에 다시보기 내용을 우선 반영한다. |
| 185 | app/routes/settings.py | fn | `_recent_errors` | 59 | 서버 로그 끝부분에서 최근 500 응답과 마지막 오류 줄을 센다. |
| 186 | app/routes/settings.py | fn | `_record_status` | 89 | 기록이 언제까지 쌓여 있는지(마지막 기록일과 그 경과일). |
| 187 | app/routes/settings.py | EP | `get('/api/health')` | 109 | 연동·백업·기록·오류 상태를 한 번에. 설정 탭 상태판이 읽고, 직접 열어 봐도 된다. |
| 188 | app/routes/settings.py | fn | `_backup_status` | 130 | 로컬·클라우드 백업 폴더의 최신 .sql 덤프 상태(파일명·크기KB·경과일)를 돌려준다. |
| 189 | app/routes/settings.py | fn | `_routine_times` | 153 | 고정 할일 규칙에서 고를 수 있는 시작시각. 요일마다 블록 시간이 다를 수 있어 7일치를 합친다. |
| 190 | app/routes/settings.py | fn | `_load_cat_templates` | 159 | 구분 템플릿 목록을 셀(요일 0~6 × 코어블록 → 구분)과 고정 할일 규칙까지 채워 돌려준다. |
| 191 | app/routes/settings.py | EP | `get('/settings')` | 188 | (문서 없음) |
| 192 | app/routes/settings.py | fn | `_data_summary` | 226 | 데이터 탭 요약(기록 일수·슬롯 수·기간·미처리 수집함·활성 구분). |
| 193 | app/routes/settings.py | EP | `get('/data')` | 256 | 데이터 탭: 요약·백업·내보내기·삭제(설정에서 분리, 화면 2분할). |
| 194 | app/routes/settings.py | fn | `_block_scopes` | 269 | 세션 시간 편집 범위 8개(공통 + 월~일). 덮어쓰지 않은 요일은 공통 값을 그대로 보여준다. |
| 195 | app/routes/settings.py | fn | `_valid_hhmm` | 287 | 'HH:MM' 이고 00:00~24:00 범위인지. 분은 자유 — 세션 30분 단위는 블록 길이(30분 배수)로 보장한다. |
| 196 | app/routes/settings.py | fn | `_parse_scope` | 295 | 세션 시간 편집 범위 입력값을 (유효한가, 요일 또는 None) 으로. ''=공통, '0'~'6'=요일. |
| 197 | app/routes/settings.py | EP | `post('/settings/blocktimes')` | 306 | 8블록의 시작·끝 시간을 저장한다(라벨·코어여부·개수 고정). 30분 경계·겹침을 검증한다. |
| 198 | app/routes/settings.py | EP | `post('/settings/blocktimes/reset')` | 353 | 공통은 기본 시간표로, 요일은 덮어쓰기를 지워 공통을 따르게 되돌린다. |
| 199 | app/routes/settings.py | EP | `post('/settings/weekday-concepts')` | 369 | 요일별 컨셉 7칸(wd0~wd6, 0=월 ~ 6=일)을 저장한다. 오늘 탭 날짜 옆 괄호에 나온다. |
| 200 | app/routes/settings.py | EP | `post('/settings/events-calendar')` | 378 | 오늘 탭 일정 쓰기용 구글 캘린더 ID를 저장한다(빈 값이면 일정 쓰기 해제). |
| 201 | app/routes/settings.py | EP | `post('/settings/events-calendar/test')` | 387 | 저장된 일정용 캘린더에 테스트 이벤트를 만들고 지워 연결을 확인한다. |
| 202 | app/routes/settings.py | EP | `post('/settings/achieve-calendar')` | 393 | 오늘 '달성'을 쓸 성과 캘린더 ID를 저장한다(빈 값이면 성과 쓰기 해제). |
| 203 | app/routes/settings.py | EP | `post('/settings/achieve-calendar/test')` | 402 | 저장된 성과 캘린더에 테스트 이벤트를 만들고 지워 연결을 확인한다. |
| 204 | app/routes/settings.py | EP | `post('/settings/category/add')` | 408 | (문서 없음) |
| 205 | app/routes/settings.py | fn | `_hides_last_category` | 442 | 이 구분을 숨기면 고를 수 있는 구분이 하나도 안 남는지. |
| 206 | app/routes/settings.py | EP | `post('/settings/category/update')` | 450 | (문서 없음) |
| 207 | app/routes/settings.py | EP | `post('/settings/category/move')` | 477 | (문서 없음) |
| 208 | app/routes/settings.py | EP | `post('/settings/category/delete')` | 508 | 카테고리를 숨김 처리한다(소프트 삭제). 슬롯·블록의 기존 참조는 보존된다. |
| 209 | app/routes/settings.py | EP | `post('/settings/save')` | 524 | (문서 없음) |
| 210 | app/routes/settings.py | EP | `post('/settings/template/add')` | 539 | 새 구분 템플릿을 빈 상태로 추가하고 생성된 id를 돌려준다. |
| 211 | app/routes/settings.py | EP | `post('/settings/template/rename')` | 559 | 구분 템플릿 이름을 바꾼다. |
| 212 | app/routes/settings.py | EP | `post('/settings/template/delete')` | 579 | 구분 템플릿과 그 셀을 함께 삭제한다. |
| 213 | app/routes/settings.py | EP | `post('/settings/template/cell')` | 592 | 템플릿 한 칸(요일 0~6 × 코어블록)의 구분을 저장한다. 값이 비면 미지정. |
| 214 | app/routes/settings.py | fn | `_clean_weekdays` | 618 | '0,1,4' 형태로 요일을 정리한다. 0~6 밖의 값과 중복은 버린다. |
| 215 | app/routes/settings.py | EP | `post('/settings/routine/add')` | 626 | 빈 고정 할일 규칙 한 줄을 템플릿에 추가하고 생성된 id를 돌려준다. |
| 216 | app/routes/settings.py | EP | `post('/settings/routine/save')` | 649 | 고정 할일 규칙 한 줄(요일·시작시각·칸 수·할일·구분)을 저장한다. |
| 217 | app/routes/settings.py | EP | `post('/settings/routine/delete')` | 678 | 고정 할일 규칙 한 줄을 지운다(이미 채워 둔 칸은 그대로 남는다). |
| 218 | app/routes/settings.py | fn | `_env_file_path` | 696 | 프로젝트 루트의 .env 경로. |
| 219 | app/routes/settings.py | fn | `_read_env_text` | 704 | .env 내용을 문자열로 읽는다(없으면 빈 문자열). |
| 220 | app/routes/settings.py | fn | `_mask_env_text` | 712 | KEY=값 의 값을 가린다. 화면·브라우저 캐시·화면 공유에 시크릿이 그대로 남지 않게 한다. |
| 221 | app/routes/settings.py | fn | `_unmask_env_text` | 725 | 가려진 채로 돌아온 값(********)을 기존 .env의 실제 값으로 되돌린다. |
| 222 | app/routes/settings.py | EP | `post('/settings/env/save')` | 743 | .env 전체 내용을 저장한다. 직전 내용을 6block-data에 백업하고 임시파일로 원자적 |
| 223 | app/routes/settings.py | EP | `post('/settings/restart')` | 779 | 이 서버를 재시작한다(응답 후 SIGTERM 자기 종료 → launchd가 KeepAlive로 재기동). |
| 224 | app/routes/settings.py | EP | `post('/settings/ai/save')` | 789 | AI 연결의 base URL·모델을 저장한다(키는 보안상 .env AI_API_KEY로만 관리). |
| 225 | app/routes/settings.py | EP | `post('/settings/ai/test')` | 798 | 현재 설정으로 AI에 짧은 호출을 보내 연결을 확인한다. |
| 226 | app/routes/settings.py | EP | `post('/settings/backup')` | 814 | scripts/backup.py를 즉시 실행해 .sql 덤프를 만든다. |
| 227 | app/routes/settings.py | EP | `get('/settings/export.csv')` | 830 | 기간 내 슬롯 기록을 CSV로 내보낸다(엑셀 호환 UTF-8 BOM). |
| 228 | app/routes/settings.py | EP | `post('/settings/purge')` | 861 | 기간 내 기록(슬롯·블록·일 메타)을 삭제한다. 되돌릴 수 없다. |
| 229 | app/routes/week.py | fn | `_week_lt_rows` | 35 | 주간 목표 열에 세울 장기 항목을 상위별로 묶는다. |
| 230 | app/routes/week.py | EP | `get('/week')` | 77 | (문서 없음) |
| 231 | app/routes/week.py | EP | `get('/week/{date_str}')` | 82 | (문서 없음) |
| 232 | app/routes/week.py | fn | `_week_view` | 90 | (문서 없음) |
| 233 | app/routes/week.py | EP | `post('/week/save/{week_start_str}')` | 275 | (문서 없음) |
| 234 | app/routes/week.py | EP | `post('/week/apply-template')` | 363 | 선택한 구분 템플릿을 그 주 7일에 일괄 적용한다. 블록 구분 42칸 + 고정 할일 규칙. |
| 235 | app/routes/week.py | EP | `post('/week/item-to-theme')` | 452 | 이번 주 장기 항목 제목을 고른 블록(B1~B6)의 이번 주 이름으로 넣는다(장기 → 주간). |
| 236 | app/routes/week.py | EP | `post('/week/decompose-themes')` | 493 | 이번 주 계획(주간 목표 + 이 주 장기 항목)을 B1~B6 블록 테마로 나눈다. 빈 테마만 채운다. |
