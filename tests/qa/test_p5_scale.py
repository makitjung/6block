# /week·/analytics·/today 의 쿼리 수와 시간이 데이터량에 비례해 늘어나는지 확인(N+1 탐지)
import sqlite3
from datetime import date, timedelta


def _seed_range(start_i: int, end_i: int):
    """start_i 일째부터 end_i 일째까지(0-based, end 미포함) 기록을 직접 넣는다."""
    from app.common import today_str
    from app.config import DAY_BLOCKS, slots_for_day
    from app.db import get_conn
    # 오늘 화면이 만드는 골격과 겹치지 않게 전부 과거에 심는다(오늘 -1005일 ~ -6일).
    base = date.fromisoformat(today_str()) - timedelta(days=1005)
    now = "2024-01-01T00:00:00"
    with get_conn() as c:
        for i in range(start_i, end_i):
            ds = (base + timedelta(days=i)).strftime("%Y-%m-%d")
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


def test_query_and_time_scaling(client, capsys):
    """데이터가 30일 → 1000일로 33배 늘 때 쿼리 수·응답시간이 어떻게 변하는지 실측."""
    import time
    real = sqlite3.connect

    def probe(url, rounds=3):
        n = [0]

        def cc(*a, **k):
            c = real(*a, **k)
            c.set_trace_callback(lambda _s: n.__setitem__(0, n[0] + 1))
            return c

        sqlite3.connect = cc
        try:
            client.get(url)
        finally:
            sqlite3.connect = real
        client.get(url)                       # 워밍업
        xs = []
        for _ in range(rounds):
            t0 = time.perf_counter()
            client.get(url)
            xs.append((time.perf_counter() - t0) * 1000)
        return n[0], sorted(xs)[len(xs) // 2]

    rows = []
    done = 0
    for target in (30, 365, 1000):
        _seed_range(done, target)
        done = target
        rows.append((target,) + probe("/week") + probe("/analytics") + probe("/today"))

    with capsys.disabled():
        print("\n 일수 | /week 쿼리  ms | /analytics 쿼리  ms | /today 쿼리  ms")
        for r in rows:
            print(f"{r[0]:5d} | {r[1]:10d} {r[2]:4.0f} | {r[3]:14d} {r[4]:4.0f} | "
                  f"{r[5]:11d} {r[6]:4.0f}")

    first, last = rows[0], rows[-1]
    # 데이터가 33배 늘어도 화면이 보는 범위(한 주·하루)는 그대로다. 쿼리 수가 함께 늘면 N+1 이다.
    assert last[1] <= first[1] * 2, f"/week 쿼리 수가 데이터량 따라 증가: {first[1]} → {last[1]}"
    assert last[5] <= first[5] * 2, f"/today 쿼리 수가 데이터량 따라 증가: {first[5]} → {last[5]}"
    # 분석은 전 기간을 보므로 늘어나는 게 정상이다. 다만 1초를 넘으면 체감된다.
    assert last[4] < 1000, f"/analytics 1000일에서 {last[4]:.0f}ms"
