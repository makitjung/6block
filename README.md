# 6block

하루를 30분 슬롯과 6개 코어 블록으로 나눠 **계획(PLAN) → 실행(DO) → 복기(SEE)** 하는 개인용 시간관리 대시보드입니다. Mac mini에서 상시 구동하고 Tailscale로 휴대폰에서 PWA(앱)처럼 사용합니다.

---

## 1. 만든 목적

- 하루를 **6개 코어 블록(B1~B6) + 2개 버퍼 블록**, 그 안의 **30분 슬롯**으로 쪼개 시간을 흘려보내지 않고 의식적으로 쓰기 위함.
- 블록마다 **무엇을 할지(PLAN)** 정하고, 슬롯마다 **실제로 한 것(DO)** 을 기록하고, 끝나면 **돌아보기(SEE)** 하는 Plan-Do-See 루프를 한 화면에서 돌리기 위함.
- 포모도로 집중, GTD 빠른 수집, 구글 캘린더·Things3 같은 외부 일정/할 일을 **한 화면에 통합**해 도구를 옮겨다니지 않기 위함.
- Mac mini(상시 가동)에 두고, 외부에서는 Tailscale 사설망으로만 접속해 어디서든 같은 데이터를 쓰기 위함.

---

## 2. 주요 특징

### 하루 8블록 구조

| 블록 | 구분 | 시간 |
|------|------|------|
| B1 | 코어 | 07:30 – 09:30 |
| B2 | 코어 | 09:30 – 11:30 |
| 점심 | 버퍼 | 11:30 – 12:30 |
| B3 | 코어 | 12:30 – 14:30 |
| B4 | 코어 | 14:30 – 17:00 |
| 저녁 | 버퍼 | 17:00 – 19:00 |
| B5 | 코어 | 19:00 – 21:00 |
| B6 | 코어 | 21:00 – 23:00 |

### 오늘 화면 (`/today`)

- **목표 3 · 계획 3**, 메모·각오 입력.
- **빠른 수집함(GTD Inbox)**: 떠오른 생각을 Enter로 바로 적어두고 나중에 정리.
- **오늘 일정·할 일**: 구글 캘린더 일정과 Things3 Today 할 일을 최상단에 한 번에 모아 표시(60초 실시간 폴링).
- **블록마다 PLAN / SEE** 입력, 블록 이름 지정.
- **블록별 호버 버튼(일정 · 할 일)**: PLAN·SEE 위에서 그 시간대 캘린더 일정과 Things3 Today를 호버(폰은 탭)로 확인.
- **현재 블록만 보기**: 오늘 화면은 기본적으로 지금 시각의 블록만 표시하고, `전체 블록 보기` 버튼으로 9블록 전체를 펼쳐 스크롤.
- **30분 슬롯**: 슬롯마다 카테고리(색 띠), DO 입력, 실행 완료 체크박스.
- **포모도로(슬롯 종료시각까지 집중)**: ▶를 누르면 그 30분 슬롯이 끝날 때까지 집중하고, 자동 모드는 정각·30분 경계에 다음 슬롯을 자동 시작합니다. 별도 휴식 단계는 없고 종료 시 종소리·알림으로 알립니다.

### 주간 화면 (`/week`)

- 주간 목표, **블록 테마(B1~B6 이름)**, 카테고리별 사용 시간 통계, 코어 블록 달성률.
- 주간에서 정한 블록 이름이 일간 기본값이 되고, 일간에서 덮어쓸 수 있음.

### 그 외

- **PWA**: 홈 화면에 설치, 오프라인 폴백, 서비스워커 캐시.
- **다크 / 라이트 테마** 토글.
- **데이터**: 로컬 SQLite(`~/6block-data/blocks.db`), 매일 `.sql` 덤프 백업(로컬 + OneDrive 2곳).
- **기술 스택**: Python 3.13 · FastAPI · Jinja2 · SQLite · 바닐라 JS(빌드 단계 없음).

---

## 3. 주의사항

- **Things3 연동은 macOS 전용**입니다. AppleScript로 Today 목록을 읽으므로 시스템 설정 > 개인정보 보호 및 보안 > 자동화에서 권한 허용이 필요합니다. Today 목록만 가져오며 시간 정보는 없습니다.
- **구글 캘린더**는 "설정 및 공유 > 캘린더 통합 > iCal 형식의 **비공개** 주소(.ics)"를 `.env`의 `GCAL_ICAL_URL`에 넣어야 동작합니다. 비워두면 캘린더만 비활성화되고 앱은 정상 작동합니다.
- **인증이 없습니다.** 서버는 `0.0.0.0:8000`으로 열리므로 반드시 Tailscale 같은 사설망으로만 접근하세요. 공개 인터넷에 노출하지 마세요.
- **시크릿은 커밋 금지.** `.env`, `*.db`, `*.key`, `*.crt`는 `.gitignore`로 제외되어 있습니다.
- **설치형 PWA는 설치 시점의 매니페스트(가로회전 등)를 앱에 구워 저장**합니다. `manifest.json`을 바꾸면(예: 세로 잠금 → 가로 허용) 휴대폰에서 **앱을 삭제하고 다시 설치**해야 반영됩니다.
- **캐시 동작**: 정적 파일·HTML은 `Cache-Control: no-cache` + `?v=<수정시각>` 캐시버스팅이 적용되어, 코드 변경은 새로고침으로 반영됩니다. 다만 이미 옛 버전을 캐시한 기기는 1~2회 새로고침(또는 PWA 재설치)이 필요할 수 있습니다.
- **서버 재시작 기준**: `app/static`·`app/templates`(JS/CSS/HTML) 변경은 자동 반영되지만, **`main.py` 등 파이썬 코드 변경은 서버 재시작이 필요**합니다(launchd가 `--reload` 없이 구동).
- 모든 시각은 **KST(Asia/Seoul)** 고정입니다.

---

## 4. 사용법

### 요구 환경

- macOS (Apple Silicon), Python 3.13, 프로젝트 가상환경(`.venv`).

### 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # 이후 GCAL_ICAL_URL 입력(선택)
```

### 개발 실행

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
# 브라우저에서 http://127.0.0.1:8000  (자동으로 /today 로 이동)
```

### 상시 구동 (launchd)

Mac mini에서는 `~/Library/LaunchAgents/io.6block.uvicorn.plist`(`KeepAlive`)로 항상 떠 있습니다.

```bash
# 재시작 (코드 반영)
launchctl kickstart -k "gui/$(id -u)/io.6block.uvicorn"

# 등록 / 해제
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/io.6block.uvicorn.plist
launchctl bootout   "gui/$(id -u)/io.6block.uvicorn"

# 로그
tail -f ~/6block-data/uvicorn.err.log
```

### 휴대폰에서 앱으로 쓰기

1. 휴대폰을 Mac mini와 같은 Tailscale 네트워크에 연결.
2. 크롬으로 Mac mini 주소(예: `http://<tailscale-호스트>:8000/today`)에 접속.
3. 크롬 메뉴 → **홈 화면에 추가 / 앱 설치**로 PWA 설치.
4. 휴대폰 시스템의 **화면 자동 회전**이 켜져 있어야 가로 보기가 됩니다.

### 화면 / 점검 경로

- `/today` 오늘, `/week` 주간.
- `/api/health` 연동 상태(구글 캘린더·Things3), `/api/now` 서버 KST 시각.

### 백업 / 복원

```bash
# 백업 (launchd io.6block.backup 로도 매일 자동 실행)
.venv/bin/python scripts/backup.py
# 덤프 위치: ~/6block-data/backups/blocks-YYYYMMDD.sql, 그리고 OneDrive/AI/6block-backups

# 복원
sqlite3 ~/6block-data/blocks.db < ~/6block-data/backups/blocks-YYYYMMDD.sql
```

---

## 5. 프로젝트 구조

```
app/
  main.py              FastAPI 엔트리(라우팅·저장·폴링 API·PWA 서빙·캐시 헤더)
  config.py            블록/슬롯/카테고리/환경값 정의
  db.py                SQLite 연결·스키마 초기화·자동 마이그레이션
  schema.sql           테이블 스키마
  seed.py              초기 카테고리 시드
  integrations/
    gcal.py            구글 캘린더 비공개 iCal 파싱·캐시
    things.py          Things3 Today(AppleScript) 읽기
  templates/           base / today / week (Jinja2)
  static/              app.js · style.css · sw.js · manifest.json · 아이콘
scripts/
  backup.py            SQLite .sql 덤프 백업
requirements.txt
.env.example
```
