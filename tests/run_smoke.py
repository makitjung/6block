# 6block 전체 스모크 테스트. 임시 서버를 직접 띄우고 실제 HTTP 요청으로 확인한 뒤 정리한다.
# 실행 · .venv/bin/python tests/run_smoke.py   (외부 라이브러리 없이 표준 라이브러리만 사용)
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape as html_unescape

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("SIXBLOCK_TEST_PORT", "8011"))
BASE = f"http://127.0.0.1:{PORT}"
HOST = f"127.0.0.1:{PORT}"

passed: list[str] = []
failed: list[str] = []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"{mark}  {name}" + (f"   [{extra}]" if not cond else ""))


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.getcode(), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(path, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
            return r.getcode(), (json.loads(raw) if raw.startswith("{") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, (json.loads(raw) if raw.startswith("{") else raw)


def db_query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def wait_up(proc, timeout=40):
    """서버가 응답할 때까지 기다린다. 그 사이 죽으면 즉시 실패로 알린다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(BASE + "/today", timeout=3):
                return True
        except Exception:
            time.sleep(0.4)
    return False


def run_checks(db_path):
    # 1. 주요 화면이 모두 열리는지
    for path in ("/today", "/week", "/plan", "/settings",
                 "/analytics", "/data", "/reflect"):
        code, _ = get(path)
        check(f"GET {path} 200", code == 200, code)
    code, html = get("/today")
    nav = re.findall(r'<nav class="topnav">(.*?)</nav>', html, re.S)
    labels = re.findall(r">([^<>]+)</a>", nav[0]) if nav else []
    check("상위 탭 5개(오늘·주간·장기·고결감·설정)",
          labels == ["오늘", "주간", "장기", "고결감", "설정"], labels)

    # 2. 설정 화면 구조(그룹 4개 + 세션시간 공통·월~일 8칸)
    code, html = get("/settings")
    tabs = re.findall(r'<button[^>]*class="set-bt-tab[ "]', html)
    check("설정 그룹 4개", html.count('class="set-group"') == 4, html.count('class="set-group"'))
    check("세션시간 탭 8개(공통+월~일)", len(tabs) == 8, len(tabs))
    check("세션시간 패널 8개", html.count('class="set-bt-panel"') == 8)

    # 3. CSRF Origin 검사
    code, out = post("/inbox/add", {"text": "테스트 수집"})
    check("Origin 없는 요청은 통과(스크립트·curl)", code == 200, code)
    inbox_id = out.get("id") if isinstance(out, dict) else None
    code, out = post("/inbox/add", {"text": "같은 출처"}, {"Origin": f"http://{HOST}"})
    check("같은 출처 POST 통과", code == 200, code)
    same_id = out.get("id") if isinstance(out, dict) else None
    code, out = post("/inbox/add", {"text": "공격"}, {"Origin": "http://evil.example"})
    check("다른 출처 POST 403", code == 403, code)
    code, out = post("/settings/purge", {"start": "2020-01-01", "end": "2030-01-01"},
                     {"Referer": "http://evil.example/page"})
    check("다른 출처 Referer도 403(기간삭제 차단)", code == 403, code)
    n = db_query(db_path, "SELECT COUNT(*) AS c FROM inbox")[0]["c"]
    check("차단된 요청은 저장되지 않음", n == 2, n)
    for iid in (inbox_id, same_id):
        if iid:
            post(f"/inbox/delete/{iid}", {})

    # 4. 요일별 세션 시간 · 수요일(2)만 덮어쓰기
    wed = [("06:00", "08:00"), ("08:00", "10:00"), ("10:00", "11:00"),
           ("11:00", "13:00"), ("13:00", "15:00"), ("15:00", "17:30"),
           ("17:30", "19:30"), ("19:30", "21:30")]
    data = {"scope": "2"}
    for i, (s, e) in enumerate(wed):
        data[f"start_{i}"], data[f"end_{i}"] = s, e
    code, out = post("/settings/blocktimes", data)
    check("수요일 세션시간 저장", code == 200 and out.get("ok"), out)

    # 화면에 실제로 그려진 블록 시간만 본다(설정 JSON 등 다른 문자열에 걸리지 않게).
    def block_times(date_str):
        _c, h = get("/day/" + date_str)
        return re.findall(r'class="block-time mono">([^<]+)<', h)

    check("수요일 첫 블록이 06:00 – 08:00", block_times("2026-07-29")[:1] == ["06:00 – 08:00"],
          block_times("2026-07-29")[:1])
    check("목요일은 공통 07:30 – 09:30 유지", block_times("2026-07-30")[:1] == ["07:30 – 09:30"],
          block_times("2026-07-30")[:1])

    bad = {"scope": ""}
    base = [("07:30", "09:30"), ("09:30", "11:30"), ("11:30", "12:30"),
            ("12:30", "14:30"), ("14:30", "16:30"), ("16:30", "19:00"),
            ("19:00", "21:00"), ("21:00", "23:00")]
    for i, (s, e) in enumerate(base):
        bad[f"start_{i}"], bad[f"end_{i}"] = s, e
    bad["end_0"] = "09:15"
    code, out = post("/settings/blocktimes", bad)
    check("블록 길이가 30분 배수가 아니면 거부", code == 400, out)

    bad["end_0"] = "09:30"
    bad["start_1"] = "09:00"
    code, out = post("/settings/blocktimes", bad)
    check("앞 블록과 겹치면 거부", code == 400, out)

    code, out = post("/settings/blocktimes/reset", {"scope": "2"})
    check("수요일 되돌리기 후 공통 복귀",
          code == 200 and block_times("2026-07-29")[:1] == ["07:30 – 09:30"],
          block_times("2026-07-29")[:1])

    # 5. 하루 골격 · 같은 날을 두 번 열어도 블록·슬롯이 늘지 않는지(멱등)
    get("/day/2026-07-31")
    get("/day/2026-07-31")
    nb = db_query(db_path, "SELECT COUNT(*) AS c FROM blocks WHERE date='2026-07-31'")[0]["c"]
    ns = db_query(db_path, "SELECT COUNT(*) AS c FROM slots WHERE date='2026-07-31'")[0]["c"]
    check("골격 생성이 멱등(블록 8개)", nb == 8, nb)
    check("골격 생성이 멱등(슬롯 중복 없음)", ns == 31, ns)

    # 6. 필드 자동저장 왕복
    blk = db_query(db_path,
                   "SELECT id FROM blocks WHERE date='2026-07-31' ORDER BY block_order")[0]["id"]
    code, out = post("/save/field", {"entity": "block", "id": blk,
                                     "field": "plan_text", "value": "테스트 계획"})
    check("블록 PLAN 자동저장", code == 200 and out.get("ok"), out)
    v = db_query(db_path, "SELECT plan_text FROM blocks WHERE id=?", (blk,))[0]["plan_text"]
    check("저장값이 DB에 반영", v == "테스트 계획", v)
    code, out = post("/save/field", {"entity": "block", "id": blk,
                                     "field": "nope", "value": "x"})
    check("허용되지 않은 필드는 거부", code == 400, code)

    # 7. 블록 시간이 바뀌어도 장소만 적은 날의 입력이 사라지지 않는지(회귀)
    post("/save/field", {"entity": "block", "id": blk, "field": "bloc", "value": "카페"})
    data = {"scope": ""}
    shifted = [("07:00", "09:00"), ("09:00", "11:00"), ("11:00", "12:00"),
               ("12:00", "14:00"), ("14:00", "16:00"), ("16:00", "19:00"),
               ("19:00", "21:00"), ("21:00", "23:00")]
    for i, (s, e) in enumerate(shifted):
        data[f"start_{i}"], data[f"end_{i}"] = s, e
    post("/settings/blocktimes", data)
    get("/day/2026-07-31")
    rows = db_query(db_path,
                    "SELECT location FROM blocks WHERE date='2026-07-31' AND location IS NOT NULL")
    check("시간 변경 후에도 장소가 남아 있음", len(rows) == 1 and rows[0]["location"] == "카페",
          [dict(r) for r in rows])
    post("/settings/blocktimes/reset", {"scope": ""})

    # 8. 장기 계획 막대 · 추가 → 하위 추가 → 상위 자동 계산 → 삭제
    code, html = get("/plan")
    areas = re.findall(r'class="gt-add" data-area="(\d+)"', html)
    check("장기 화면에 영역별 추가 버튼", len(areas) >= 2, len(areas))
    check("장기 화면에 표(격자)가 없음", "plan-grid" not in html and "pg-input" not in html)
    code, out = post("/plan/item/add", {"area_id": areas[0], "title": "노무사 1차 합격",
                                        "start": "2026-08-01", "end": "2026-09-30"})
    check("계획 막대 항목 추가", code == 200 and out.get("ok"), out)
    parent = out.get("id")
    code, out = post("/plan/item/add", {"area_id": areas[0], "parent_id": parent,
                                        "title": "노동법 1회독",
                                        "start": "2026-07-20", "end": "2026-10-15"})
    check("하위 항목 추가", code == 200 and out.get("ok"), out)
    child = out.get("id")
    row = db_query(db_path, "SELECT start_date, end_date FROM lt_item WHERE id=?", (parent,))[0]
    check("상위 기간이 하위 최소~최대로 자동 확장",
          row["start_date"] == "2026-07-20" and row["end_date"] == "2026-10-15", dict(row))
    post("/plan/item/update", {"id": child, "progress": "60"})
    p = db_query(db_path, "SELECT progress FROM lt_item WHERE id=?", (parent,))[0]["progress"]
    check("상위 진척률이 하위 평균(60)", p == 60, p)
    code, out = post("/plan/item/update", {"id": child, "start": "2026-11-01",
                                           "end": "2026-10-01"})
    check("종료일이 시작일보다 빠르면 거부", code == 400, out)
    code, out = post("/plan/item/add", {"area_id": areas[0], "parent_id": "abc",
                                        "title": "x", "start": "2026-08-01",
                                        "end": "2026-08-02"})
    check("상위 항목 값이 잘못되면 거부", code == 400, code)

    code, html = get("/plan?level=month&anchor=2026-08-01")
    check("계획 막대가 그려짐", 'class="gt-bar' in html and "노동법 1회독" in html)
    # 상위 항목 한 줄 안에 상위(depth 0)와 하위(depth 1) 막대가 함께 겹쳐 그려진다
    row = re.search(r'<div class="gt-row gt-itemrow"[^>]*>.*?</div>\s*</div>', html, re.S)
    seg = row.group(0) if row else ""
    check("상위 막대 안에 하위 막대가 겹쳐 그려짐",
          'data-depth="0"' in seg and 'data-depth="1"' in seg, seg[:120])

    # 주간 탭 '이번 주 장기 항목' → 진척률 편집·주간 목표로 옮기기
    code, html = get("/week/2026-08-03")
    check("주간 탭에 이 주 장기 항목 노출",
          "노동법 1회독" in html and 'class="wk-lt-prog-input"' in html)
    code, out = post("/week/item-to-goal", {"week_start": "2026-08-03", "item_id": child})
    goal = db_query(db_path,
                    "SELECT weekly_goal FROM weekly_meta WHERE week_start='2026-08-03'")
    check("장기 항목을 주간 목표로 옮김",
          code == 200 and goal and goal[0]["weekly_goal"] == "노동법 1회독", out)
    code, out = post("/week/item-to-goal", {"week_start": "2026-08-03", "item_id": child})
    goal = db_query(db_path,
                    "SELECT weekly_goal FROM weekly_meta WHERE week_start='2026-08-03'")
    check("같은 항목을 두 번 옮겨도 중복되지 않음",
          goal[0]["weekly_goal"] == "노동법 1회독", goal[0]["weekly_goal"])
    code, out = post("/week/item-to-theme",
                     {"week_start": "2026-08-03", "item_id": child, "label": "B3"})
    th = db_query(db_path, "SELECT theme_text FROM weekly_block_themes "
                           "WHERE week_start='2026-08-03' AND block_label='B3'")
    check("장기 항목을 블록 이름으로 옮김",
          code == 200 and th and th[0]["theme_text"] == "노동법 1회독", out)
    code, out = post("/week/item-to-theme",
                     {"week_start": "2026-08-03", "item_id": child, "label": "없는블록"})
    check("없는 블록으로는 옮기지 않음", code == 400, code)

    code, out = post("/plan/item/delete", {"id": parent})
    n = db_query(db_path, "SELECT COUNT(*) AS c FROM lt_item")[0]["c"]
    check("항목 삭제 시 하위까지 함께 삭제", code == 200 and n == 0, n)

    # 9. 정리한 것들이 실제로 정리됐는지
    cols = {r["name"] for r in db_query(db_path, "PRAGMA table_info(categories)")}
    check("categories.color 컬럼 제거", "color" not in cols, sorted(cols))
    rcols = {r["name"] for r in db_query(db_path, "PRAGMA table_info(reflection)")}
    check("reflection.review_gcal_event_id 제거", "review_gcal_event_id" not in rcols)
    code, _ = get("/search?q=x")
    check("레거시 /search 제거(404)", code == 404, code)

    # 10. 화면에 설정 전체가 실리지 않는지(캘린더 ID·AI 주소 노출 방지)
    code, html = get("/today")
    m = re.search(r"window\.__settings = (\{.*?\});", html)
    keys = set(json.loads(m.group(1)).keys()) if m else set()
    check("화면에는 필요한 설정 3개만 실림",
          keys == {"pomo_auto", "pomo_warn5", "collapse_blocks"}, sorted(keys))

    # 11. .env 편집기는 값을 가려서 보여주고, 가린 채 저장해도 실제 값이 유지되는지
    #     (서버가 임시 폴더의 가짜 .env 를 보도록 되어 있어 실제 .env 는 건드리지 않는다)
    env_path = pathlib.Path(db_path).parent / ".env"
    original = env_path.read_text(encoding="utf-8")
    code, html = get("/settings")
    check(".env 값이 화면에서 가려짐", "********" in html and "sk-test-secret" not in html)
    masked = re.search(r'aria-label=".env 내용">(.*?)</textarea>', html, re.S)
    body = html_unescape(masked.group(1)) if masked else ""
    code, out = post("/settings/env/save", {"content": body})
    check("가린 채 저장해도 .env 원본 유지",
          code == 200 and env_path.read_text(encoding="utf-8") == original, code)
    code, out = post("/settings/env/save",
                     {"content": body.replace("AI_MODEL=********", "AI_MODEL=바뀐모델")})
    after = env_path.read_text(encoding="utf-8")
    check("가리지 않고 적은 값은 실제로 반영",
          "AI_MODEL=바뀐모델" in after and "sk-test-secret" in after, after[:80])

    # 12. 검색어의 LIKE 와일드카드가 글자 그대로 처리되는지
    rows = db_query(db_path,
                    "SELECT id FROM blocks WHERE date='2026-07-31' ORDER BY block_order")
    post("/save/field", {"entity": "block", "id": rows[1]["id"],
                         "field": "plan_text", "value": "달성 50% 목표"})
    post("/save/field", {"entity": "block", "id": rows[2]["id"],
                         "field": "plan_text", "value": "퍼센트 없는 계획"})
    code, html = get("/analytics?q=" + urllib.parse.quote("50%"))
    check("'50%' 검색이 그 기록을 찾음", "달성 50% 목표" in html)
    code, html = get("/analytics?q=" + urllib.parse.quote("%"))
    check("'%' 검색은 %가 든 기록만 찾음",
          "달성 50% 목표" in html and "퍼센트 없는 계획" not in html)

    # 13. 잘못된 날짜로 온 자동저장은 거부되는지
    code, out = post("/save/field", {"entity": "meta", "id": "nope", "field": "memo", "value": "x"})
    check("meta 저장에 잘못된 날짜 거부", code == 400, code)
    n = db_query(db_path, "SELECT COUNT(*) AS c FROM daily_meta WHERE date='nope'")[0]["c"]
    check("잘못된 날짜 행이 생기지 않음", n == 0, n)

    # 14. 감사·반성 3칸도 자동저장되는지(예전에는 저장 버튼을 눌러야 했다)
    post("/save/field", {"entity": "meta", "id": "2026-07-31", "field": "grat2",
                         "grat1": "가", "grat2": "나", "grat3": ""})
    row = db_query(db_path, "SELECT gratitude FROM daily_meta WHERE date='2026-07-31'")
    check("감사·반성 3칸 저장", row and row[0]["gratitude"] == "가\n나\n",
          row[0]["gratitude"] if row else None)

    # 15. 오늘 컨셉 3칸(빠른 수집함 자리)
    code, html = get("/day/2026-07-31")
    check("오늘 탭에 컨셉 3칸", html.count('name="concept') == 3, html.count('name="concept'))
    post("/save/field", {"entity": "meta", "id": "2026-07-31", "field": "concept1",
                         "concept1": "몰입", "concept2": "", "concept3": "정리"})
    row = db_query(db_path, "SELECT concept FROM daily_meta WHERE date='2026-07-31'")
    check("컨셉 3칸 저장", row and row[0]["concept"] == "몰입\n\n정리",
          row[0]["concept"] if row else None)

    # 16. 수집함 표시 설정(기본 끔 → 켜면 오늘·주간 모두 다시 보인다)
    code, html = get("/day/2026-07-31")
    code, wk = get("/week/2026-07-27")
    check("기본은 수집함 숨김",
          'id="inbox-input"' not in html and 'id="wk-inbox-input"' not in wk)
    post("/settings/save", {"show_inbox": "1"})
    code, html = get("/day/2026-07-31")
    code, wk = get("/week/2026-07-27")
    check("설정을 켜면 수집함이 다시 보임",
          'id="inbox-input"' in html and 'id="wk-inbox-input"' in wk)
    post("/settings/save", {"show_inbox": "0"})

    # 17. 요일 컨셉(설정 7칸 → 오늘 탭 날짜 옆 괄호)
    code, out = post("/settings/weekday-concepts",
                     {f"wd{i}": ("금요일컨셉" if i == 4 else "") for i in range(7)})
    check("요일 컨셉 저장", code == 200 and out.get("ok"), out)
    code, html = get("/day/2026-07-31")          # 2026-07-31은 금요일
    check("오늘 탭 날짜 옆에 그 요일 컨셉",
          '<span class="hero-wdc">(금요일컨셉)</span>' in html)
    code, html = get("/day/2026-07-30")          # 목요일은 비어 있어 괄호도 없음
    check("컨셉이 비면 괄호도 없음", "hero-wdc" not in html)


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="6block-test-"))
    env = dict(os.environ, SIXBLOCK_TEST_DIR=str(tmp), SIXBLOCK_TEST_PORT=str(PORT))
    log = open(tmp / "server.log", "w")
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "smoke_server.py")],
                            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_up(proc):
            log.close()
            print("테스트 서버를 띄우지 못했습니다. 로그 ↓")
            print((tmp / "server.log").read_text(encoding="utf-8")[-3000:])
            return 1
        print(f"임시 서버 {BASE} · 임시 DB {tmp}\n")
        run_checks(tmp / "blocks.db")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(passed) + len(failed)
    print(f"\n{total}개 중 통과 {len(passed)} · 실패 {len(failed)}")
    if failed:
        print("실패 · " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
