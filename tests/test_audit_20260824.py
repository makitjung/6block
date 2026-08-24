# 2026-08-24 감사에서 확인한 결함들을 못 박는 회귀 테스트. 고치기 전에는 전부 실패한다.
import datetime
import json

import app.db as db
from app.common import today_str, week_start

MONDAY = week_start(datetime.date.today()).strftime("%Y-%m-%d")


# -- 결함 1. slots(block_id) 인덱스가 없어 블록별 슬롯 조회가 전체 스캔이었다 -------


def test_슬롯을_블록으로_찾는_길에_인덱스가_있다(conn):
    """없으면 slots 전체를 훑는다. 3천행에서 21배, 1만2천행에서 34배 느렸다."""
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'slots'"
    )}
    assert "idx_slots_block" in names, f"slots 인덱스: {names}"


def test_블록별_슬롯_조회가_전체스캔이_아니다(conn):
    plan = " ".join(
        r["detail"] for r in conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id, start_time, do_text FROM slots WHERE block_id = 1 "
            "ORDER BY slot_index"
        )
    )
    assert "SCAN slots" not in plan, plan
    assert "idx_slots_block" in plan, plan


# -- 결함 2. inbox.status 는 넣기만 하고 읽는 코드가 없는 죽은 컬럼이었다 ----------


def test_수집함에_죽은_status_컬럼이_없다(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inbox)")}
    assert cols == {"id", "text", "created_at", "done"}, cols


def test_옛_DB에_status가_있어도_마이그레이션이_걷어낸다(conn):
    """옛 덤프를 복원하면 status 가 되살아난다. 그때 한 번 더 지워지는지 확인한다."""
    conn.execute("ALTER TABLE inbox ADD COLUMN status TEXT NOT NULL DEFAULT ''")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    db.init_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inbox)")}
    assert "status" not in cols, cols


# -- 결함 3. schema.sql 이 이미 만드는 표를 _migrate 가 또 만들고 있었다 -----------


def test_마이그레이션이_스키마와_같은_표를_또_만들지_않는다():
    """init_db 는 schema.sql 을 먼저 통째로 돌린다. _migrate 의 CREATE TABLE 은 죽은 코드다."""
    src = (db.Path(db.__file__).parent / "db.py").read_text(encoding="utf-8")
    body = src[src.index("def _migrate("):]
    body = body[: body.index("\n@contextmanager")]
    assert "CREATE TABLE" not in body, "_migrate 안에 CREATE TABLE 이 남아 있다"


def _rows(sql, params=()):
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# -- 결함 4. 주간 KPI 가 블록이 아니라 슬롯을 세어 4배로 부풀었다 -----------------


def _plan_one_core_block(client):
    """이번 주 월요일 B1 에 PLAN 을 적고, 그 블록의 슬롯 하나에 DO 를 적는다."""
    client.get(f"/week/{MONDAY}")
    b1 = _rows("SELECT id FROM blocks WHERE date = ? AND block_label = 'B1'",
               (MONDAY,))[0]
    with db.get_conn() as c:
        c.execute("UPDATE blocks SET plan_text = '한 블록만 계획' WHERE id = ?", (b1["id"],))
        s = c.execute(
            "SELECT id FROM slots WHERE block_id = ? ORDER BY slot_index LIMIT 1",
            (b1["id"],),
        ).fetchone()
        c.execute("UPDATE slots SET do_text = '한 칸만 실행' WHERE id = ?", (s["id"],))
    return b1["id"]


def test_주간_코어_KPI가_슬롯이_아니라_블록을_센다(client):
    """블록 하나에 슬롯이 4개라, 슬롯을 세면 1블록이 4로 잡혀 정확히 4배 부풀었다."""
    _plan_one_core_block(client)
    html = client.get(f"/week/{MONDAY}").text
    assert "<strong>1</strong>/42" in html, "코어 칸이 블록 수(1)가 아니다"


def test_주간과_분석의_PLAN_DO_달성률이_같다(client):
    """두 화면이 같은 이름의 지표를 서로 다른 단위로 계산하던 것을 막는다."""
    _plan_one_core_block(client)
    week_html = client.get(f"/week/{MONDAY}").text
    assert "<strong>100</strong>%" in week_html, "주간 달성률이 100% 가 아니다"
    analytics_html = client.get("/analytics?days=30").text
    assert analytics_html.count("100") > 0
    # 주간이 세는 계획 블록 수와 분석이 세는 계획 블록 수가 같아야 한다.
    planned = _rows(
        "SELECT COUNT(*) n FROM blocks WHERE is_core = 1 "
        "AND TRIM(COALESCE(plan_text, '')) != '' AND date BETWEEN ? AND ?",
        (MONDAY, (week_start(datetime.date.today())
                  + datetime.timedelta(days=6)).strftime("%Y-%m-%d")),
    )[0]["n"]
    assert planned == 1


# -- 결함 5. 매니페스트 아이콘에 ?v= 가 없어 폰이 화면마다 다시 물었다 -------------


def test_매니페스트_아이콘에_버전이_붙는다(client):
    """?v= 가 없으면 /static 은 no-cache 라 열 때마다 304 왕복이 하나 더 생긴다."""
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    data = json.loads(r.text)
    icons = [i["src"] for i in data["icons"]]
    assert icons, "아이콘 목록이 비었다"
    for src in icons:
        assert "?v=" in src, f"버전이 없는 아이콘 주소: {src}"


def test_버전이_붙은_아이콘은_1년_캐시로_나간다(client):
    data = json.loads(client.get("/manifest.webmanifest").text)
    src = data["icons"][0]["src"]
    r = client.get(src)
    assert r.status_code == 200
    assert "immutable" in r.headers.get("cache-control", ""), r.headers


def test_매니페스트가_버전을_붙이는_아이콘은_전부_VERSIONED_ASSETS에_있다():
    """빠진 파일에 ?v= 를 붙이면 그림을 바꿔도 폰이 1년 동안 옛것을 쓴다."""
    from app.common import BASE_DIR, VERSIONED_ASSETS

    data = json.loads((BASE_DIR / "static" / "manifest.json").read_text(encoding="utf-8"))
    missing = [
        i["src"] for i in data["icons"]
        if i["src"].startswith("/static/")
        and i["src"].rsplit("/", 1)[-1] not in VERSIONED_ASSETS
    ]
    assert not missing, f"VERSIONED_ASSETS 에 빠진 아이콘: {missing}"


# -- 결함 6. 같은 날짜를 동시에 처음 열면 하나만 살고 나머지는 500 이었다 -----------


def test_새_날짜를_동시에_열어도_500이_없다(client, fresh_db):
    """폰과 맥이 함께 열려 있고 자정을 넘겨 새 날짜를 폴링할 때 나던 자리다.

    예전에는 UNIQUE(date, block_order) 에 걸려 진 쪽이 IntegrityError 로 500 이 됐다.
    HTTP 로 10개를 동시에 던지면 9개가 500 이었다.
    """
    import concurrent.futures

    target = "2027-03-15"
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        codes = [f.result() for f in [
            ex.submit(lambda: client.get(f"/day/{target}").status_code)
            for _ in range(10)
        ]]
    assert codes == [200] * 10, f"200 이 아닌 응답: {sorted(set(codes))}"

    blocks = _rows("SELECT block_order FROM blocks WHERE date = ?", (target,))
    slots = _rows("SELECT slot_index FROM slots WHERE date = ?", (target,))
    assert len(blocks) == 8, f"블록이 {len(blocks)}개다"
    assert len({b["block_order"] for b in blocks}) == 8, "블록 순서가 겹친다"
    assert len(slots) == len({s["slot_index"] for s in slots}), "슬롯 번호가 겹친다"


def test_같은_주를_동시에_열어도_500이_없다(client, fresh_db):
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        codes = [f.result() for f in [
            ex.submit(lambda: client.get("/week/2027-03-15").status_code)
            for _ in range(8)
        ]]
    assert codes == [200] * 8, f"200 이 아닌 응답: {sorted(set(codes))}"


# -- 결함 7. 달력을 넘겨 보기만 해도 빈 골격이 끝없이 쌓이던 것 ---------------------


def test_빈_껍데기_날짜만_지우고_적어_둔_날은_남긴다(client, fresh_db):
    """화면을 여는 것만으로 그 날짜의 블록 8행·슬롯 30여 행이 생긴다.

    2026-08-24 실데이터에서 블록이 있는 날짜 106일 중 31일(29%)이 껍데기였다.
    """
    from app.common import purge_empty_days

    far_empty = "2020-03-04"      # 오늘 ±180일 바깥, 아무것도 안 적음
    far_filled = "2020-03-05"     # 오늘 ±180일 바깥이지만 적어 둔 것이 있음
    near_empty = today_str()      # 오늘 ±180일 안쪽 → 건드리지 않는다

    for d in (far_empty, far_filled, near_empty):
        assert client.get(f"/day/{d}").status_code == 200
    with db.get_conn() as conn:
        b = conn.execute("SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
                         (far_filled,)).fetchone()
        conn.execute("UPDATE blocks SET plan_text = '남겨야 한다' WHERE id = ?", (b["id"],))

    with db.get_conn() as conn:
        removed = purge_empty_days(conn)

    assert removed == 1, f"{removed}일을 지웠다"
    assert not _rows("SELECT id FROM blocks WHERE date = ?", (far_empty,))
    assert not _rows("SELECT id FROM slots WHERE date = ?", (far_empty,))
    assert _rows("SELECT id FROM blocks WHERE date = ?", (far_filled,)), "적어 둔 날을 지웠다"
    assert _rows("SELECT id FROM blocks WHERE date = ?", (near_empty,)), "가까운 날을 지웠다"


def test_지운_날을_다시_열면_그대로_다시_만든다(client, fresh_db):
    from app.common import purge_empty_days

    d = "2020-03-04"
    client.get(f"/day/{d}")
    with db.get_conn() as conn:
        purge_empty_days(conn)
    assert client.get(f"/day/{d}").status_code == 200
    assert len(_rows("SELECT id FROM blocks WHERE date = ?", (d,))) == 8


# -- 결함 8. 장기 탭이 열지도 않은 편집칸 36개를 화면에 세워 두던 것 -----------------


def _plan_with_bars(client):
    """장기 항목 세 개를 만들어 막대가 있는 장기 탭을 얻는다."""
    area = _rows("SELECT id FROM lt_area ORDER BY display_order LIMIT 1")[0]["id"]
    for i in range(3):
        r = client.post("/plan/item/add", data={
            "title": f"항목{i}", "start": "2026-08-01", "end": "2026-08-31",
            "area_id": str(area),
        })
        assert r.json()["ok"], r.text
    html = client.get("/plan").text
    assert "항목0" in html
    return html


def test_장기_편집칸은_template_안에_있다(client, fresh_db):
    """template 안의 내용은 문서에 서지 않아 배치·스타일 계산을 받지 않는다.

    2026-08-24 실측 · 실데이터 사본(막대 36개)에서 DOM 노드 2,642 → 724개,
    input 219 → 50개, DOMContentLoaded 331ms → 116ms.
    """
    html = _plan_with_bars(client)
    assert html.count('<template class="gt-edit-tpl"') == 3, "편집칸이 template 이 아니다"
    # 살아 있는 편집칸은 하나도 없어야 한다(전부 template 안)
    for chunk in html.split('<template class="gt-edit-tpl"')[:1]:
        assert 'class="gt-edit"' not in chunk, "template 밖에 편집칸이 서 있다"


def test_장기_항목이_늘어도_화면에_서는_입력칸은_그대로다(client, fresh_db):
    """편집칸을 다 세우던 시절에는 막대 하나마다 입력칸 여섯 개가 문서에 들어왔다.

    지금 남는 것은 블록 줄마다 하나씩인 '추가 폼'뿐이라 항목 수와 무관하다.
    """
    import re

    def live_inputs(html):
        live = re.sub(r"<template class=\"gt-edit-tpl\".*?</template>", "", html, flags=re.S)
        return live.count("<input")

    _plan_with_bars(client)
    few = live_inputs(client.get("/plan").text)

    area = _rows("SELECT id FROM lt_area ORDER BY display_order LIMIT 1")[0]["id"]
    for i in range(12):
        client.post("/plan/item/add", data={
            "title": f"더{i}", "start": "2026-08-01", "end": "2026-08-31",
            "area_id": str(area),
        })
    many_html = client.get("/plan").text
    many = live_inputs(many_html)

    assert many_html.count('<template class="gt-edit-tpl"') == 15, "막대가 15개가 아니다"
    assert many == few, f"항목이 3개에서 15개가 되자 입력칸이 {few} → {many} 로 늘었다"
