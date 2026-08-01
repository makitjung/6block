# 6block 전체 스모크 테스트. 임시 서버를 직접 띄우고 실제 HTTP 요청으로 확인한 뒤 정리한다.
# 실행 · .venv/bin/python tests/run_smoke.py   (외부 라이브러리 없이 표준 라이브러리만 사용)
import datetime
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


def get_binary(path):
    """이미지처럼 utf-8 이 아닌 응답용. (상태코드, content-type, 바이트) 를 준다."""
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.getcode(), r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


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

    # 6-2. 오늘 탭 블록·슬롯을 그 주 할 일에 잇기(연결 키만 저장, 글은 직접 입력)
    slt = db_query(db_path,
                   "SELECT id FROM slots WHERE date='2026-07-31' ORDER BY slot_index")[0]["id"]
    post("/save/field", {"entity": "block", "id": blk, "field": "wk_todo", "value": "wk:1"})
    post("/save/field", {"entity": "slot", "id": slt, "field": "wk_todo", "value": "lt:9"})
    bw = db_query(db_path, "SELECT wk_todo FROM blocks WHERE id=?", (blk,))[0]["wk_todo"]
    sw = db_query(db_path, "SELECT wk_todo FROM slots WHERE id=?", (slt,))[0]["wk_todo"]
    check("블록·슬롯의 주간 할 일 연결 저장", bw == "wk:1" and sw == "lt:9", (bw, sw))
    post("/save/field", {"entity": "slot", "id": slt, "field": "wk_todo", "value": ""})
    sw = db_query(db_path, "SELECT wk_todo FROM slots WHERE id=?", (slt,))[0]["wk_todo"]
    check("연결 안 함을 고르면 비워짐", sw is None, sw)
    # 블록은 두 시간짜리라 여러 계획을 쉼표로 잇는다(슬롯·목표는 하나만).
    post("/save/field", {"entity": "block", "id": blk, "field": "wk_todo",
                         "value": "lt:9,wk:2"})
    bw = db_query(db_path, "SELECT wk_todo FROM blocks WHERE id=?", (blk,))[0]["wk_todo"]
    check("블록은 여러 계획을 함께 이음", bw == "lt:9,wk:2", bw)
    # 목표 3줄의 연결은 daily_meta.goal_links 에 줄바꿈 3칸으로 합쳐 저장된다.
    post("/save/field", {"entity": "meta", "id": "2026-07-31", "field": "goallink2",
                         "value": "lt:9", "goallink1": "", "goallink2": "lt:9",
                         "goallink3": ""})
    gl = db_query(db_path,
                  "SELECT goal_links FROM daily_meta WHERE date='2026-07-31'")
    check("목표 줄의 계획 연결 저장",
          gl and gl[0]["goal_links"] == "\nlt:9\n", gl and gl[0]["goal_links"])
    # 연결 버튼은 그 주 목표 열에 이을 것이 하나라도 있어야 나온다(자유 란 하나로 확인).
    post("/save/field", {"entity": "wmeta", "id": "2026-07-27", "field": "wgoal1",
                         "value": "이번 주 자유 목표", "wgoal1": "이번 주 자유 목표",
                         "wgoal2": "", "wgoal3": ""})
    code, html = get("/day/2026-07-31")
    check("블록·DO·목표 앞에 연결 버튼이 붙음",
          html.count('class="wl-btn"') >= 3 and 'name="goallink1"' in html
          and 'data-multi="1"' in html, html.count('class="wl-btn"'))

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
    # 간트 왼쪽 줄은 코어블록 B1~B6 + 미지정. 영역은 줄이 아니라 막대 색으로만 쓰인다.
    blocks = re.findall(r'class="gt-add" data-block="(\w*)"', html)
    check("간트 왼쪽 줄이 B1~B6 + 미지정",
          blocks == ["B1", "B2", "B3", "B4", "B5", "B6", ""], blocks)
    opts = re.search(r'<select class="gt-f-area"[^>]*>(.*?)</select>', html, re.S)
    areas = re.findall(r'value="(\d+)"', opts.group(1) if opts else "")
    check("항목 추가 폼에 영역 선택칸", len(areas) >= 2, len(areas))
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
    # 상위는 하위를 품도록 넓어지기만 하고, 직접 정한 기간(8/1~9/30)은 줄어들지 않는다.
    check("상위 기간이 하위를 품도록 넓어짐",
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
    # 열은 연 5칸·나머지 4칸이고, 보고 있는 기간이 두 번째 칸에 온다.
    def cols(lv, anchor):
        """열 머리글 이름만 뽑는다(앞뒤 기간 이동 화살표·주간 링크는 뺀다)."""
        _c, h = get(f"/plan?level={lv}&anchor={anchor}")
        out = []
        for blk in re.findall(r'class="gt-col-label">(.*?)</span>', h, re.S):
            words = [w for w in re.sub(r"<[^>]+>", " ", blk).split()
                     if w not in ("‹", "›", "↗")]
            out.append(words[0] if words else "")
        return h, out
    _h, ys = cols("year", "2026-07-27")
    check("연은 5칸이고 그 해가 두 번째", ys == ["2025", "2026", "2027", "2028", "2029"], ys)
    _h, ms = cols("month", "2026-11-15")
    check("월은 4칸이고 그 달이 두 번째", ms == ["10월", "11월", "12월", "1월"], ms)
    _h, qs = cols("quarter", "2026-11-15")
    check("분기는 4칸이고 그 분기가 두 번째",
          qs == ["3분기", "4분기", "1분기", "2분기"], qs)
    # 다음/이전은 그 단위 하나만큼만 옮긴다.
    _c, h = get("/plan?level=month&anchor=2026-11-15")
    check("월 이동은 한 달씩",
          "/plan?level=month&anchor=2026-10-15" in h
          and "/plan?level=month&anchor=2026-12-15" in h)
    _c, h = get("/plan?level=week&anchor=2026-11-15")
    check("주 이동은 한 주씩",
          "/plan?level=week&anchor=2026-11-08" in h
          and "/plan?level=week&anchor=2026-11-22" in h)
    # 해·분기·달이 바뀌는 자리에 표시가 붙는다.
    _c, h = get("/plan?level=month&anchor=2026-11-15")
    check("해가 바뀌는 열에 표시",
          'class="gt-brk">2027년' in h and 'data-lv="year"' in h)
    _c, h = get("/plan?level=week&anchor=2026-07-27")
    check("달이 바뀌는 열에 표시",
          'class="gt-brk">8월' in h and 'data-lv="month"' in h)
    # 그 해의 몇 번째 주인지(ISO). 2026-07-27 주는 31주차.
    check("장기 주 열에 몇 주차 표시",
          "~8/2 · 31주" in h and "~8/9 · 32주" in h, h.count("주</span>"))
    _c, h = get("/week/2026-07-27")
    # 주간 제목은 '31주차 7.27~8.2' 한 줄(요일 표기 줄은 없앴다)
    check("주간 제목은 주차 + 기간 한 줄",
          '<span class="hero-wk">31주차</span> 7.27~8.2' in h
          and "월요일 주" not in h, h.count("hero-date-main"))
    check("주간 통계는 접혀서 나간다",
          'id="wk-stats" hidden' in h and 'id="wk-stats-toggle"' in h)
    check("주간 리뷰는 없앴다",
          "주간 리뷰" not in h and 'class="review-grid' not in h)
    # 막대 진하기는 기간이 아니라 계층 단계로 갈린다(최상위 0 → 하위로 갈수록 커진다)
    code, html = get("/plan?level=year&anchor=2026-01-01")
    check("기간 구분(장기·중기·단기)은 색에 쓰지 않음",
          'data-span=' not in html and "초단기" not in html)
    levels = dict(re.findall(r'class="gt-e-lv" data-level="(\d)"[^>]*>([^<]+)<', html))
    check("계층 단계로 진하기를 나눔(3단계)",
          levels.get("0", "").strip() == "최상위" and levels.get("1", "").strip() == "하위"
          and "3" not in levels, levels)
    # 같은 영역에서 뿌리가 여럿이면 색조를 조금씩 돌려 서로 구분한다
    _c, o = post("/plan/item/add", {"area_id": areas[0], "title": "같은영역 두번째",
                                    "start": "2026-08-01", "end": "2026-09-30"})
    second = o.get("id")
    _c, h = get("/plan?level=year&anchor=2026-01-01")
    hues = set(re.findall(r'--gt-hue: (-?\d+)deg', h))
    check("같은 영역의 다른 뿌리는 색조가 다름", len(hues) >= 2, sorted(hues))
    post("/plan/item/delete", {"id": second})
    def block_seg(page, key):
        """그 블록 줄의 막대 부분만 잘라낸다(추가폼·편집칸은 뺀다).

        편집칸의 '상위 항목' 목록에 모든 항목 이름이 들어 있어 그것까지 세면 안 된다.
        """
        for s in re.split(r'(?=<div class="gt-row gt-blockrow)', page):
            if f'data-block="{key}"' in s.split(">", 1)[0] + ">":
                return s.split('<div class="gt-form"')[0]
        return ""
    # 같은 블록 줄에서 기간이 겹치는 막대는 위아래 칸(lane)으로 갈라 서로 가리지 않는다.
    # 아직 블록을 안 준 항목이라 미지정(data-block="") 줄에 함께 들어 있다.
    seg = block_seg(html, "")
    lanes = re.findall(r'data-lane="(\d+)"', seg)
    check("겹치는 막대는 서로 다른 칸에 담긴다",
          len(lanes) >= 2 and len(set(lanes)) == len(lanes), lanes)
    check("블록 줄은 적어도 두 칸을 둔다",
          re.search(r'gt-blockrow[^>]*--lanes: (\d+)', seg).group(1) >= "2", seg[:120])

    # 8-1. 블록(B1~B6) 배정과 영역 색
    code, out = post("/plan/item/update", {"id": parent, "block": "B3"})
    check("항목을 B3 블록으로 옮김", code == 200 and out.get("ok"), out)
    v = db_query(db_path, "SELECT block_label FROM lt_item WHERE id=?", (parent,))[0]
    check("블록이 저장됨", v["block_label"] == "B3", dict(v))
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    check("상위를 블록에 놓으면 하위도 그 줄에 같이 온다",
          "노무사 1차 합격" in block_seg(h, "B3")
          and "노동법 1회독" in block_seg(h, "B3")
          and "노동법 1회독" not in block_seg(h, ""), block_seg(h, "B3")[:160])
    code, out = post("/plan/item/update", {"id": child, "block": "B5"})
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    check("하위에 블록을 따로 주면 그 줄로 빠짐",
          "노동법 1회독" in block_seg(h, "B5")
          and "노동법 1회독" not in block_seg(h, "B3"))
    # 한 항목을 여러 블록에서 동시에 진행하면 같은 막대가 그 줄마다 나온다
    code, out = post("/plan/item/update", {"id": child, "block": "B5,B2"})
    v = db_query(db_path, "SELECT block_label FROM lt_item WHERE id=?", (child,))[0]
    check("블록 여러 개가 B1~B6 순으로 저장됨", v["block_label"] == "B2,B5", dict(v))
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    check("같은 막대가 고른 블록 줄마다 나옴",
          "노동법 1회독" in block_seg(h, "B2")
          and "노동법 1회독" in block_seg(h, "B5")
          and "노동법 1회독" not in block_seg(h, "B3"))
    code, out = post("/plan/item/update", {"id": child, "block": "B5,없는블록,B5"})
    v = db_query(db_path, "SELECT block_label FROM lt_item WHERE id=?", (child,))[0]
    check("모르는 값·중복은 버린다", v["block_label"] == "B5", dict(v))
    # 여러 블록에 걸린 항목은 어느 줄에서 고쳐도 한 곳에 저장돼 모든 줄에 함께 반영된다
    post("/plan/item/update", {"id": child, "block": "B2,B5"})
    post("/plan/item/update", {"id": child, "title": "노동법 2회독"})
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    check("한 줄에서 고치면 다른 블록 줄에도 함께 반영",
          "노동법 2회독" in block_seg(h, "B2") and "노동법 2회독" in block_seg(h, "B5")
          and "노동법 1회독" not in h)
    post("/plan/item/update", {"id": child, "title": "노동법 1회독"})
    code, out = post("/plan/item/update", {"id": child, "block": ""})
    v = db_query(db_path, "SELECT block_label FROM lt_item WHERE id=?", (child,))[0]
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    check("블록을 하나도 안 고르면 미지정 줄로 간다",
          v["block_label"] is None and "노동법 1회독" in block_seg(h, ""), dict(v))
    post("/plan/item/update", {"id": child, "block": "B5"})
    code, out = post("/plan/item/update", {"id": child, "block": "없는블록"})
    v = db_query(db_path, "SELECT block_label FROM lt_item WHERE id=?", (child,))[0]
    check("모르는 블록 값은 미지정으로", v["block_label"] is None, dict(v))
    # 항목 숨기기. 기본 화면에서는 빠지고 '숨긴 항목 보기'로만 다시 나온다.
    code, out = post("/plan/item/update", {"id": child, "hidden": "1"})
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    _c, h2 = get("/plan?level=month&anchor=2026-08-01&show_hidden=1")
    # 막대만 세도록 좁힌다(영역 관리 버튼도 data-id 를 쓴다)
    bar_of = f'data-id="{child}" data-parent='
    check("숨긴 항목은 기본 화면에서 빠진다",
          bar_of not in h and 'id="pg-show-hidden"' in h)
    check("숨긴 항목 보기를 켜면 다시 나온다",
          bar_of in h2 and "is-hidden-item" in h2)
    post("/plan/item/update", {"id": child, "hidden": "0"})
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    check("숨김을 풀면 기본 화면에 돌아온다", bar_of in h)
    # 오늘 세로선은 기본으로 안 그린다(체크박스로만 켠다)
    check("오늘 선은 기본으로 꺼져 있다",
          'id="pg-today-line"' in h and 'class="gantt"' in h)
    # 상위를 어느 블록에 놓으면 하위 사슬도 같은 블록으로 따라간다
    code, out = post("/plan/item/update", {"id": parent, "block": "B4,B6"})
    v = db_query(db_path, "SELECT block_label FROM lt_item WHERE id=?", (child,))[0]
    check("상위를 블록에 놓으면 하위도 같이 배치됨", v["block_label"] == "B4,B6", dict(v))
    # 그 뒤 하위만 따로 빼 두면 상위의 다른 수정에는 안 딸려간다
    post("/plan/item/update", {"id": child, "block": "B5"})
    post("/plan/item/update", {"id": parent, "title": "노무사 1차 합격"})
    v = db_query(db_path, "SELECT block_label FROM lt_item WHERE id=?", (child,))[0]
    check("따로 뺀 하위는 상위의 다른 수정에 안 딸려감", v["block_label"] == "B5", dict(v))
    # 상위가 하위보다 위 칸에 온다(상위 lane < 하위 lane)
    post("/plan/item/update", {"id": parent, "block": "B3"})
    post("/plan/item/update", {"id": child, "block": "B3"})
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    seg = block_seg(h, "B3")
    lane_of = dict((m[1], m[0]) for m in
                   re.findall(r'data-lane="(\d+)"[^>]*data-id="(\d+)"', seg))
    check("상위가 하위보다 위 칸에 놓인다",
          lane_of.get(str(parent)) < lane_of.get(str(child)), lane_of)
    # 한 줄 안에서는 영역 표시 순서대로 위에서 아래로 놓인다
    _c, o = post("/plan/item/add", {"area_id": areas[3], "block": "B3", "title": "뒤 영역",
                                    "start": "2026-08-05", "end": "2026-08-20"})
    late = o.get("id")
    _c, o = post("/plan/item/add", {"area_id": areas[1], "block": "B3", "title": "앞 영역",
                                    "start": "2026-09-01", "end": "2026-09-20"})
    early = o.get("id")
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    lane2 = dict((m[1], int(m[0])) for m in
                 re.findall(r'data-lane="(\d+)"[^>]*data-id="(\d+)"', block_seg(h, "B3")))
    check("영역 순서가 빠른 항목이 위 칸에 놓인다",
          lane2.get(str(early), 9) < lane2.get(str(late), 9), lane2)
    post("/plan/item/delete", {"id": late})
    post("/plan/item/delete", {"id": early})
    post("/plan/item/update", {"id": child, "block": ""})
    # 막대 색 = 영역 톤, 진하기 = 기간 구분(--gt-tone 과 data-span 이 함께 실린다)
    check("막대에 영역 색이 실림", "--gt-tone: var(--tone-" in h)
    code, out = post("/plan/area/update", {"id": areas[0], "tone": "purple"})
    check("영역 색 변경", code == 200 and out.get("ok"), out)
    _c, h = get("/plan?level=month&anchor=2026-08-01")
    check("바꾼 색이 막대에 반영", "--gt-tone: var(--tone-purple)" in h)
    code, out = post("/plan/area/update", {"id": areas[0], "tone": "무지개"})
    check("모르는 색은 거부", code == 400, out)
    post("/plan/item/update", {"id": parent, "block": ""})

    # 주간 '목표' 열 = 장기 최하위 항목 + 자유 란 3개(카드 하나로 합쳐져 있다)
    code, html = get("/week/2026-08-03")
    check("주간 목표 열에 최하위 항목만 란이 생김",
          f'name="ltgoal_{child}"' in html and f'name="ltgoal_{parent}"' not in html)
    check("최하위 항목에 상위 이름이 함께 보임",
          '<span class="wg-up">노무사 1차 합격 ›</span>' in html)
    check("진척률·블록 보내기가 목표 열 안에 있음",
          'class="wk-lt-prog-input"' in html and 'wk-lt-theme' in html
          and 'class="card wk-lt"' not in html)
    ltg = f"SELECT goal_text FROM weekly_lt_goal WHERE week_start='2026-08-03' AND item_id={child}"
    post("/save/field", {"entity": "ltgoal", "id": child, "field": "ltgoal",
                         "value": "직접 적은 계획", "week_start": "2026-08-03"})
    goal = db_query(db_path, ltg)
    check("장기 항목의 이번 주 계획 저장",
          goal and goal[0]["goal_text"] == "직접 적은 계획", goal)
    # 목표 열 자유 란 3개는 weekly_meta.weekly_goal 에 줄바꿈으로 합쳐 저장된다.
    post("/save/field", {"entity": "wmeta", "id": "2026-08-03", "field": "wgoal2",
                         "value": "자유2", "wgoal1": "자유1", "wgoal2": "자유2",
                         "wgoal3": ""})
    wg = db_query(db_path,
                  "SELECT weekly_goal FROM weekly_meta WHERE week_start='2026-08-03'")
    check("목표 열 자유 란 3개가 한 칸에 합쳐 저장됨",
          wg and wg[0]["weekly_goal"] == "자유1\n자유2\n", wg and wg[0]["weekly_goal"])
    code, out = post("/week/item-to-theme",
                     {"week_start": "2026-08-03", "item_id": child, "label": "B3"})
    th = db_query(db_path, "SELECT theme_text FROM weekly_block_themes "
                           "WHERE week_start='2026-08-03' AND block_label='B3'")
    check("장기 항목을 블록 이름으로 옮김",
          code == 200 and th and th[0]["theme_text"] == "노동법 1회독", out)
    code, out = post("/week/item-to-theme",
                     {"week_start": "2026-08-03", "item_id": child, "label": "없는블록"})
    check("없는 블록으로는 옮기지 않음", code == 400, code)

    # 막대 끌어 옮기기 · 끈 만큼(일 단위) 기간 이동 / 다른 막대의 하위로 / 영역으로 빼기
    code, out = post("/plan/item/shift", {"id": child, "days": "31"})
    row = db_query(db_path,
                   "SELECT start_date, end_date FROM lt_item WHERE id=?", (child,))[0]
    check("막대를 끈 만큼 옮김(+31일, 길이 유지)",
          row["start_date"] == "2026-08-20" and row["end_date"] == "2026-11-15", dict(row))
    code, out = post("/plan/item/shift", {"id": child, "days": "3"})
    row = db_query(db_path,
                   "SELECT start_date, end_date FROM lt_item WHERE id=?", (child,))[0]
    check("한 달보다 짧게도 옮겨진다(+3일)",
          row["start_date"] == "2026-08-23" and row["end_date"] == "2026-11-18", dict(row))
    post("/plan/item/shift", {"id": child, "days": "-3"})
    # 하위가 있는 상위를 끌면 하위 사슬도 같은 날수만큼 함께 밀린다
    before = db_query(db_path,
                      "SELECT start_date FROM lt_item WHERE id=?", (parent,))[0]["start_date"]
    code, out = post("/plan/item/shift", {"id": parent, "days": "7"})
    p = db_query(db_path, "SELECT start_date FROM lt_item WHERE id=?", (parent,))[0]
    c = db_query(db_path, "SELECT start_date FROM lt_item WHERE id=?", (child,))[0]
    check("상위를 끌면 하위까지 통짜로 옮겨짐",
          code == 200 and out.get("with_children") == 1
          and p["start_date"] == "2026-07-27" and c["start_date"] == "2026-08-27",
          {"before": before, "parent": p["start_date"], "child": c["start_date"]})
    post("/plan/item/shift", {"id": parent, "days": "-7"})
    # 상위 기간을 고치면 하위 기간도 그만큼 따라 움직인다(시작·종료 각각)
    ps = db_query(db_path,
                  "SELECT start_date, end_date FROM lt_item WHERE id=?", (parent,))[0]
    cs = db_query(db_path,
                  "SELECT start_date, end_date FROM lt_item WHERE id=?", (child,))[0]
    post("/plan/item/update", {"id": parent, "start": "2026-07-25", "end": ps["end_date"]})
    c2 = db_query(db_path,
                  "SELECT start_date, end_date FROM lt_item WHERE id=?", (child,))[0]
    moved = (datetime.date.fromisoformat("2026-07-25")
             - datetime.date.fromisoformat(ps["start_date"])).days
    want = (datetime.date.fromisoformat(cs["start_date"])
            + datetime.timedelta(days=moved)).isoformat()
    check("상위 시작을 옮기면 하위 시작도 같이 움직임",
          c2["start_date"] == want and c2["end_date"] == cs["end_date"],
          {"moved": moved, "child": dict(c2), "want": want})
    code, out = post("/plan/item/resize", {"id": parent, "edge": "end", "days": "14"})
    c3 = db_query(db_path,
                  "SELECT start_date, end_date FROM lt_item WHERE id=?", (child,))[0]
    want_e = (datetime.date.fromisoformat(c2["end_date"])
              + datetime.timedelta(days=14)).isoformat()
    check("상위 종료를 늘리면 하위 종료도 같이 늘어남",
          c3["end_date"] == want_e and c3["start_date"] == c2["start_date"],
          {"child": dict(c3), "want": want_e})
    # 끌어서 보이는 기간 밖으로 보내도 사라지지 않는다. focus 를 주면 그 항목이 보이는
    # 자리로 화면이 옮겨 가고, 지난 항목이 됐으면 접힘도 풀린다.
    _c, h = get(f"/plan?level=month&anchor=2026-08-01&focus={child}")
    check("옮긴 항목이 화면 밖이면 그 자리로 따라간다",
          f'data-id="{child}"' in h and "is-focus" in h)
    _c, o = post("/plan/item/add", {"area_id": areas[0], "title": "지나간 막대",
                                    "start": "2020-01-01", "end": "2020-01-31"})
    gone = o.get("id")
    _c, h = get(f"/plan?level=month&anchor=2026-08-01&focus={gone}")
    check("지난 항목은 기본으로 보이고 체크박스로만 숨긴다",
          "지나간 막대" in h and 'id="pg-past-hide"' in h and "show-past" not in h)
    post("/plan/item/delete", {"id": gone})

    code, out = post("/plan/item/add", {"area_id": areas[1], "title": "체력 만들기",
                                        "start": "2026-08-01", "end": "2026-12-31"})
    other = out.get("id")
    p0 = db_query(db_path,
                  "SELECT start_date FROM lt_item WHERE id=?", (parent,))[0]["start_date"]
    code, out = post("/plan/item/reparent", {"id": other, "parent_id": parent})
    r = db_query(db_path,
                 "SELECT parent_id, area_id FROM lt_item WHERE id=?", (other,))[0]
    check("다른 막대 위에 놓으면 그 막대의 하위가 됨",
          code == 200 and r["parent_id"] == parent and r["area_id"] == int(areas[0]), dict(r))
    p = db_query(db_path,
                 "SELECT start_date, end_date FROM lt_item WHERE id=?", (parent,))[0]
    # 새 하위가 상위보다 늦게 끝나면 상위 종료가 그만큼 늘어난다(시작은 이미 더 이르다)
    check("하위가 늘면 상위 막대가 그만큼 넓어짐",
          p["start_date"] == p0 and p["end_date"] == "2026-12-31", dict(p))
    code, out = post("/plan/item/reparent", {"id": parent, "parent_id": other})
    check("자기 하위로는 넣지 못함", code == 400, out)

    code, out = post("/plan/item/add", {"area_id": areas[0], "parent_id": other,
                                        "title": "주 3회 달리기",
                                        "start": "2026-09-01", "end": "2026-09-30"})
    gchild = out.get("id")
    code, out = post("/plan/item/reparent", {"id": other, "area_id": areas[1]})
    r = db_query(db_path,
                 "SELECT parent_id, area_id FROM lt_item WHERE id=?", (other,))[0]
    g = db_query(db_path, "SELECT area_id FROM lt_item WHERE id=?", (gchild,))[0]
    check("영역을 바꾸면 최상위로 빠짐",
          code == 200 and r["parent_id"] is None and r["area_id"] == int(areas[1]), dict(r))
    check("하위 사슬도 같은 영역으로 따라옴", g["area_id"] == int(areas[1]), dict(g))
    code, out = post("/plan/item/reparent", {"id": other, "area_id": "99999"})
    check("없는 영역으로는 옮기지 않음", code == 404, code)

    # 기간 조절(한쪽 끝만) · 하위가 있는 상위 기간 직접 수정
    code, out = post("/plan/item/resize", {"id": other, "edge": "end", "days": "31"})
    r = db_query(db_path,
                 "SELECT start_date, end_date FROM lt_item WHERE id=?", (other,))[0]
    check("한쪽 끝만 늘려 기간을 바꿈",
          r["start_date"] == "2026-08-01" and r["end_date"] == "2027-01-31", dict(r))
    code, out = post("/plan/item/resize", {"id": other, "edge": "start", "days": "400"})
    check("기간이 뒤집히면 거부", code == 400, out)
    # 적어 넣은 기간은 하위가 있어도 그대로 남는다(예전에는 하위를 품도록 되돌려 놔서
    # 하위가 있는 항목은 날짜를 고쳐도 안 바뀌는 것처럼 보였다).
    code, out = post("/plan/item/update", {"id": parent, "title": "노무사 1차 합격",
                                           "start": "2026-11-01", "end": "2026-11-30"})
    p = db_query(db_path,
                 "SELECT start_date, end_date FROM lt_item WHERE id=?", (parent,))[0]
    check("하위가 있어도 적어 넣은 기간이 그대로 저장됨",
          p["start_date"] == "2026-11-01" and p["end_date"] == "2026-11-30", dict(p))
    code, out = post("/plan/item/resize", {"id": parent, "edge": "start", "days": "-10"})
    p = db_query(db_path,
                 "SELECT start_date FROM lt_item WHERE id=?", (parent,))[0]
    check("하위가 있어도 끈 대로 기간이 줄고 늘어남",
          p["start_date"] == "2026-10-22", dict(p))
    post("/plan/item/delete", {"id": other})

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
    check("화면에는 필요한 설정만 실림(캘린더 ID·AI 주소 제외)",
          keys == {"pomo_auto", "pomo_end_alarm", "collapse_blocks",
                   "pomo_start_sound", "pomo_start_sec",
                   "pomo_end_sound", "pomo_end_sec"}, sorted(keys))

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

    # 15. 오늘 컨셉 3칸(목표·달성·감사반성 세 열 바로 위, 같은 격자)
    code, html = get("/day/2026-07-31")
    check("오늘 탭에 컨셉 3칸", html.count('name="concept') == 3, html.count('name="concept'))
    check("컨셉은 목표 3열 바로 위",
          0 < html.find('class="concept-row"') < html.find('class="goal-plan"'))
    check("컨셉은 날짜 줄에서 빠졌다", "hero-concept" not in html)
    # 주간 띠: Things3 할 일은 장기 목록 아래에 온다(띠가 그려질 때만 본다)
    check("주간 띠는 장기 목록 다음에 할 일",
          'class="lt-strip"' not in html
          or html.find('class="lt-strip-cols"') < html.find('id="wk-tasks"'))
    post("/save/field", {"entity": "meta", "id": "2026-07-31", "field": "concept1",
                         "concept1": "몰입", "concept2": "", "concept3": "정리"})
    row = db_query(db_path, "SELECT concept FROM daily_meta WHERE date='2026-07-31'")
    check("컨셉 3칸 저장", row and row[0]["concept"] == "몰입\n\n정리",
          row[0]["concept"] if row else None)

    # 16. 수집함 표시 설정(기본 끔 → 켜면 오늘 탭에 다시 보인다.
    #     주간 탭 수집함은 주간 리뷰와 함께 없앴다)
    code, html = get("/day/2026-07-31")
    check("기본은 수집함 숨김", 'id="inbox-input"' not in html)
    post("/settings/save", {"show_inbox": "1"})
    code, html = get("/day/2026-07-31")
    check("설정을 켜면 수집함이 다시 보임", 'id="inbox-input"' in html)

    # 16-2. 슬롯 '고민'·'▶' 버튼은 기본으로 감춘다(설정에서 각각 켤 수 있다)
    code, html = get("/day/2026-07-31")
    check("기본은 슬롯 고민·▶ 버튼 숨김",
          'class="slot-reflect"' not in html and 'class="slot-play"' not in html)
    post("/settings/save", {"show_reflect": "1", "show_slot_play": "1"})
    code, html = get("/day/2026-07-31")
    check("설정을 켜면 슬롯 고민·▶ 버튼이 보임",
          'class="slot-reflect"' in html and 'class="slot-play"' in html)
    post("/settings/save", {"show_reflect": "0", "show_slot_play": "0"})
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

    # 22. 아이콘: 아이폰 홈화면은 뿌리의 PNG 를 찾는다(SVG 는 무시한다)
    for path, ctype in (("/favicon.ico", "image/x-icon"),
                        ("/apple-touch-icon.png", "image/png"),
                        ("/static/icon.png", "image/png")):
        code, got, blob = get_binary(path)      # 이미지라 utf-8 로 읽으면 깨진다
        check(f"{path} 응답 {ctype}", code == 200 and got.startswith(ctype),
              f"{code} {got}")
        check(f"{path} 내용 있음", len(blob) > 500, len(blob))
    code, html = get("/today")
    check("apple-touch-icon 은 PNG", 'rel="apple-touch-icon"' in html
          and 'apple-touch-icon.png' in html)

    # 23. 정적 파일 캐시: ?v= 가 붙으면 오래 캐시, 없으면 재검증
    req = urllib.request.Request(BASE + "/static/app.js?v=1")
    with urllib.request.urlopen(req, timeout=10) as r:
        cc_ver = r.headers.get("Cache-Control", "")
    req = urllib.request.Request(BASE + "/static/app.js")
    with urllib.request.urlopen(req, timeout=10) as r:
        cc_raw = r.headers.get("Cache-Control", "")
    check("?v= 붙은 정적 파일은 immutable", "immutable" in cc_ver, cc_ver)
    check("?v= 없는 정적 파일은 no-cache", cc_raw == "no-cache", cc_raw)
    req = urllib.request.Request(BASE + "/today")
    with urllib.request.urlopen(req, timeout=10) as r:
        check("HTML no-cache", r.headers.get("Cache-Control") == "no-cache",
              r.headers.get("Cache-Control"))

    # 24. 상태판: 연동·백업·기록·오류를 한 주소에서 준다
    code, raw = get("/api/health")
    st = json.loads(raw)
    check("/api/health 200", code == 200, code)
    for key in ("gcal", "gcal_write", "events", "achieve", "things", "ai",
                "backup", "records", "errors", "version"):
        check(f"상태판에 {key} 포함", key in st, sorted(st))
    check("오류가 없으면 옛 트레이스백을 끌어오지 않는다",
          st["errors"]["count"] or not st["errors"]["last"], st["errors"])

    # 25. 구분 템플릿 격자는 펼칠 때 그린다(설정 화면에 42칸을 미리 싣지 않는다)
    code, out = post("/settings/template/add", {"name": "스모크템플릿"})
    tpl_id = out.get("id") if isinstance(out, dict) else None
    code, html = get("/settings")
    check("템플릿 격자는 비어서 나간다", 'class="set-tpl-cell' not in html)
    check("격자를 그릴 값은 실려 나간다",
          "__tplCells" in html and "__tplCats" in html and "__tplBlocks" in html)
    if tpl_id:
        code, out = post("/settings/template/cell", {
            "template_id": tpl_id, "weekday": "3", "block_label": "B2",
            "category_id": "",
        })
        check("템플릿 칸 저장(미지정)", code == 200 and out.get("ok"), out)
        post("/settings/template/delete", {"id": tpl_id})

    # 26. 하루 마감 · 기록이 빈 슬롯 모으기
    #     날짜를 지정해 열면 오늘이 아니므로 목록이 없어야 한다(지나간 시각 기준이라).
    code, html = get("/day/2026-07-30")
    check("지난 날짜에는 빈 슬롯 목록이 없다", 'class="cu-row"' not in html)
    code, html = get("/today")
    rows = re.findall(r'<div class="cu-row" data-slot="(\d+)"', html)
    check("오늘은 기록이 빈 슬롯을 모아 준다(0개 이상)", isinstance(rows, list))
    if rows:
        sid = rows[0]
        code, out = post("/save/field", {"entity": "slot", "id": sid,
                                         "field": "did_text", "value": "스모크 한일"})
        check("빈 슬롯 칸에 적으면 저장된다", code == 200 and out.get("ok"), out)
        r = db_query(db_path, "SELECT did_text FROM slots WHERE id = ?", (sid,))
        check("한 일이 그 슬롯에 들어갔다",
              r and r[0]["did_text"] == "스모크 한일", r and r[0]["did_text"])
        code, html = get("/today")
        check("기록이 생기면 목록에서 빠진다",
              f'data-slot="{sid}"' not in re.sub(r'(?s).*?id="cu-list"', '', html)
              .split("</div>\n            </div>")[0])
        post("/save/field", {"entity": "slot", "id": sid,
                             "field": "did_text", "value": ""})

    # 27. 하루 마감은 위 2열(하루 평가 · 내일 가장 중요한 일) + 아래 빈 슬롯 한 열.
    #     '감사 한 줄'도 '고결감 기록' 버튼도 여기서는 뺐다(고결감은 슬롯의 '고민' 버튼으로 연다).
    code, html = get("/today")
    check("감사 한 줄 칸은 사라졌다", 'id="sd-thanks"' not in html)
    check("고결감 버튼은 마감 카드에서 빠졌다", 'class="ghost-btn open-reflect"' not in html)
    check("마감 카드 위는 2열", 'class="sd-2col"' in html)
    # 목록 편집(Enter 로 항목 잇기·Tab 들여쓰기)은 gp-input 을 뺀 모든 textarea 에 이미 걸린다.
    check("하루 평가 칸이 있다", 'name="day_review"' in html and 'class="gp-input"' not in
          html.split('name="day_review"')[0].rsplit("<textarea", 1)[-1])
    check("내일 가장 중요한 일이 2열 안에 있다",
          html.find('class="sd-2col"') < html.find('id="sd-tomorrow"'))
    check("빈 슬롯은 2열 아래에 온다",
          'id="cu-list"' not in html or html.find('id="sd-tomorrow"') < html.find('id="cu-list"'))
    # 하루 평가 저장(한 칸 자동저장 → daily_meta.day_review). 날짜는 서버가 그려 준 값(KST)을 쓴다.
    today_str = re.search(r'class="day-form"[^>]*data-date="(\d{4}-\d\d-\d\d)"', html)
    today_str = today_str.group(1) if today_str else ""
    post("/save/field", {"entity": "meta", "id": today_str,
                         "field": "day_review", "value": "- 잘한 것\n- 아쉬운 것"})
    row = db_query(db_path, "SELECT day_review FROM daily_meta WHERE date = ?", (today_str,))
    check("하루 평가 저장", row and row[0]["day_review"] == "- 잘한 것\n- 아쉬운 것",
          row[0]["day_review"] if row else None)
    # 저장 버튼(폼 전체)으로도 저장된다
    post(f"/save/day/{today_str}", {"day_review": "폼으로 저장"})
    row = db_query(db_path, "SELECT day_review FROM daily_meta WHERE date = ?", (today_str,))
    check("하루 평가 폼 저장", row and row[0]["day_review"] == "폼으로 저장",
          row[0]["day_review"] if row else None)
    # 지난 날짜에는 이 칸이 화면에 없다. 그 폼을 저장해도 이미 적어 둔 평가가 지워지면 안 된다.
    post("/save/day/2026-07-30", {"memo": "지난날 저장"})
    post(f"/save/day/{today_str}", {"memo": ""})
    row = db_query(db_path, "SELECT day_review FROM daily_meta WHERE date = ?", (today_str,))
    check("칸이 없는 저장은 하루 평가를 지우지 않는다",
          row and row[0]["day_review"] == "폼으로 저장", row[0]["day_review"] if row else None)

    # 28. Things3 목록을 블록마다 다시 싣지 않는다
    check("블록 할일 팝오버는 비어서 나간다",
          html.count('class="hover-pop task-pop"></div>') == html.count("task-pop"))


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
