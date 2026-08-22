# 6block 테스트

실제 기록(`~/6block-data`)과 운영 서버(8000 포트)는 어떤 테스트도 건드리지 않는다.
`conftest.py` 가 임시 폴더에 DB·백업·.env 를 새로 만들고, 실제 데이터 경로로 가는
`sqlite3.connect` 를 아예 예외로 막는다. 구글 캘린더·Things3·AI 연동도 전부 꺼 둔다.

## 실행

전체 (단위 + 통합 + 프런트 정적, 6초쯤)

```bash
.venv/bin/python -m pytest tests/ -q
```

기존 스모크 (진짜 서버를 8011 포트에 띄워 HTTP 로 확인, 40초쯤)

```bash
.venv/bin/python tests/run_smoke.py
```

브라우저로 눈으로 볼 때 (임시 DB, 8024 포트)

```bash
.venv/bin/python tests/serve_isolated.py
```

## 파일 구성

| 파일 | 무엇을 보는가 |
| --- | --- |
| `conftest.py` | 격리 장치와 공용 픽스처(`client`, `fresh_db`, `conn`, `real_integrations`) |
| `test_unit_config.py` | 하루 8블록 골격, 30분 슬롯 생성, 색 팔레트 |
| `test_unit_common.py` | 3칸 입력, 날짜 파싱, 장기 항목 트리 정렬, LIKE 이스케이프 |
| `test_unit_csrf.py` | Origin 가드 정규화(접미사·후행점·포트 우회 시도 포함) |
| `test_routes_smoke.py` | 79개 라우트 전수. 정상값·악성 경로값·빈 폼·쓰레기 폼·교차 출처 |
| `test_flows.py` | 화면에서 실제로 하는 일을 HTTP 로 밟아 DB 까지 확인 |
| `test_integrations.py` | 구글·Things3·AI 고장 주입(네트워크는 부르지 않는다) |
| `test_frontend_static.py` | JS 문법, 한글 IME 가드, 자산 버전, 서비스워커 정책 |
| `test_known_defects.py` | 아직 안 고친 잠재 결함. xfail 로 통과하고 고치면 실패해 알려 준다 |
| `test_uncovered_paths.py` | 다른 테스트가 한 번도 안 밟던 사용자 경로(재시작 버튼, 고결감 삭제 연쇄, 캘린더 쓰기 게이트, .env 경로) |
| `run_smoke.py` | 진짜 서버를 띄워 HTML 구조까지 확인하는 208개 검사 |
| `serve_isolated.py` | 브라우저 확인용 격리 서버 |

## test_known_defects.py 를 다루는 법

여기 있는 테스트는 `xfail(strict=True)` 라서, 해당 결함을 고치면 **XPASS 로 실패한다.**
그게 정상이다. 고친 뒤 그 테스트의 `@pytest.mark.xfail` 줄을 지우면 평범한 회귀 테스트가 된다.

지금 남아 있는 셋은 전부 **화면에서는 도달할 수 없는** 잠재 결함이다. 호출부가 막고 있어서
현재는 문제가 안 되지만, 그 막는 곳을 손대면 여기가 먼저 알려 준다.

- `lt_tree_order` 가 parent_id 순환에서 항목을 잃는다 (`/plan/item/reparent` 가 막고 있음)
- `_rule_distribute(text, 0)` 이 ZeroDivisionError (호출부가 늘 코어블록 6개를 넘김)
- `_parse_date(20260815)` 가 AttributeError (폼 값은 늘 문자열)
