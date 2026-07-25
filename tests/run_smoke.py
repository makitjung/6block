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
    for path in ("/today", "/week", "/plan", "/plan?view=gantt", "/settings",
                 "/analytics", "/data", "/reflect"):
        code, _ = get(path)
        check(f"GET {path} 200", code == 200, code)

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

    # 8. 장기 간트 · 추가 → 하위 추가 → 상위 자동 계산 → 삭제
    code, html = get("/plan?view=gantt")
    areas = re.findall(r'class="gt-add" data-area="(\d+)"', html)
    check("간트 화면에 영역별 추가 버튼", len(areas) >= 2, len(areas))
    code, out = post("/plan/item/add", {"area_id": areas[0], "title": "노무사 1차 합격",
                                        "start": "2026-08-01", "end": "2026-09-30"})
    check("간트 항목 추가", code == 200 and out.get("ok"), out)
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

    code, html = get("/plan?level=month&anchor=2026-08-01&view=gantt")
    check("간트 막대가 그려짐", 'class="gt-bar' in html and "노동법 1회독" in html)
    code, html = get("/week/2026-08-03")
    check("주간 탭 맥락에 이 주 간트 항목 노출", "노동법 1회독" in html)

    code, out = post("/plan/item/delete", {"id": parent})
    n = db_query(db_path, "SELECT COUNT(*) AS c FROM lt_item")[0]["c"]
    check("항목 삭제 시 하위까지 함께 삭제", code == 200 and n == 0, n)


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
