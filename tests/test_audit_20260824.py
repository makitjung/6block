# 2026-08-24 감사에서 확인한 결함들을 못 박는 회귀 테스트. 고치기 전에는 전부 실패한다.
import datetime

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
