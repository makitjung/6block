# 장기플랜(연·분기·월·주 계획 막대)과 영역·항목 관리를 담당하는 라우터
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.common import (
    KST,
    _parse_date,
    int_id,
    opt_id,
    templates,
)
from app.config import CORE_BLOCKS, TONE_KEYS, TONES, area_tone
from app.db import get_conn, get_day_blocks

router = APIRouter()


# -- 장기플랜 기간 계산 ------------------------------------------------------
PLAN_LEVELS = ("year", "quarter", "month", "week")
PLAN_LEVEL_LABELS = {"year": "연", "quarter": "분기", "month": "월", "week": "주"}


def _parse_anchor(anchor: str) -> date:
    """anchor 쿼리(YYYY-MM-DD)를 date로. 비었거나 잘못되면 오늘(KST)."""
    try:
        return datetime.strptime(anchor, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return datetime.now(KST).date()


def _month_last(y: int, m: int) -> date:
    """그 달의 마지막 날."""
    return (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)


def _plan_columns(level: str, anchor: date):
    """(열 목록, 헤더 라벨). 보고 있는 기간이 왼쪽에서 두 번째 열에 오도록 앞에 하나를 더 둔다.

    연은 5칸, 분기·월·주는 4칸이다. 열은 key·label·sub·current·week_link·drill_*·start·end
    와 함께 brk(더 큰 단위가 바뀌는 자리의 이름)·brk_level(year|quarter|month)을 가진다.
    brk 는 첫 열에는 달지 않는다(왼쪽 끝에는 그을 자리가 없다).

    drill_level/drill_anchor: 그 열 머리글을 누르면 들어갈 다음(더 잘은) 단위와 anchor.
    start/end: 그 열이 덮는 실제 날짜 구간(date). 간트 막대 위치 계산에 쓴다.
    """
    today = datetime.now(KST).date()
    cols: list[dict] = []
    if level == "year":
        y0 = anchor.year - 1                    # 보고 있는 해가 두 번째
        for y in range(y0, y0 + 5):
            cols.append({"key": str(y), "label": str(y), "sub": "",
                         "current": y == today.year, "week_link": None,
                         "drill_level": "quarter", "drill_anchor": f"{y}-01-01",
                         "start": date(y, 1, 1), "end": date(y, 12, 31),
                         "brk": "", "brk_level": ""})
        header = f"{y0}–{y0 + 4}"
    elif level == "quarter":
        q_start = date(anchor.year, (anchor.month - 1) // 3 * 3 + 1, 1)
        for i in range(4):
            s = _add_months(q_start, (i - 1) * 3)
            y, m0 = s.year, s.month
            q = (m0 - 1) // 3 + 1
            cols.append({"key": f"{y}-Q{q}", "label": f"{q}분기",
                         "sub": f"{m0}~{m0 + 2}월",
                         "current": y == today.year and (today.month - 1) // 3 + 1 == q,
                         "week_link": None,
                         "drill_level": "month",
                         "drill_anchor": f"{y}-{m0:02d}-01",
                         "start": s, "end": _month_last(y, m0 + 2),
                         "brk": f"{y}년" if (q == 1 and i) else "",
                         "brk_level": "year" if (q == 1 and i) else ""})
        header = _span_header(cols)
    elif level == "month":
        m_start = date(anchor.year, anchor.month, 1)
        for i in range(4):
            s = _add_months(m_start, i - 1)
            y, m = s.year, s.month
            if m == 1:
                brk, lv = f"{y}년", "year"
            elif m in (4, 7, 10):
                brk, lv = f"{(m - 1) // 3 + 1}분기", "quarter"
            else:
                brk, lv = "", ""
            cols.append({"key": f"{y}-{m:02d}", "label": f"{m}월", "sub": "",
                         "current": y == today.year and m == today.month,
                         "week_link": None,
                         "drill_level": "week", "drill_anchor": f"{y}-{m:02d}-01",
                         "start": s, "end": _month_last(y, m),
                         "brk": brk if i else "", "brk_level": lv if i else ""})
        header = _span_header(cols)
    else:  # week
        cur_monday = today - timedelta(days=today.weekday())
        first = anchor - timedelta(days=anchor.weekday() + 7)   # 보고 있는 주가 두 번째
        prev_month = None
        for i in range(4):
            monday = first + timedelta(weeks=i)
            end = monday + timedelta(days=6)
            if prev_month is None or monday.month == prev_month:
                brk, lv = "", ""
            elif monday.month == 1:
                brk, lv = f"{monday.year}년", "year"
            else:
                brk, lv = f"{monday.month}월", "month"
            prev_month = monday.month
            key = monday.strftime("%Y-%m-%d")
            cols.append({"key": key, "label": f"{monday.month}/{monday.day}",
                         "sub": f"~{end.month}/{end.day} · {monday.isocalendar()[1]}주",
                         "current": monday == cur_monday, "week_link": key,
                         "drill_level": None, "drill_anchor": None,
                         "start": monday, "end": end,
                         "brk": brk, "brk_level": lv})
        header = _span_header(cols)
    return cols, header


def _span_header(cols) -> str:
    """보이는 기간 전체를 한 줄로. 해가 같으면 해를 한 번만 적는다."""
    s, e = cols[0]["start"], cols[-1]["end"]
    if s.year == e.year:
        return f"{s.year}년 {s.month}월 – {e.month}월"
    return f"{s.year}년 {s.month}월 – {e.year}년 {e.month}월"


def _plan_nav(level: str, anchor: date):
    """이전/다음 anchor(YYYY-MM-DD 문자열) 쌍. 그 단위 하나만큼만 옮긴다."""
    if level == "year":
        return f"{anchor.year - 1:04d}-01-01", f"{anchor.year + 1:04d}-01-01"
    if level == "quarter":
        return (_add_months(anchor, -3).strftime("%Y-%m-%d"),
                _add_months(anchor, 3).strftime("%Y-%m-%d"))
    if level == "month":
        return (_add_months(anchor, -1).strftime("%Y-%m-%d"),
                _add_months(anchor, 1).strftime("%Y-%m-%d"))
    return ((anchor - timedelta(days=7)).strftime("%Y-%m-%d"),
            (anchor + timedelta(days=7)).strftime("%Y-%m-%d"))


def _plan_breadcrumb(level: str, anchor: date):
    """연>분기>월>주 경로. 각 단위는 anchor가 속한 기간 라벨 + 그 단위로 가는 링크."""
    q = (anchor.month - 1) // 3 + 1
    monday = anchor - timedelta(days=anchor.weekday())
    labels = {
        "year": str(anchor.year),
        "quarter": f"{q}분기",
        "month": f"{anchor.month}월",
        "week": f"{monday.month}/{monday.day} 주",
    }
    a = anchor.strftime("%Y-%m-%d")
    idx = PLAN_LEVELS.index(level)
    return [
        {"level": lv, "label": labels[lv], "anchor": a, "current": lv == level}
        for i, lv in enumerate(PLAN_LEVELS)
        if i <= idx
    ]


# -- 장기플랜 간트 ----------------------------------------------------------
# lt_item 한 줄이 간트 막대 하나다. parent_id 로 연→분기→월→주 항목을 잇고,
# 하위가 있는 항목은 기간(하위 최소~최대)과 진척률(하위 평균)을 하위에서 자동으로 따라간다.


def _lt_rollup(conn, item_id: int | None):
    """상위 사슬을 하위에 맞춘다. 기간은 하위를 모두 품도록 넓히고, 진척률은 하위 평균을 따른다.

    직접 정한 상위 기간은 줄이지 않는다(연 계획 안에 3개월짜리 하위 하나만 있어도
    연 계획은 그대로 남는다). 날짜는 'YYYY-MM-DD' 라 문자열 비교가 곧 날짜 비교다.
    """
    now = datetime.now(KST).isoformat(timespec="seconds")
    seen: set[int] = set()
    cur = item_id
    while cur:
        row = conn.execute(
            "SELECT parent_id FROM lt_item WHERE id = ?", (cur,)
        ).fetchone()
        pid = row["parent_id"] if row else None
        if not pid or pid in seen:      # 상위가 없거나 순환이면 멈춘다
            return
        seen.add(pid)
        agg = conn.execute(
            "SELECT MIN(start_date) AS s, MAX(end_date) AS e, "
            "       AVG(progress) AS p, COUNT(*) AS n "
            "FROM lt_item WHERE parent_id = ?",
            (pid,),
        ).fetchone()
        prow = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (pid,)
        ).fetchone()
        if agg and agg["n"] and prow:
            conn.execute(
                "UPDATE lt_item SET start_date = ?, end_date = ?, progress = ?, "
                "updated_at = ? WHERE id = ?",
                (min(prow["start_date"], agg["s"]), max(prow["end_date"], agg["e"]),
                 round(agg["p"] or 0), now, pid),
            )
        cur = pid


# 블록 한 줄에 적어도 이만큼 칸을 둔다. 기간이 겹치는 막대는 아래 칸으로 내려 서로 가리지 않는다.
MIN_LANES = 2


def _assign_lanes(bars: list[dict]) -> int:
    """한 줄 안에서 막대를 위아래 칸에 나눠 담는다. 쓴 칸 수(최소 MIN_LANES)를 돌려준다.

    상위 항목이 하위 항목보다 위 칸에 오도록 계층 단계가 얕은 것부터 자리를 잡고,
    같은 줄에 상위가 있으면 그 바로 아래 칸부터 빈자리를 찾는다. 기간이 안 겹치는
    것끼리는 같은 칸을 나눠 쓴다. 날짜는 'YYYY-MM-DD'라 문자열 비교가 곧 날짜 비교이며,
    화면에 보이는 구간(vs~ve)으로 재야 눈에 겹치지 않는다.
    """
    # 한 뿌리(최상위)와 그 하위들을 한 묶음으로 보고, 묶음마다 연속한 칸을 통째로 준다.
    # 그래야 상위와 하위 사이에 남남인 항목이 끼어들지 않는다.
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

    lanes: list[list[tuple[str, str]]] = []      # 칸마다 이미 놓인 (시작, 끝) 목록
    held: list[tuple[int, int]] = []             # 묶음이 통째로 잡아 둔 칸 범위
    def open_at(i: int, b) -> bool:
        """그 칸이 어느 묶음에도 잡혀 있지 않고 기간도 안 겹치는가."""
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

    # 영역 표시 순서대로 위에서 아래로 놓는다. 같은 영역 안에서는 손으로 정한 순서(rank)를
    # 먼저 따르고, 정한 적이 없으면 예전처럼 일찍 시작하는 것부터 놓는다. 기간이 안 겹치면
    # 칸을 나눠 쓰고, 영역이 바뀌면 지금까지 쓴 칸 아래로 내려 영역끼리 섞이지 않게 한다.
    # 묶음(상위+하위)은 연속한 칸 범위를 통째로 잡아 사이에 남이 못 끼게 한다.
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
        rel: dict[int, int] = {}          # 항목 id → 묶음 안에서의 상대 칸
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
    # 맨 아래 한 칸은 늘 비워 둔다. 하위 막대를 그리로 끌어내려 상위에서 떼고,
    # 다른 줄에서 끌어온 막대를 놓는 자리로도 쓴다.
    # (lanes 는 자리를 찾다 늘어나기도 해서, 실제로 쓴 칸으로 센다)
    used = max((b["lane"] for b in bars), default=-1) + 1
    return max(used + 1, MIN_LANES)


def _lt_apply_delta(conn, item_id: int, ds: int, de: int, now: str):
    """상위 기간이 움직인 만큼 하위 사슬의 시작·종료도 같이 민다.

    시작을 ds일, 종료를 de일 옮긴다. 한쪽만 움직였으면 반대쪽 날짜는 건드리지 않는다
    (상위 시작만 미뤘는데 하위 종료일까지 따라 밀리면 적어 둔 마감이 말없이 바뀐다).
    기간이 뒤집히는 경우에만 움직이는 쪽을 반대쪽 끝에 붙여 세운다. 즉 안 움직이기로 한
    날짜는 어떤 경우에도 그대로 남는다.
    """
    if not ds and not de:
        return
    for kid in _lt_descendants(conn, item_id):
        row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (kid,)
        ).fetchone()
        s, e = _parse_date(row["start_date"]), _parse_date(row["end_date"])
        if not s or not e:
            continue
        s2, e2 = s, e
        if ds and de:       # 양쪽이 함께 움직였으면(통째 이동·기간 재입력) 둘 다 민다
            s2 = s + timedelta(days=ds)
            e2 = max(e + timedelta(days=de), s2)
        elif ds:            # 시작만 움직였다. 종료는 그대로 두고 뒤집힐 때만 종료에 붙인다
            s2 = min(s + timedelta(days=ds), e)
        else:               # 종료만 움직였다. 시작은 그대로 두고 뒤집힐 때만 시작에 붙인다
            e2 = max(e + timedelta(days=de), s)
        conn.execute(
            "UPDATE lt_item SET start_date = ?, end_date = ?, updated_at = ? WHERE id = ?",
            (s2.isoformat(), e2.isoformat(), now, kid),
        )

# 막대 색 진하기는 기간 길이가 아니라 계층 단계로 정한다. 최상위가 가장 진하고 하위로 갈수록 연하다.
# 3단계까지만 나눠 진하기 차이를 뚜렷하게 둔다(더 깊은 하위는 마지막 단계로 눌러 그린다).
MAX_LEVEL = 2
LEVEL_LABELS = ["최상위", "하위", "하위2"]

# 같은 영역에서 계획을 여럿 돌릴 때 서로 구분되도록 뿌리(최상위 항목)마다 색조를 조금씩 돌린다(도).
# 영역 색 계열은 알아볼 만큼만 비틀고, 한 항목의 하위들은 뿌리와 같은 색조를 쓴다.
HUE_STEPS = [0, -10, 10, -20, 20]

# 세로 순서를 손으로 정한 적이 없는 뿌리에 매기는 값. 정한 것들 뒤에 서지 않고 예전과 같은
# 자리(일찍 시작한 것이 위)를 지키도록, 정렬에서 서로 비길 만큼 큰 값 하나를 함께 쓴다.
NO_RANK = 1_000_000


def _lt_descendants(conn, item_id: int) -> list[int]:
    """그 항목 아래의 모든 하위 항목 id(깊이 무관)."""
    out: list[int] = []
    stack = [item_id]
    while stack:
        cur = stack.pop()
        for r in conn.execute("SELECT id FROM lt_item WHERE parent_id = ?", (cur,)):
            out.append(r["id"])
            stack.append(r["id"])
    return out


def _lt_root(conn, item_id: int) -> int:
    """그 항목이 속한 뿌리(최상위) 항목 id. 세로 순서는 뿌리 단위로만 매긴다."""
    cur, seen = item_id, set()
    while cur not in seen:
        seen.add(cur)
        row = conn.execute(
            "SELECT parent_id FROM lt_item WHERE id = ?", (cur,)
        ).fetchone()
        if not row or not row["parent_id"]:
            return cur
        cur = row["parent_id"]
    return cur


def _lt_rollup_parent(conn, parent_id: int | None):
    """상위를 잃거나 얻은 쪽의 사슬을 남은 자식 기준으로 다시 계산한다."""
    if not parent_id:
        return
    sib = conn.execute(
        "SELECT id FROM lt_item WHERE parent_id = ? LIMIT 1", (parent_id,)
    ).fetchone()
    if sib:
        _lt_rollup(conn, sib["id"])


def _add_months(d: date, n: int) -> date:
    """달을 n개 더한 날짜. 그 달에 없는 날(1/31 + 1개월)은 말일로 맞춘다."""
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, _month_last(y, m).day))


def _block_rows() -> list[dict]:
    """간트 왼쪽에 세울 행. 코어블록 B1~B6 + 블록을 정하지 않은 항목이 모이는 '미지정' 한 줄."""
    times = {lbl: f"{s}~{e}" for lbl, is_core, s, e in get_day_blocks() if is_core}
    rows = [{"key": b, "label": b, "time": times.get(b, "")} for b in CORE_BLOCKS]
    rows.append({"key": "", "label": "미지정", "time": "블록 없음"})
    return rows


def _split_blocks(raw) -> list[str]:
    """저장된 블록 값('B1,B5')을 코어블록 목록으로. 모르는 값·중복은 버리고 B1→B6 순으로 맞춘다.

    한 항목을 여러 블록에서 동시에 진행할 수 있어 값이 여러 개다. 비면 미지정이다.
    """
    got = {b.strip() for b in (raw or "").split(",")}
    return [b for b in CORE_BLOCKS if b in got]


def _clean_blocks(raw) -> str:
    """폼에서 온 블록 값을 저장형('B1,B5')으로. 하나도 못 알아보면 ''(미지정)."""
    return ",".join(_split_blocks(raw))


def _gantt_blocks(conn, areas, span_start: date, span_end: date,
                  show_hidden: bool = False) -> list[dict]:
    """블록(B1~B6·미지정)별 간트 행 목록. 그 블록으로 배정된 막대가 한 줄에 모두 들어간다.

    한 항목이 블록을 여러 개 가질 수 있어(여러 블록에서 동시에 진행) 같은 막대가 여러 줄에
    나온다. 하위 항목은 자기 블록이 없으면 상위 블록을 그대로 물려받는다.

    색은 영역 톤(tone), 진하기는 계층 단계(level·최상위가 가장 진함), 색조 비틀기(hue)는
    같은 영역 안에서 뿌리끼리 구분하려고 준다. 한 뿌리의 하위들은 뿌리와 같은 색조를 쓴다.
    상하위 관계는 색 진하기로만 나타내고, 자리는 겹치지 않게 위아래 칸(lane)으로 나눈다.
    left/width 는 보이는 기간 전체에 대한 퍼센트라 템플릿이 계산 없이 그린다.
    """
    total = (span_end - span_start).days + 1
    today = datetime.now(KST).date()
    tones = {a["id"]: a["tone"] for a in areas}
    names = {a["id"]: a["name"] for a in areas}
    # 영역 표시 순서(프로젝트·투자·학습…). 한 줄 안에서 이 순서대로 위에서 아래로 놓는다.
    orders = {a["id"]: i for i, a in enumerate(areas)}
    rows = _block_rows()
    children: dict[int | None, list] = {}
    for r in conn.execute(
        "SELECT id, area_id, parent_id, title, start_date, end_date, progress, "
        "       block_label, hidden, masked, sort_order FROM lt_item "
        "ORDER BY start_date, id"
    ):
        # 접어 둔 항목은 '숨긴 항목 보기'를 켰을 때만 끌어온다(하위도 함께 빠진다)
        if r["area_id"] in tones and (show_hidden or not r["hidden"]):
            children.setdefault(r["parent_id"], []).append(dict(r))

    def overlaps(it) -> bool:
        s, e = _parse_date(it["start_date"]), _parse_date(it["end_date"])
        if not s or not e:
            return False
        if s <= span_end and e >= span_start:
            return True
        return any(overlaps(c) for c in children.get(it["id"], []))

    def bar(it, blocks: list[str], level: int, hue: int, rank: int,
            path: tuple, root_id: int) -> dict:
        s = _parse_date(it["start_date"]) or span_start
        e = _parse_date(it["end_date"]) or s
        vs, ve = max(s, span_start), min(e, span_end)
        visible = vs <= ve
        row = dict(it)
        row["own_blocks"] = _split_blocks(it["block_label"])
        row["blocks"] = ",".join(blocks)
        row["vs"], row["ve"] = vs.isoformat(), ve.isoformat()   # 칸 나누기용(보이는 구간)
        row["level"] = min(level, MAX_LEVEL)
        row["level_label"] = LEVEL_LABELS[min(level, MAX_LEVEL)]
        row["hue"] = hue
        # 세로 순서는 뿌리 하나에 매기고 하위는 그 값을 그대로 물려받는다(묶음째 오르내린다).
        row["rank"] = rank
        # 상위 사슬(뿌리부터 바로 위까지)의 제목. 하위 막대가 어디서 내려왔는지 보여 준다.
        row["path"] = list(path)
        row["parent_title"] = path[-1] if path else ""
        row["root_id"] = root_id
        row["visible"] = visible
        row["left"] = round((vs - span_start).days / total * 100, 3) if visible else 0
        row["width"] = round(((ve - vs).days + 1) / total * 100, 3) if visible else 0
        row["clip_left"] = s < span_start
        row["clip_right"] = e > span_end
        row["range_label"] = f"{s.month}/{s.day}~{e.month}/{e.day}"
        row["has_children"] = bool(children.get(it["id"]))
        row["days"] = (e - s).days + 1
        row["past"] = e < today        # 종료일이 지난 항목은 화면에서 기본으로 접는다
        row["hidden"] = bool(it["hidden"])
        row["masked"] = bool(it["masked"])   # 주간·오늘에서만 뺀 항목(간트에는 그대로 그린다)
        row["tone"] = tones.get(it["area_id"], "blue")
        row["area_name"] = names.get(it["area_id"], "")
        row["area_order"] = orders.get(it["area_id"], 999)
        return row

    bars_by_block: dict[str, list] = {r["key"]: [] for r in rows}

    def walk(it, level: int, hue: int, rank: int, path: tuple, root_id: int):
        # 블록은 항목마다 제 것만 본다. 안 고르면 미지정 줄로 간다(상위를 따라가지 않는다).
        blocks = _split_blocks(it["block_label"]) or [""]
        b = bar(it, blocks, level, hue, rank, path, root_id)
        if b["visible"]:
            for k in blocks:
                bars_by_block[k].append({**b})   # 줄마다 칸(lane)이 달라 사본으로 담는다
        for c in children.get(it["id"], []):
            if overlaps(c):
                walk(c, level + 1, hue, rank, (*path, it["title"]), root_id)

    seen: dict[int, int] = {}       # 영역마다 뿌리를 몇 개 봤는지(색조를 돌려 쓰려고)
    for it in children.get(None, []):
        if it["area_id"] not in tones or not overlaps(it):
            continue
        n = seen.get(it["area_id"], 0)
        seen[it["area_id"]] = n + 1
        rank = it["sort_order"] if it["sort_order"] is not None else NO_RANK
        walk(it, 0, HUE_STEPS[n % len(HUE_STEPS)], rank, (), it["id"])

    # 키 이름은 'items'를 피한다(Jinja에서 dict.items 메서드와 겹친다).
    return [{**row,
             "lanes": _assign_lanes(bars_by_block[row["key"]]),
             "bars": bars_by_block[row["key"]]}
            for row in rows]


@router.post("/plan/item/add")
async def plan_item_add(request: Request):
    """간트 항목을 만든다. parent_id 를 주면 그 항목의 하위로 붙고 영역을 물려받는다."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    start = _parse_date(form.get("start"))
    end = _parse_date(form.get("end")) or start
    raw_parent = (form.get("parent_id") or "").strip()
    try:
        area_id = int_id(form.get("area_id"))
    except (TypeError, ValueError):
        area_id = 0
    if not title:
        return JSONResponse({"ok": False, "error": "제목을 입력하세요"}, status_code=400)
    if not start or not end:
        return JSONResponse({"ok": False, "error": "시작·종료 날짜가 필요합니다"},
                            status_code=400)
    if end < start:
        return JSONResponse({"ok": False, "error": "종료일이 시작일보다 빠릅니다"},
                            status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        parent_id = None
        if raw_parent:
            try:
                pid = int_id(raw_parent)
            except ValueError:
                return JSONResponse({"ok": False, "error": "상위 항목 값이 잘못됨"},
                                    status_code=400)
            prow = conn.execute(
                "SELECT id, area_id FROM lt_item WHERE id = ?", (pid,)
            ).fetchone()
            if not prow:
                return JSONResponse({"ok": False, "error": "상위 항목 없음"},
                                    status_code=404)
            parent_id, area_id = prow["id"], prow["area_id"]
        if not conn.execute(
            "SELECT 1 FROM lt_area WHERE id = ?", (area_id,)
        ).fetchone():
            return JSONResponse({"ok": False, "error": "영역 없음"}, status_code=404)
        cur = conn.execute(
            "INSERT INTO lt_item (area_id, parent_id, title, start_date, end_date, "
            "progress, block_label, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (area_id, parent_id, title, start.isoformat(), end.isoformat(),
             _clean_blocks(form.get("block")) or None, now),
        )
        new_id = cur.lastrowid
        _lt_rollup(conn, new_id)
    return JSONResponse({"ok": True, "id": new_id})


@router.post("/plan/item/update")
async def plan_item_update(request: Request):
    """간트 항목의 제목·기간·진척률·블록을 고친다(보낸 값만 바꾼다)."""
    form = await request.form()
    try:
        item_id = int_id(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    fields: dict = {}
    if (form.get("title") or "").strip():
        fields["title"] = form.get("title").strip()
    # 블록은 빈 값도 뜻이 있다('미지정'으로 되돌리기). 칸이 왔는지로만 판단한다.
    if form.get("block") is not None:
        fields["block_label"] = _clean_blocks(form.get("block")) or None
    if form.get("hidden") is not None:
        fields["hidden"] = 1 if (form.get("hidden") or "").strip() in ("1", "on") else 0
    if form.get("masked") is not None:
        fields["masked"] = 1 if (form.get("masked") or "").strip() in ("1", "on") else 0
    if form.get("start") is not None and (form.get("start") or "").strip():
        d = _parse_date(form.get("start"))
        if not d:
            return JSONResponse({"ok": False, "error": "시작일 형식"}, status_code=400)
        fields["start_date"] = d.isoformat()
    if form.get("end") is not None and (form.get("end") or "").strip():
        d = _parse_date(form.get("end"))
        if not d:
            return JSONResponse({"ok": False, "error": "종료일 형식"}, status_code=400)
        fields["end_date"] = d.isoformat()
    if form.get("progress") is not None and (form.get("progress") or "").strip():
        try:
            fields["progress"] = max(0, min(100, int(form.get("progress"))))
        except ValueError:
            return JSONResponse({"ok": False, "error": "진척률 형식"}, status_code=400)
    if not fields:
        return JSONResponse({"ok": False, "error": "바꿀 값 없음"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT start_date, end_date, block_label FROM lt_item WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        s = fields.get("start_date", row["start_date"])
        e = fields.get("end_date", row["end_date"])
        if e < s:
            return JSONResponse({"ok": False, "error": "종료일이 시작일보다 빠릅니다"},
                                status_code=400)
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE lt_item SET {sets}, updated_at = ? WHERE id = ?",
            (*fields.values(), now, item_id),
        )
        # 적어 넣은 기간은 하위가 있어도 그대로 둔다. 예전에는 하위를 모두 품도록
        # 되돌려 놔서 상위 항목은 날짜를 고쳐도 안 바뀌는 것처럼 보였다.
        # 대신 상위가 움직인 만큼 하위 사슬도 같이 민다.
        old_s, old_e = _parse_date(row["start_date"]), _parse_date(row["end_date"])
        new_s, new_e = _parse_date(s), _parse_date(e)
        if old_s and old_e and new_s and new_e:
            _lt_apply_delta(conn, item_id, (new_s - old_s).days, (new_e - old_e).days, now)
        # 상위를 어느 블록에 놓으면 하위 사슬도 같은 블록으로 따라 옮긴다. 실제로 바뀔 때만
        # 내려보내므로, 그 뒤에 하위를 따로 다른 블록으로 빼 두면 그대로 남는다.
        if "block_label" in fields and fields["block_label"] != row["block_label"]:
            kids = _lt_descendants(conn, item_id)
            if kids:
                ph = ",".join("?" * len(kids))
                conn.execute(
                    f"UPDATE lt_item SET block_label = ?, updated_at = ? "
                    f"WHERE id IN ({ph})",
                    (fields["block_label"], now, *kids),
                )
        _lt_rollup(conn, item_id)
    return JSONResponse({"ok": True})


@router.post("/plan/item/shift")
async def plan_item_shift(request: Request):
    """계획 막대를 끈 만큼(일 단위) 좌우로 옮긴다. 기간 길이는 그대로다.

    하위가 있으면 하위 사슬 전체를 같은 날수만큼 함께 민다(계획을 통째로 당기거나 미룬다).
    """
    form = await request.form()
    try:
        item_id = int_id(form.get("id"))
        days = int(form.get("days"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    if days == 0:
        return JSONResponse({"ok": True, "moved": 0})
    delta = timedelta(days=days)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone():
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        ids = [item_id, *_lt_descendants(conn, item_id)]
        ph = ",".join("?" * len(ids))
        for r in conn.execute(
            f"SELECT id, start_date, end_date FROM lt_item WHERE id IN ({ph})", ids
        ).fetchall():
            s, e = _parse_date(r["start_date"]), _parse_date(r["end_date"])
            if not s or not e:
                continue
            conn.execute(
                "UPDATE lt_item SET start_date = ?, end_date = ?, updated_at = ? "
                "WHERE id = ?",
                ((s + delta).isoformat(), (e + delta).isoformat(), now, r["id"]),
            )
        _lt_rollup(conn, item_id)
    return JSONResponse({"ok": True, "moved": days, "with_children": len(ids) - 1})


@router.post("/plan/item/resize")
async def plan_item_resize(request: Request):
    """막대의 한쪽 끝(edge=start|end)만 끈 만큼(일 단위) 늘리거나 줄인다.

    하위가 있으면 움직인 그 끝에 맞춰 하위 사슬의 같은 쪽 끝도 함께 민다.
    """
    form = await request.form()
    try:
        item_id = int_id(form.get("id"))
        days = int(form.get("days"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    edge = (form.get("edge") or "").strip()
    if edge not in ("start", "end"):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    if days == 0:
        return JSONResponse({"ok": True, "moved": 0})
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        s, e = _parse_date(row["start_date"]), _parse_date(row["end_date"])
        if not s or not e:
            return JSONResponse({"ok": False, "error": "기간 없음"}, status_code=400)
        if edge == "start":
            s = s + timedelta(days=days)
        else:
            e = e + timedelta(days=days)
        if e < s:
            return JSONResponse({"ok": False, "error": "기간이 뒤집힙니다"}, status_code=400)
        conn.execute(
            "UPDATE lt_item SET start_date = ?, end_date = ?, updated_at = ? WHERE id = ?",
            (s.isoformat(), e.isoformat(), now, item_id),
        )
        _lt_apply_delta(conn, item_id,
                        days if edge == "start" else 0,
                        days if edge == "end" else 0, now)
        _lt_rollup(conn, item_id)
    return JSONResponse({"ok": True})


@router.post("/plan/item/order")
async def plan_item_order(request: Request):
    """막대의 세로 순서를 바꾼다. 옮길 막대(id)를 기준 막대(peer) 위(before)나 아래(after)에 둔다.

    순서는 뿌리(최상위) 묶음 단위이고 같은 영역 안에서만 매긴다. 상위 아래에 하위가 붙는
    규칙과 영역끼리의 위아래(영역 관리 순서)는 그대로다. 한 번 손대면 그 영역의 뿌리 전부에
    0,1,2… 를 다시 매겨, 정한 것과 안 정한 것이 섞여 순서가 흔들리지 않게 한다.
    """
    form = await request.form()
    try:
        item_id = int_id(form.get("id"))
        peer_id = int_id(form.get("peer"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    place = (form.get("place") or "before").strip()
    if place not in ("before", "after"):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        me, peer = _lt_root(conn, item_id), _lt_root(conn, peer_id)
        if me == peer:      # 같은 묶음 안에서 끈 것. 묶음 안 순서는 계층이 정한다
            return JSONResponse({"ok": True, "changed": False})
        rows = {
            r["id"]: r["area_id"]
            for r in conn.execute(
                "SELECT id, area_id FROM lt_item WHERE id IN (?, ?)", (me, peer)
            )
        }
        if len(rows) < 2:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        if rows[me] != rows[peer]:
            return JSONResponse(
                {"ok": False, "error": "같은 영역 안에서만 순서를 바꿉니다"}, status_code=400
            )
        cur = [
            (r["id"], r["sort_order"])
            for r in conn.execute(
                "SELECT id, sort_order FROM lt_item WHERE area_id = ? AND parent_id IS NULL "
                "ORDER BY COALESCE(sort_order, ?), start_date, id",
                (rows[me], NO_RANK),
            )
        ]
        ids = [i for i, _ in cur]
        if me not in ids or peer not in ids:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        ids.remove(me)
        ids.insert(ids.index(peer) + (1 if place == "after" else 0), me)
        was = dict(cur)
        for n, rid in enumerate(ids):
            if was[rid] != n:       # 실제로 자리가 바뀐 것만 적는다
                conn.execute(
                    "UPDATE lt_item SET sort_order = ?, updated_at = ? WHERE id = ?",
                    (n, now, rid),
                )
    return JSONResponse({"ok": True, "changed": True})


@router.post("/plan/item/reparent")
async def plan_item_reparent(request: Request):
    """막대를 다른 막대의 하위로 넣거나(parent_id), 영역에 놓아 최상위로 뺀다(area_id).

    하위로 들어가면 상위의 기간·진척률이 자기 하위들로 다시 계산돼 상위 막대 안에 겹쳐 보인다.
    """
    form = await request.form()
    try:
        item_id = int_id(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
    raw_parent = (form.get("parent_id") or "").strip()
    raw_area = (form.get("area_id") or "").strip()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT parent_id, area_id FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        old_parent = row["parent_id"]
        kin = _lt_descendants(conn, item_id)
        if raw_parent:
            try:
                pid = int_id(raw_parent)
            except ValueError:
                return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
            if pid == item_id or pid in kin:
                return JSONResponse(
                    {"ok": False, "error": "자기 자신이나 자기 하위로는 넣을 수 없습니다"},
                    status_code=400,
                )
            prow = conn.execute(
                "SELECT area_id FROM lt_item WHERE id = ?", (pid,)
            ).fetchone()
            if not prow:
                return JSONResponse({"ok": False, "error": "상위 항목 없음"},
                                    status_code=404)
            new_parent, new_area = pid, prow["area_id"]
        else:
            try:
                new_area = int_id(raw_area)
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
            if not conn.execute(
                "SELECT 1 FROM lt_area WHERE id = ?", (new_area,)
            ).fetchone():
                return JSONResponse({"ok": False, "error": "영역 없음"}, status_code=404)
            new_parent = None
        if new_parent == old_parent and new_area == row["area_id"]:
            return JSONResponse({"ok": True, "changed": False})
        conn.execute(
            "UPDATE lt_item SET parent_id = ?, area_id = ?, updated_at = ? WHERE id = ?",
            (new_parent, new_area, now, item_id),
        )
        if kin:  # 하위 사슬도 같은 영역으로 함께 옮긴다(영역은 행이라 섞이면 안 된다)
            ph = ",".join("?" * len(kin))
            conn.execute(
                f"UPDATE lt_item SET area_id = ?, updated_at = ? WHERE id IN ({ph})",
                (new_area, now, *kin),
            )
        _lt_rollup_parent(conn, old_parent)   # 하나 빠진 옛 상위
        _lt_rollup(conn, item_id)             # 하나 들어온 새 상위
    return JSONResponse({"ok": True, "changed": True})


@router.post("/plan/item/delete")
async def plan_item_delete(request: Request):
    """간트 항목을 지운다. 하위 항목도 함께 지워지고 상위 기간은 다시 계산된다."""
    form = await request.form()
    try:
        item_id = int_id(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT parent_id FROM lt_item WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"ok": True})
        conn.execute("DELETE FROM lt_item WHERE id = ?", (item_id,))
        if row["parent_id"]:
            # 지워진 항목의 형제를 기준으로 상위 사슬을 다시 계산한다.
            sib = conn.execute(
                "SELECT id FROM lt_item WHERE parent_id = ? LIMIT 1", (row["parent_id"],)
            ).fetchone()
            if sib:
                _lt_rollup(conn, sib["id"])
    return JSONResponse({"ok": True})


# -- 장기플랜 ---------------------------------------------------------------


@router.get("/plan")
def plan_view(request: Request, level: str = "week", anchor: str = "", focus: str = "",
              show_hidden: str = ""):
    # 기본은 주 단위(이번 주가 두 번째 칸). 월·분기·연은 축소로 간다.
    if level not in PLAN_LEVELS:
        level = "week"
    a = _parse_anchor(anchor)
    # focus = 방금 끌어 옮긴 항목. 그 항목이 보이는 기간 밖으로 나갔으면 화면을 그쪽으로
    # 옮겨 준다. 안 그러면 끌던 막대가 그냥 사라진 것처럼 보인다.
    focus_id = opt_id(focus) or 0
    if focus_id:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT start_date, end_date FROM lt_item WHERE id = ?", (focus_id,)
            ).fetchone()
        fs = _parse_date(r["start_date"]) if r else None
        fe = _parse_date(r["end_date"]) if r else None
        if fs and fe:
            seen_cols, _hd = _plan_columns(level, a)
            if not (fs <= seen_cols[-1]["end"] and fe >= seen_cols[0]["start"]):
                a = fs
    cols, header = _plan_columns(level, a)
    span_start, span_end = cols[0]["start"], cols[-1]["end"]
    with get_conn() as conn:
        areas = [
            dict(x)
            for x in conn.execute(
                "SELECT id, name, tone FROM lt_area WHERE is_active = 1 "
                "ORDER BY display_order"
            )
        ]
        all_areas = conn.execute(
            "SELECT id, name, is_active, tone FROM lt_area "
            "ORDER BY is_active DESC, display_order"
        ).fetchall()
        seen_hidden = show_hidden == "1"
        gantt = _gantt_blocks(conn, areas, span_start, span_end, seen_hidden)
        # 접어 둔 항목이 몇 개인지(보이는 기간에 걸친 것만). 0이면 안내도 안 내보낸다.
        hidden_count = conn.execute(
            "SELECT COUNT(*) FROM lt_item i JOIN lt_area a ON a.id = i.area_id "
            "WHERE i.hidden = 1 AND a.is_active = 1 "
            "AND i.start_date <= ? AND i.end_date >= ?",
            (span_end.isoformat(), span_start.isoformat()),
        ).fetchone()[0]
    # 지난 항목(종료일이 오늘 이전)은 기본으로 보여 주고, 체크박스를 켤 때만 숨긴다.
    # 0이면 체크박스도 내보내지 않는다.
    past_count = sum(1 for row in gantt for b in row["bars"] if b["past"])
    prev_anchor, next_anchor = _plan_nav(level, a)
    order = list(PLAN_LEVELS)
    i = order.index(level)
    # 항목 추가 기본 시작일. 오늘이 보이는 기간 안이면 오늘, 아니면 그 기간 첫날.
    today = datetime.now(KST).date()
    default_start = today if span_start <= today <= span_end else span_start
    return templates.TemplateResponse(
        "plan.html",
        {
            "request": request,
            "level": level,
            "level_label": PLAN_LEVEL_LABELS[level],
            "anchor": a.strftime("%Y-%m-%d"),
            "columns": cols,
            "header": header,
            "areas": areas,
            "all_areas": [dict(x) for x in all_areas],
            "breadcrumb": _plan_breadcrumb(level, a),
            "prev_anchor": prev_anchor,
            "next_anchor": next_anchor,
            "zoom_in": order[i + 1] if i + 1 < len(order) else None,
            "zoom_out": order[i - 1] if i - 1 >= 0 else None,
            "levels": PLAN_LEVELS,
            "level_labels": PLAN_LEVEL_LABELS,
            "gantt": gantt,
            "focus_id": focus_id,
            "hidden_count": hidden_count,
            "show_hidden": seen_hidden,
            "core_blocks": CORE_BLOCKS,
            "tones": TONES,
            "past_count": past_count,
            "span_start": span_start.strftime("%Y-%m-%d"),
            "span_end": span_end.strftime("%Y-%m-%d"),
            # 화면 가로폭을 이 날수로 나눠 픽셀↔날짜를 환산한다(막대를 끈 만큼만 움직이게).
            "span_days": (span_end - span_start).days + 1,
            # 오늘 세로선 자리(보이는 기간에 대한 %). 기간 밖이면 None이라 선을 안 그린다.
            "today_pct": (round((today - span_start).days
                                / ((span_end - span_start).days + 1) * 100, 3)
                          if span_start <= today <= span_end else None),
            "default_start": default_start.strftime("%Y-%m-%d"),
        },
    )


@router.post("/plan/area/add")
async def plan_area_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM lt_area WHERE name = ?", (name,)).fetchone()
        if row:  # 같은 이름이 있으면(숨김 포함) 다시 활성화
            conn.execute("UPDATE lt_area SET is_active = 1 WHERE id = ?", (row["id"],))
            cid = row["id"]
        else:
            order = conn.execute(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM lt_area"
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO lt_area (name, display_order, is_active, tone) "
                "VALUES (?, ?, 1, ?)",
                (name, order, area_tone(order)),
            )
            cid = cur.lastrowid
    return JSONResponse({"ok": True, "id": cid, "name": name})


@router.post("/plan/area/update")
async def plan_area_update(request: Request):
    """영역 이름이나 막대 색(tone)을 바꾼다. 보낸 값만 고친다."""
    form = await request.form()
    try:
        cid = int_id(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    fields: dict = {}
    if (form.get("name") or "").strip():
        fields["name"] = form.get("name").strip()
    tone = (form.get("tone") or "").strip()
    if tone:
        if tone not in TONE_KEYS:
            return JSONResponse({"ok": False, "error": "모르는 색"}, status_code=400)
        fields["tone"] = tone
    if not fields:
        return JSONResponse({"ok": False}, status_code=400)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE lt_area SET {sets} WHERE id = ?", (*fields.values(), cid))
    return JSONResponse({"ok": True})


@router.post("/plan/area/move")
async def plan_area_move(request: Request):
    form = await request.form()
    try:
        cid = int_id(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    direction = form.get("dir")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, display_order FROM lt_area WHERE is_active = 1 "
            "ORDER BY display_order"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if cid not in ids:
            return JSONResponse({"ok": False}, status_code=404)
        i = ids.index(cid)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(rows):
            a, b = rows[i], rows[j]
            conn.execute("UPDATE lt_area SET display_order = ? WHERE id = ?",
                         (b["display_order"], a["id"]))
            conn.execute("UPDATE lt_area SET display_order = ? WHERE id = ?",
                         (a["display_order"], b["id"]))
    return JSONResponse({"ok": True})


@router.post("/plan/area/delete")
async def plan_area_delete(request: Request):
    """영역을 숨김 처리(소프트 삭제)한다. 그 영역의 계획 내용은 보존된다."""
    form = await request.form()
    try:
        cid = int_id(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE lt_area SET is_active = 0 WHERE id = ?", (cid,))
    return JSONResponse({"ok": True})
