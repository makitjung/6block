# 2026-08-24 감사에서 확인한 결함들을 못 박는 회귀 테스트. 고치기 전에는 전부 실패한다.
import app.db as db


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
