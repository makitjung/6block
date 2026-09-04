# _assign_lanes 최적화가 결과를 한 칸도 바꾸지 않았는지, 옛 구현과 무작위 입력으로 대조한다.
import random
import time

from app.routes.plan import MIN_LANES, NO_RANK, _assign_lanes

# ---------------------------------------------------------------------------
# 최적화 전 원본 구현. 여기 손대면 안 된다. 이 파일의 존재 이유가 '옛 결과와 같은가'다.
# 바뀐 곳은 세 군데뿐이다. held 구간목록 → 잡힌 칸 집합, floor·used 의 max(bars...) → max_lane 추적.
# ---------------------------------------------------------------------------


def _assign_lanes_original(bars: list[dict]) -> int:
    byid = {b["id"]: b for b in bars}

    def root_of(b) -> int:
        cur, seen = b, set()
        while cur["parent_id"] in byid and cur["id"] not in seen:
            seen.add(cur["id"])
            cur = byid[cur["parent_id"]]
        return cur["id"]

    fams: dict[int, list] = {}
    for b in bars:
        fams.setdefault(root_of(b), []).append(b)

    lanes: list[list[tuple[str, str]]] = []
    held: list[tuple[int, int]] = []

    def open_at(i: int, b) -> bool:
        if any(lo <= i < hi for lo, hi in held):
            return False
        while i >= len(lanes):
            lanes.append([])
        return all(b["ve"] < s or b["vs"] > e for s, e in lanes[i])

    def put(i: int, b):
        while i >= len(lanes):
            lanes.append([])
        lanes[i].append((b["vs"], b["ve"]))
        b["lane"] = i

    ordered = sorted(fams.values(),
                     key=lambda f: (f[0]["area_order"], f[0]["rank"],
                                    min(b["vs"] for b in f), f[0]["id"]))
    floor, seen_area = 0, None
    for fam in ordered:
        if seen_area is not None and fam[0]["area_order"] != seen_area:
            floor = max((b["lane"] for b in bars if "lane" in b), default=-1) + 1
        seen_area = fam[0]["area_order"]
        members = sorted(fam, key=lambda x: (x["level"], x["vs"], x["ve"], x["id"]))
        if len(members) < 2:
            b = members[0]
            i = floor
            while not open_at(i, b):
                i += 1
            put(i, b)
            continue
        rel: dict[int, int] = {}
        span: list[list[tuple[str, str]]] = []
        for b in members:
            k = rel.get(b["parent_id"], -1) + 1
            while True:
                if k >= len(span):
                    span.append([])
                if all(b["ve"] < s or b["vs"] > e for s, e in span[k]):
                    break
                k += 1
            span[k].append((b["vs"], b["ve"]))
            rel[b["id"]] = k
        base = floor
        while not all(open_at(base + rel[b["id"]], b) for b in members):
            base += 1
        for b in members:
            put(base + rel[b["id"]], b)
        held.append((base, base + len(span)))
    used = max((b["lane"] for b in bars), default=-1) + 1
    return max(used + 1, MIN_LANES)


# ---------------------------------------------------------------------------


def _make_bars(n: int, seed: int) -> list[dict]:
    """무작위 막대. 상하관계·영역·수동순서·기간 겹침이 골고루 섞이게 만든다."""
    rnd = random.Random(seed)
    bars = []
    for i in range(1, n + 1):
        # 앞서 만든 것 중 하나를 상위로(사슬이 생기게), 또는 최상위
        parent = rnd.choice([None] + [b["id"] for b in bars[-6:]]) if bars else None
        s = rnd.randint(1, 300)
        e = s + rnd.randint(0, 120)
        bars.append({
            "id": i,
            "parent_id": parent,
            "area_order": rnd.randint(0, 4),
            "rank": rnd.randint(0, 3),
            # 형제 순서(srank)는 여기서 고정한다. 이 파일이 재는 것은 옛 구현과의 동일성이고,
            # 옛 구현에는 이 값이 없었다. 모두 같으면 정렬 결과가 옛것과 똑같아진다.
            "srank": NO_RANK,
            "level": rnd.randint(0, 3),
            "vs": f"2026-{s // 31 + 1:02d}-{s % 31 + 1:02d}",
            "ve": f"2026-{e // 31 + 1:02d}-{e % 31 + 1:02d}",
        })
    return bars


def _lanes_of(bars):
    return {b["id"]: b.get("lane") for b in bars}


def test_무작위_입력_200회에서_결과가_완전히_같다():
    """칸 배정과 반환값(쓴 칸 수)이 옛 구현과 하나도 다르지 않아야 한다."""
    for seed in range(200):
        n = (seed % 40) + 1
        a, b = _make_bars(n, seed), _make_bars(n, seed)
        want_used = _assign_lanes_original(a)
        got_used = _assign_lanes(b)
        assert got_used == want_used, f"seed={seed} 반환값 {got_used} != {want_used}"
        assert _lanes_of(b) == _lanes_of(a), f"seed={seed} 칸 배정이 달라졌다"


def test_경계_입력도_같다():
    """빈 목록·1개·전부 같은 영역·전부 다른 영역."""
    cases = [
        [],
        _make_bars(1, 999),
        [dict(b, area_order=0) for b in _make_bars(25, 7)],
        [dict(b, area_order=i) for i, b in enumerate(_make_bars(5, 8))],
        [dict(b, parent_id=None) for b in _make_bars(30, 11)],       # 전부 최상위
    ]
    for i, case in enumerate(cases):
        a = [dict(x) for x in case]
        b = [dict(x) for x in case]
        assert _assign_lanes(b) == _assign_lanes_original(a), f"case {i} 반환값"
        assert _lanes_of(b) == _lanes_of(a), f"case {i} 칸 배정"


def test_최적화가_실제로_빨라졌다(capsys):
    """항목이 많을 때 옛 구현보다 느리지 않아야 한다(같은 결과 + 더 빠름)."""
    out = []
    for n in (200, 800, 2000):
        a, b = _make_bars(n, 42), _make_bars(n, 42)
        t0 = time.perf_counter()
        _assign_lanes_original(a)
        old = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        _assign_lanes(b)
        new = (time.perf_counter() - t0) * 1000
        assert _lanes_of(b) == _lanes_of(a)
        out.append((n, old, new))
    with capsys.disabled():
        print("\n막대수 | 옛 구현 | 새 구현 | 배율")
        for n, old, new in out:
            print(f"{n:6d} | {old:6.1f}ms | {new:6.1f}ms | {old / new:4.1f}배")
    n, old, new = out[-1]
    assert new <= old, f"2000개에서 더 느려졌다: {old:.1f}ms → {new:.1f}ms"
