# 2026-08-24 감사에서 확인한 결함들을 못 박는 회귀 테스트. 고치기 전에는 전부 실패한다.

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
