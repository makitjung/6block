# 최종 검증. 페르소나 보고를 실제 클라이언트(app.js) 동작과 대조하고, 누락된 엔드포인트 성능을 잰다.
import time
from datetime import date, timedelta

import pytest

from app.integrations import gcal_write


# ---------------------------------------------------------------------------
# 1. /save/field 메타 3칸 : 3단계가 'High 데이터 유실'로 보고한 건의 재검증
#    app.js bindAutoSave(3241~3245)는 data-as-prefix 로 그룹의 세 칸을 모두 모아 보낸다.
#    (app.js 719~733 에서 goal/dplan/grat/concept 각 칸에 asPrefix·asIdx 를 심는다)
#    따라서 실제 브라우저가 보내는 폼에는 dplan1·dplan2·dplan3 이 함께 들어 있다.
# ---------------------------------------------------------------------------


def _meta_col(client, date_str, col):
    from app.db import get_conn
    with get_conn() as c:
        row = c.execute(
            f"SELECT {col} FROM daily_meta WHERE date = ?", (date_str,)
        ).fetchone()
        return row[col] if row else None


def test_save_field_real_client_payload_saves(client):
    """실제 app.js 가 보내는 폼(그룹 3칸 동봉)이면 정상 저장된다 → 보고된 유실은 재현되지 않는다."""
    d = "2026-08-15"
    client.get(f"/day/{d}")
    r = client.post("/save/field", data={
        "entity": "meta", "id": d, "field": "dplan1", "value": "달성1",
        # app.js 가 groupPrefix 로 함께 싣는 세 칸
        "dplan1": "달성1", "dplan2": "", "dplan3": "",
    })
    assert r.status_code == 200, r.text
    assert _meta_col(client, d, "daily_plan") == "달성1\n\n"


def test_save_field_real_client_payload_preserves_siblings(client):
    """두 번째 칸만 고쳐도 첫 칸이 살아남는다(부분 갱신 불변식)."""
    d = "2026-08-15"
    client.get(f"/day/{d}")
    client.post("/save/field", data={
        "entity": "meta", "id": d, "field": "dplan1", "value": "가",
        "dplan1": "가", "dplan2": "", "dplan3": "",
    })
    client.post("/save/field", data={
        "entity": "meta", "id": d, "field": "dplan2", "value": "나",
        "dplan1": "가", "dplan2": "나", "dplan3": "",
    })
    assert _meta_col(client, d, "daily_plan") == "가\n나\n"


def test_save_field_tag_path_single_key_saves(client):
    """태그 경로(app.js 1049)는 한 칸만 보낸다. 그 칸도 정상 저장돼야 한다."""
    d = "2026-08-15"
    client.get(f"/day/{d}")
    r = client.post("/save/field", data={
        "entity": "meta", "id": d, "field": "goaltag2", "value": "건강",
        "goaltag2": "건강",          # app.js 가 {[group+idx]: val} 로 함께 싣는 키
    })
    assert r.status_code == 200
    assert _meta_col(client, d, "goal_tags") == "\n건강\n"


def test_save_field_minimal_payload_is_silently_dropped(client):
    """계약 취약점(진짜 결함이 아니라 견고성 문제).

    그룹 키 없이 {entity,field,value} 만 보내면 200 을 돌려주면서 값을 버린다.
    현재 화면은 이런 폼을 보내지 않으므로 사용자에게 드러나지 않는다. 다만 이 엔드포인트를
    다른 클라이언트(Record 앱·스크립트)가 부르면 조용히 유실된다.
    """
    d = "2026-08-15"
    client.get(f"/day/{d}")
    r = client.post("/save/field", data={
        "entity": "meta", "id": d, "field": "dplan1", "value": "버려짐",
    })
    assert r.status_code == 200                       # 성공이라고 답하지만
    assert _meta_col(client, d, "daily_plan") == ""   # 값은 저장되지 않는다


# ---------------------------------------------------------------------------
# 2. parse_summary 중첩 대괄호 : docstring 약속 위반 재확인 + 현실 경로 확인
# ---------------------------------------------------------------------------


def test_parse_summary_nested_bracket_breaks_docstring():
    """docstring: '형식이 아니면 (고민, 통째 제목)'. 중첩 대괄호는 통째가 아니라 잘린 제목을 준다."""
    kind, title = gcal_write.parse_summary("[고민 [부제]] 제목")
    assert kind == "고민"
    assert title == "] 제목"                    # 실제
    assert title != "[고민 [부제]] 제목"          # 약속된 '통째'가 아니다


@pytest.mark.parametrize("summary,want_kind,want_title", [
    ("[고민] 제목", "고민", "제목"),
    ("[결정] [중요] 회의", "결정", "[중요] 회의"),   # 제목 안의 대괄호는 정상 보존
    ("[결심] 옛 명칭", "결정", "옛 명칭"),           # 옛 명칭은 별칭으로 정규화된다
    ("[독서] 채근담 [상편]", "고민", "채근담 [상편]"),  # 모르는 종류는 고민으로
    ("대괄호 없음", "고민", "대괄호 없음"),
])
def test_parse_summary_realistic_cases_ok(summary, want_kind, want_title):
    """앱이 실제로 만들어 내는 요약은 모두 올바르게 되읽힌다(손상은 첫 괄호 안에 '[' 가 있을 때만)."""
    assert gcal_write.parse_summary(summary) == (want_kind, want_title)


def test_create_event_roundtrip_never_produces_nested_first_bracket():
    """앱이 만드는 요약은 '[종류] 제목' 이고 종류는 고정 집합(고민·결정·감사)이라 중첩이 안 생긴다."""
    for kind in ("고민", "결정", "감사"):
        summary = f"[{kind}] 사용자가 [대괄호] 넣은 제목"
        assert gcal_write.parse_summary(summary) == (kind, "사용자가 [대괄호] 넣은 제목")


# ---------------------------------------------------------------------------
# 3. _next_day 극단 날짜 : 처리되지 않은 OverflowError 재확인
# ---------------------------------------------------------------------------


def test_next_day_overflow_at_max_year():
    """9999-12-31 은 형식이 유효해 _parse_date 를 통과하지만 +1일에서 터진다."""
    assert gcal_write._next_day("2026-08-15") == "2026-08-16"
    with pytest.raises(OverflowError):
        gcal_write._next_day("9999-12-31")


def test_next_day_no_guard_in_callers():
    """호출부에 try/except 가 없어 이 예외는 그대로 위로 올라간다(500 이 된다)."""
    import inspect
    src = inspect.getsource(gcal_write.create_event)
    assert "_next_day" in src
    assert "OverflowError" not in src


# ---------------------------------------------------------------------------
# 4. 누락된 4단계: 엔드포인트 응답시간과 데이터량 스케일 (실패한 에이전트 몫)
# ---------------------------------------------------------------------------


def _seed_days(n: int):
    """n 일치 기록을 DB 에 직접 넣는다(라우터를 n 번 부르지 않고 빠르게)."""
    from app.db import get_conn
    from app.config import DAY_BLOCKS, slots_for_day
    start = date(2026, 1, 1)
    now = "2026-01-01T00:00:00"
    with get_conn() as c:
        for i in range(n):
            ds = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            ids = {}
            for order, (label, is_core, s_t, e_t) in enumerate(DAY_BLOCKS):
                cur = c.execute(
                    "INSERT INTO blocks (date, block_order, block_label, is_core, "
                    "start_time, end_time, plan_text, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (ds, order, label, 1 if is_core else 0, s_t, e_t, f"계획{order}", now),
                )
                ids[label] = cur.lastrowid
            for idx, label, s_t, e_t in slots_for_day(DAY_BLOCKS):
                c.execute(
                    "INSERT INTO slots (date, block_id, slot_index, start_time, end_time, "
                    "do_text, did_text, done, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ds, ids[label], idx, s_t, e_t, f"할일{idx}", f"한일{idx}", idx % 2, now),
                )


def _median_ms(fn, rounds=5):
    fn()                                   # 워밍업 1회는 버린다
    xs = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return sorted(xs)[len(xs) // 2]


@pytest.mark.parametrize("days", [0, 30, 365])
def test_endpoint_latency_scaling(client, days, capsys):
    """데이터량을 늘려 가며 대표 화면의 응답시간을 실측한다."""
    if days:
        _seed_days(days)
    out = {}
    for name, url in (("/today", "/today"), ("/week", "/week"),
                      ("/plan", "/plan"), ("/analytics", "/analytics"),
                      ("/reflect", "/reflect"), ("/settings", "/settings"),
                      ("/data", "/data"), ("/api/day", "/api/day/2026-01-15")):
        r = client.get(url)
        assert r.status_code == 200, f"{url} → {r.status_code}"
        out[name] = _median_ms(lambda u=url: client.get(u))
    with capsys.disabled():
        print(f"\n[기록 {days}일] " + "  ".join(f"{k}={v:.0f}ms" for k, v in out.items()))
    # 개인용 앱이고 폰에서 접속한다. 1초를 넘으면 체감된다.
    slow = {k: v for k, v in out.items() if v > 1000}
    assert not slow, f"1초 초과: {slow}"


def test_query_count_per_request(client, capsys):
    """한 요청이 날리는 SQL 개수를 세어 N+1 을 찾는다."""
    import sqlite3
    import app.db as db

    _seed_days(30)
    counts = {}
    real_connect = sqlite3.connect
    for name, url in (("/today", "/today"), ("/week", "/week"),
                      ("/analytics", "/analytics"), ("/plan", "/plan")):
        n = [0]

        def counting_connect(*a, **k):
            c = real_connect(*a, **k)
            c.set_trace_callback(lambda _s: n.__setitem__(0, n[0] + 1))
            return c

        sqlite3.connect = counting_connect
        try:
            client.get(url)
        finally:
            sqlite3.connect = real_connect
        counts[name] = n[0]
    with capsys.disabled():
        print("\n[30일 기록 쿼리 수] " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    assert all(v < 500 for v in counts.values()), f"쿼리 폭주: {counts}"
