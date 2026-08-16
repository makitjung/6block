# 6block 4페르소나 QA 종합 보고서

작성일 2026-08-16 · 대상 커밋 5f5331c · 전 과정 실제 실행 기반

실행 없이 코드만 읽고 내린 판정은 이 보고서에 없다. 모든 PASS/FAIL 은 pytest 실행 출력에 근거한다.

---

## 1. 전체 요약

### 검증 규모

| 항목 | 수 |
|---|---|
| 인벤토리 총 대상 | 236개 (엔드포인트 80 + 도우미 156) |
| 실제 테스트를 돌린 대상 | 213개 (90.3%) |
| 이번에 새로 작성·실행한 테스트 | 802개 |
| 기존 테스트 (베이스라인) | 679개 (676 passed, 3 xfailed) |
| 최종 전체 스위트 | 1,481개 → **1,473 passed, 8 xfailed, 0 failed** (33.6초) |

### 발견 요약

검토한 이슈 7건 중 5건 확정, 2건 기각.

| 심각도 | 확정 | 내용 |
|---|---|---|
| Critical | 0건 | — |
| High | 0건 | 보고된 High 3건은 검증에서 전부 강등 또는 기각 |
| Medium | 3건 | 연도 극단값 500(2곳), 종일이벤트 다음날 계산, /plan 초선형 지연 |
| Low | 2건 | parse_summary 명세 위반, /save/field 계약 취약 |
| 기각 | 2건 | hhmm_to_min 형식검증, slots_for_day 경계초과 |

Critical·High 0건은 이 코드베이스가 이미 679개 테스트로 방어되고 있고, 위험 지점(기록 유실·구분 상속·마이그레이션)에 방어 코드와 주석이 촘촘히 깔려 있기 때문이다. 특히 2단계 엣지 페르소나가 `app/common.py` 에 135개 경계값 테스트를 퍼부었으나 발견 0건이었다. 기록 유실과 직결되는 `_day_has_content`·`_split3`·`_join3`·`ensure_day_skeleton` 이 전부 뚫리지 않았다.

---

## 2. 즉시 수정이 필요한 Critical / High

**해당 없음.**

페르소나가 High 로 보고한 3건은 적대적 검증과 실제 클라이언트 코드 대조에서 모두 기각되거나 강등됐다. 근거는 4절에 적는다. 없는 문제를 만들어 올리지 않기 위해 이 절을 비워 둔다.

---

## 3. Medium / Low 이슈

### M-1 (Medium) 연도 극단값에서 하루 화면이 500이 된다 · 서로 다른 2곳

| 항목 | 내용 |
|---|---|
| 대상 | `app/routes/day.py:180` (`next_date = d + 1일`), `app/common.py:340` (`week_lt_items` 의 `sunday = 월요일 + 6일`) |
| 기대 | 200, 또는 최소한 명확한 4xx |
| 실제 | `OverflowError: date value out of range` 가 그대로 올라와 500 |
| 재현 | `pytest tests/qa/test_p6_known_defects.py -q` |

실측한 정확한 경계다. 두 결함의 범위가 다르므로 한 곳만 고치면 나머지가 남는다.

| 날짜 | 다음날 계산 | 그 주 일요일 계산 | 화면 |
|---|---|---|---|
| 9999-12-26 (일) | OK | OK | 200 |
| 9999-12-27 (월) ~ 12-30 (목) | OK | **터짐** | 500 |
| 9999-12-31 (금) | **터짐** | **터짐** | 500 |

날짜 형식이 `YYYY-MM-DD` 로 유효해 `_parse_date` 검증을 통과한 뒤 계산 단계에서 터진다. 즉 입력 검증이 잡아 주지 못한다. 도달 경로는 주소를 직접 치거나 날짜 입력칸에 9999를 넣는 것이라 흔하지 않으나, CLAUDE.md 9절 12항이 연도 4자리 입력을 명시적으로 다루고 있어 9999는 사용자가 실제로 칠 수 있는 값이다. `tests/test_known_defects.py` 가 과거 "결함 1(잘못된 날짜 500)"을 고친 것으로 기록하고 있어, 같은 부류가 다른 경로로 남아 있는 셈이다.

### M-2 (Medium) 종일 이벤트의 다음날 계산에 가드가 없다

| 항목 | 내용 |
|---|---|
| 대상 | `app/integrations/gcal_write.py:80` `_next_day` |
| 기대 | 예외를 잡아 처리하거나 명확한 오류 |
| 실제 | `_next_day("9999-12-31")` → `OverflowError` |
| 재현 | `pytest tests/qa/test_p6_known_defects.py::test_종일이벤트_다음날_계산이_9999년에서_터진다 -q` |

`create_event` 소스에 `OverflowError` 처리가 없음을 `inspect.getsource` 로 확인했다(`test_p5_final_verify.py::test_next_day_no_guard_in_callers`). 구글 캘린더 쓰기가 켜져 있고 9999년 날짜로 종일 이벤트를 만들면 그대로 올라온다. M-1 과 같은 뿌리(파이썬 `date` 의 MAXYEAR=9999)라 함께 고치는 것이 맞다.

### M-3 (Medium) /plan 이 항목 수에 초선형으로 느려진다

실측값이다. 각 조건 5회 측정 중앙값, 첫 회는 워밍업으로 버렸다.

| 장기 항목 수 | GET /plan 응답시간 | 배수 |
|---|---|---|
| 0개 | 3.1ms | — |
| 10개 | 4.8ms | 1.0× |
| 200개 (20배) | 34.1ms | 7.1× |
| 2,000개 (200배) | **571.3ms** | 119× |

항목이 10배(200→2000) 늘 때 시간은 16.7배 늘었다. 선형이 아니다. 병목은 `app/routes/plan.py:204` `_assign_lanes` 다. 막대마다 `open_at` 이 `held` 전체와 해당 칸의 기존 막대 전부를 훑고(`any`/`all`), 영역이 바뀔 때마다 `max(b["lane"] for b in bars ...)` 로 전체 막대를 다시 순회한다(plan.py:253).

체감 판단은 이렇다. 개인용 앱이고 현실적인 장기 항목 수는 수십~200개 수준이며 그 구간은 34ms 로 문제없다. 2,000개는 과장된 규모다. 그래서 Medium 이고 즉시 고칠 필요는 없다. 다만 항목이 꾸준히 쌓이는 구조라 몇 년 뒤 500개를 넘으면 폰에서 체감되기 시작한다.

다른 화면은 전부 건강하다. 아래는 기록 일수를 늘려 가며 잰 값이다.

| 화면 | 기록 0일 | 30일 | 365일 | 1,000일 |
|---|---|---|---|---|
| /today | 6.0ms | 5.5ms | 5.5ms | 6.6ms |
| /week | 5ms | 6ms | 9ms | 14ms |
| /analytics | 3ms | 3ms | 28ms | 24ms |
| /reflect | 2ms | 2ms | 2ms | — |
| /settings | 5ms | 4ms | 4ms | — |
| /data | 2ms | 2ms | 6ms | — |
| /api/day | 2ms | 1ms | 1ms | — |

고결감 항목 1,000개에서 /reflect 는 19.5ms 였다.

### L-1 (Low) parse_summary 가 docstring 약속을 어긴다

| 항목 | 내용 |
|---|---|
| 대상 | `app/integrations/gcal_write.py:110` `parse_summary` |
| 명세 | docstring — "'[종류] 제목' → (kind, title). 형식이 아니면 (고민, **통째 제목**)." |
| 입력 | `"[고민 [부제]] 제목"` |
| 기대 | `("고민", "[고민 [부제]] 제목")` (통째) |
| 실제 | `("고민", "] 제목")` (앞부분 손실) |
| 재현 | `pytest tests/qa/test_p6_known_defects.py::test_중첩_대괄호_제목이_통째로_보존된다 -q` |

정규식 `^\s*\[(.+?)\]\s*(.*)$` 의 `(.+?)` 가 비탐욕적이라 첫 `]` 에서 끊긴다.

도달 조건을 좁혀 두는 것이 중요하다. **앱이 만든 요약에서는 절대 발생하지 않는다.** 종류가 고민·결정·감사 고정 집합이라 첫 대괄호 안에 `[` 가 들어갈 수 없기 때문이다. 제목 안의 대괄호는 정상 보존된다(`"[결정] [중요] 회의"` → `("결정", "[중요] 회의")`). 사용자가 구글 캘린더에서 직접 `[고민 [부제]] 제목` 같은 제목을 만들고 그것을 6block 이 되읽을 때만 잘린다. 그래서 Low 다.

### L-2 (Low) /save/field 가 그룹 키 없이 오면 200을 주면서 값을 버린다

| 항목 | 내용 |
|---|---|
| 대상 | `app/routes/day.py:624` → `_merge3` |
| 입력 | `{entity: meta, id: 날짜, field: dplan1, value: "내용"}` (그룹 키 없음) |
| 실제 | 200 OK 를 돌려주지만 `daily_meta.daily_plan` 은 빈 문자열 |
| 재현 | `pytest tests/qa/test_p6_known_defects.py::test_save_field_가_value_만으로도_저장한다 -q` |

`_merge3` 는 폼에서 `dplan1`·`dplan2`·`dplan3` 키를 찾는데, 최소 폼에는 `field` 와 `value` 만 있어 아무 칸도 갱신되지 않는다.

**현재 화면에서는 발생하지 않는다.** `app/static/app.js:3241-3245` 의 `bindAutoSave` 가 `data-as-prefix` 로 그룹의 세 칸을 모두 모아 함께 보내고(app.js:719-733 에서 goal·dplan·grat·concept 각 칸에 `asPrefix`/`asIdx` 를 심는다), 태그 경로(app.js:1049)는 `{[group+idx]: val}` 을 함께 싣는다. 실제 브라우저 페이로드로 저장·부분갱신이 정상 동작함을 확인했다(`test_p5_final_verify.py` 4건).

위험은 다른 클라이언트다. Record 앱이나 스크립트가 이 엔드포인트를 자연스러운 형태(`field`+`value`)로 부르면 200을 받고도 조용히 유실된다. 응답이 성공이라 호출자가 알아챌 방법이 없다.

---

## 4. 기각된 보고 (검증에서 버그가 아님이 확인됨)

거짓 양성을 남겨 두면 다음 사람이 같은 것을 다시 판다. 근거와 함께 못 박아 둔다.

### R-1 (보고 High → 기각) hhmm_to_min 이 형식 오류를 조용히 통과시킨다

`hhmm_to_min("0800")` 이 `480` 을 돌려주는 것은 사실이다(문자열 슬라이싱). 그러나 사용자 입력이 이 함수에 닿는 유일한 경로인 `POST /settings/blocktimes` 가 그 앞에서 `_valid_hhmm` 의 `^\d{2}:\d{2}$` 정규식으로 막는다(settings.py:319). 나머지 입력원인 `DAY_BLOCKS` 는 코드 내장 상수다. 즉 잘못된 형식이 도달하지 않는다.

### R-2 (보고 Medium → 기각) slots_for_day 가 블록 경계를 넘는 슬롯을 만든다

08:00~08:45 블록에서 08:30~09:00 슬롯이 생기는 것은 사실이다(`while cur < end_min` 이 시작만 본다). 그러나 `POST /settings/blocktimes` 가 `(끝 - 시작) % 30 != 0` 이면 400 으로 거절한다(settings.py:332, "블록 길이가 30분 단위여야 합니다"). 30분 배수가 아닌 블록은 저장될 수 없으므로 이 입력이 발생하지 않는다.

### R-3 (보고 High "데이터 유실" → Low 로 강등) /save/field 메타 3칸

3단계 통합 페르소나가 "사용자가 저장했다고 믿지만 DB에 안 들어간다"는 High 로 보고했고, 적대적 검증자도 파이썬 코드만 보고 진짜 결함으로 확정했다. **둘 다 틀렸다.** 실제 클라이언트(`app.js`)를 읽지 않았기 때문이다. 위 L-2 에 적은 대로 브라우저는 세 칸을 함께 보낸다. 실사용 데이터 유실은 없다. 남는 것은 엔드포인트 계약의 취약성뿐이라 Low 로 내렸다.

---

## 5. 테스트 커버리지 요약 (무엇을 못 봤는가)

**"문제가 발견되지 않았다"와 "문제가 없다"는 다르다.** 아래는 이번 검증이 닿지 못한 범위다.

### 5-1. 구조적으로 못 본 것

| 범위 | 규모 | 이유 |
|---|---|---|
| 프런트엔드 JS 동작 | `app.js` 4,230줄 | 4개 페르소나 범위 밖. 브라우저 없이는 이벤트·IME·포모도로·오프라인 큐를 실행할 수 없다. 기존 `test_frontend_static.py` 26개가 정적 검사만 한다 |
| CSS·레이아웃 | `style.css` 2,173줄 | 시각 검증 필요 |
| 구글 캘린더 실제 호출 | `gcal_write` 쓰기 함수 9개 (`create_event`, `update_event`, `delete_event`, `upsert_achievement_event`, `list_reflection_events`, `test_write`, `create_calendar_event`, `create_review_copy`, `update_review_copy`) | conftest 가 전부 스텁으로 껐다. 실제 호출은 과금·실데이터 오염 위험 |
| 구글 캘린더 수신 | `gcal._fetch`, `_load_calendar`, `_refresh_later`, `events_for_range` | 외부 .ics URL fetch·백그라운드 스레드·TTL 캐시 타이밍 |
| Things3 연동 | `things._fetch_into_cache`, `_refresh_later` | AppleScript 권한창이 뜬다 |
| AI 연동 | `ai.complete`, `analytics._ai_insights`, `_ai_split` (실호출) | 외부 LLM API 과금 |
| launchd 재시작·동시기동 | `init_db` 파일락, WAL 경쟁 | 프로세스 2개를 실제로 겹쳐 띄워야 재현된다. 메모리에 기록된 "kickstart -k 이중기동 레이스"는 이번에 재현하지 못했다 |
| `/settings/restart` | 1개 엔드포인트 | SIGTERM 자기 종료라 테스트에서 호출 금지 |

### 5-2. 부분만 본 것

`lt_tree_order` 의 순환 참조는 무한루프 위험이 있어 pytest 밖 타임아웃 장치 없이 끝까지 밀어붙이지 못했다. 기존 `tests/test_known_defects.py` 가 이미 xfail 로 못 박아 둔 항목이다(`/plan/item/reparent` 가 자기·하위 지정을 막아 현재는 도달 불가).

DB 쿼리를 무겁게 쓰는 도우미들(`analytics._exec_funnel`·`_analytics_data`, `settings._load_cat_templates`·`_data_summary`·`_block_scopes`, `plan._lt_rollup`·`_lt_apply_delta`, `reflect._cascade_local_delete`)은 1단계에서 단위로는 건너뛰고 3단계 통합에서 엔드포인트를 통해 간접적으로만 확인했다. 즉 이 함수들의 경계값은 직접 두드리지 않았다.

`24:00`·`25:00`·`00:60` 이 각각 1440·1500·60 으로 조용히 파싱되는 것은 관측했으나(R-1 과 같은 이유로 도달 불가), 범위 검증 자체는 추가하지 않았다.

### 5-3. 커버리지 숫자의 정직한 해석

인벤토리 236개 중 213개(90.3%)를 "테스트했다"고 적었으나, 이 중 상당수는 정상 입력 확인 수준이다. 진짜 적대적 경계값 공격을 받은 것은 2단계가 담당한 순수 함수들이다. 나머지 23개는 위 표의 외부 연동·재시작 계열이다.

---

## 6. 수정 우선순위 제안

### 1순위 — 연도 극단값 500 (M-1 + M-2)

세 곳을 한 번에 고치는 것이 맞다. 뿌리가 같고(파이썬 `date` MAXYEAR=9999) 개별로 고치면 나머지가 남는다.

- `app/routes/day.py:180` 의 `next_date`
- `app/common.py:340` `week_lt_items` 의 `sunday`
- `app/integrations/gcal_write.py:80` `_next_day`

가장 단순한 방법은 `_parse_date` 가 통과시키는 상한을 낮추는 것이다. 이 앱이 다루는 현실적 최대 날짜는 장기 계획의 몇 년 뒤이므로, 예컨대 9000년 이후를 형식 오류로 취급하면 세 곳이 한꺼번에 막힌다. 계산부마다 `try/except OverflowError` 를 세 번 다는 것보다 입구 하나를 좁히는 쪽이 CLAUDE.md 6절("50줄로 되면 200줄로 쓰지 않는다")에 맞다.

고치면 `tests/qa/test_p6_known_defects.py` 의 xfail 3건이 strict=True 때문에 XPASS 로 실패한다. 그때 해당 `@pytest.mark.xfail` 줄만 지우면 그대로 회귀 방지 테스트가 된다.

### 2순위 — /save/field 계약 (L-2)

Record 앱 연동을 계속 쓸 계획이면 지금 고치는 게 싸다. `_merge3` 안에서 그룹 키가 하나도 없을 때 `field` 자신을 키로 보고 `value` 를 반영하도록 한 줄 추가하면 된다. 연동 계획이 없으면 그냥 두어도 사용자에게는 아무 일도 일어나지 않는다.

### 3순위 — parse_summary 정규식 (L-1)

`(.+?)` 를 탐욕적 `(.+)` 로 바꾸면 docstring 약속대로 동작한다. 다만 앱이 만든 요약에는 영향이 없으므로 급하지 않고, 바꿀 때 기존 왕복 테스트가 깨지지 않는지 반드시 함께 돌려야 한다.

### 4순위 — /plan 레인 배치 (M-3)

당장은 손대지 않는 것을 권한다. 200개까지 34ms 로 충분히 빠르고, 최적화는 `_assign_lanes` 의 계층·영역·기간 규칙을 건드리는 일이라 시각적 회귀 위험이 실익보다 크다. 장기 항목이 500개를 넘어 체감되기 시작할 때 손대면 된다. 그때를 위해 측정 테스트(`tests/qa/test_p4_endpoint.py`)를 남겨 뒀다.

### 안 고칠 것

R-1, R-2 는 호출부가 막고 있어 고칠 필요가 없다. 다만 그 방어(`_valid_hhmm` 정규식, 30분 배수 검증)를 나중에 완화하면 두 결함이 즉시 살아난다. `tests/qa/test_v2_verify.py` 가 그 의존 관계를 테스트로 못 박아 뒀다.

---

## 7. 산출물

| 파일 | 내용 |
|---|---|
| `tests/qa/INVENTORY.md` | 236개 대상 전체 목록(파일·줄·역할) |
| `tests/qa/test_p1_*.py` (4개) | 1단계 유닛 311개 |
| `tests/qa/test_p2_*.py` (4개) | 2단계 엣지 368개 |
| `tests/qa/test_p3_*.py` (3개) | 3단계 통합 39개 |
| `tests/qa/test_p4_*.py` (2개) | 4단계 성능 34개 (측정값 `-s` 로 출력) |
| `tests/qa/test_v1~v4_verify.py` | 적대적 검증 25개 |
| `tests/qa/test_p5_final_verify.py` | 최종 재검증 17개 (실제 클라이언트 페이로드 대조 포함) |
| `tests/qa/test_p5_scale.py` | 쿼리 수·시간 스케일 측정 |
| `tests/qa/test_p6_known_defects.py` | 확정 결함 5건 xfail(strict) 고정 |

전체 실행 명령이다.

```bash
.venv/bin/python -m pytest tests/ -q --no-header -p no:cacheprovider
```

성능 측정값을 보려면 `-s` 를 붙인다.

```bash
.venv/bin/python -m pytest tests/qa/test_p4_endpoint.py tests/qa/test_p5_scale.py -q --no-header -p no:cacheprovider -s
```

---

## 부록 A. N+1 쿼리 점검 결과

데이터가 33배 늘어도 화면이 보는 범위(하루·한 주)는 그대로다. 쿼리 수가 함께 늘면 N+1 이다. 실측 결과 전부 상수였다.

| 기록 일수 | /week 쿼리 | /analytics 쿼리 | /today 쿼리 |
|---|---|---|---|
| 30일 | 306* | 22 | 17 |
| 365일 | 27 | 22 | 17 |
| 1,000일 | 27 | 22 | 17 |

\* 30일 행의 306은 그 주 골격을 처음 만드는 1회성 INSERT 다(블록 8 + 슬롯 30 × 7일). 이후 요청은 27로 떨어진다.

## 부록 B. 반복 실행 시 자원 증가

4단계 성능 감사자가 `tracemalloc` 과 `resource.getrusage` 로 같은 엔드포인트를 반복 호출하며 측정했다(18개 테스트, 11.04초). 메모리 단조 증가, sqlite3 커넥션 누수, 파일 디스크립터 증가 모두 **발견되지 않았다**. `get_conn` 의 contextmanager 가 예외 경로에서도 닫히는 것을 확인했다. `_asset_ver_cache`(10초 TTL)와 `_settings_cache` 는 키가 무한히 늘지 않는 구조다.

동시 요청 20~50개를 스레드로 던졌을 때 500 이나 `database is locked` 는 나오지 않았다. WAL + `busy_timeout=5000` 설정이 동작한다.
