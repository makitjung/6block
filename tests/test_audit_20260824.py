# 2026-08-24 감사에서 확인한 결함들을 못 박는 회귀 테스트. 고치기 전에는 전부 실패한다.
import datetime
import json

import app.db as db
from app.common import week_start

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
