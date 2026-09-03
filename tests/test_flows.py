# 통합 테스트. 화면에서 실제로 하는 일(저장·이동·삭제)을 HTTP 로 그대로 밟아 DB 까지 확인한다.
import datetime

import pytest

import app.db as db
from app.common import today_str, week_start

TODAY = today_str()
MONDAY = week_start(datetime.date.today()).strftime("%Y-%m-%d")


def _rows(sql, params=()):
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _one(sql, params=()):
    rows = _rows(sql, params)
    return rows[0] if rows else None


# -- 하루 골격 --------------------------------------------------------------


def test_오늘_화면이_하루_골격을_만든다(client):
    assert client.get("/today").status_code == 200
    blocks = _rows("SELECT * FROM blocks WHERE date = ? ORDER BY block_order", (TODAY,))
    slots = _rows("SELECT * FROM slots WHERE date = ? ORDER BY slot_index", (TODAY,))
    assert len(blocks) == 8
    assert [b["block_label"] for b in blocks][:2] == ["B1", "B2"]
    assert len(slots) == 31, f"기본 시간표의 30분 슬롯 개수가 달라졌다: {len(slots)}"
    assert [s["slot_index"] for s in slots] == list(range(len(slots)))


def test_골격을_두_번_열어도_중복되지_않는다(client):
    client.get("/today")
    n1 = len(_rows("SELECT id FROM blocks WHERE date = ?", (TODAY,)))
    client.get("/today")
    client.get(f"/day/{TODAY}")
    n2 = len(_rows("SELECT id FROM blocks WHERE date = ?", (TODAY,)))
    assert n1 == n2 == 8


def test_점심_저녁_블록은_기타_구분으로_시드된다(client):
    client.get("/today")
    etc = _one("SELECT id FROM categories WHERE name = '기타'")
    for label in ("점심", "저녁"):
        row = _one("SELECT category_id FROM blocks WHERE date = ? AND block_label = ?",
                   (TODAY, label))
        assert row["category_id"] == etc["id"], f"{label} 블록 구분이 기타가 아니다"


# -- 잘못된 날짜가 주소에 들어왔을 때 ------------------------------------------

BAD_DATES = ["invalid", "2026-13-45", "2026-02-30", "26-1-1", "0", "-1", "' OR 1=1--"]


@pytest.mark.parametrize("bad", BAD_DATES)
def test_잘못된_날짜의_오늘_화면은_오늘로_보낸다(client, bad):
    res = client.get(f"/day/{bad}", follow_redirects=False)
    assert res.status_code in (302, 303, 307), res.status_code
    assert res.headers["location"] == "/today"
    assert client.get(f"/day/{bad}").status_code == 200


@pytest.mark.parametrize("bad", BAD_DATES)
def test_잘못된_날짜의_주간_화면은_이번_주로_보낸다(client, bad):
    res = client.get(f"/week/{bad}", follow_redirects=False)
    assert res.status_code in (302, 303, 307), res.status_code
    assert res.headers["location"] == "/week"
    assert client.get(f"/week/{bad}").status_code == 200


@pytest.mark.parametrize("bad", BAD_DATES)
def test_잘못된_날짜로는_저장도_조회도_400(client, bad):
    assert client.get(f"/api/day/{bad}").status_code == 400
    assert client.post(f"/save/day/{bad}", data={"memo": "x"}).status_code == 400
    assert client.post(f"/week/save/{bad}", data={"memo": "x"}).status_code == 400


def test_잘못된_날짜로_저장하면_그_날짜_행이_생기지_않는다(client):
    """예전에는 ensure_day_skeleton 이 먼저 돌아 쓰레기 날짜의 블록이 만들어졌다."""
    client.get("/today")
    before = _one("SELECT COUNT(*) AS c FROM blocks")["c"]
    for bad in BAD_DATES:
        client.post(f"/save/day/{bad}", data={"memo": "x"})
        client.get(f"/api/day/{bad}")
    assert _one("SELECT COUNT(*) AS c FROM blocks")["c"] == before
    이상한날짜 = _rows("SELECT DISTINCT date FROM blocks WHERE date NOT GLOB "
                  "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'")
    assert 이상한날짜 == [], 이상한날짜


# -- 주소의 id 가 범위를 벗어났을 때 --------------------------------------------

ID_ROUTES = ["/slot/done/{}", "/inbox/done/{}", "/inbox/delete/{}",
             "/reflect/sync/{}", "/reflect/update/{}", "/reflect/delete/{}",
             "/reflect/review-note/{}"]


@pytest.mark.parametrize("path", ID_ROUTES)
@pytest.mark.parametrize("bad", [
    "99999999999999999999",                    # SQLite 64비트 범위 초과
    "9223372036854775808",                     # 딱 한 칸 초과
    "0", "-1", "-9999999999999999999999",
])
def test_범위_밖의_id는_422로_거절한다(client, path, bad):
    res = client.post(path.format(bad), data={})
    assert res.status_code == 422, f"{path.format(bad)} → {res.status_code}"


@pytest.mark.parametrize("path", ID_ROUTES)
def test_범위_안이지만_없는_id는_500이_아니다(client, path):
    """SQLite 최대값은 정상 범위다. 없는 행일 뿐이라 조용히 넘어가야 한다."""
    res = client.post(path.format("9223372036854775807"), data={})
    assert res.status_code != 500, res.status_code


# -- 오늘 탭 저장 ------------------------------------------------------------


def test_슬롯_do_저장이_왕복한다(client):
    client.get("/today")
    slot = _one("SELECT id FROM slots WHERE date = ? ORDER BY slot_index LIMIT 1", (TODAY,))
    res = client.post("/save/field", data={
        "entity": "slot", "id": slot["id"], "field": "do_text", "value": "테스트 할 일",
    })
    assert res.status_code == 200, res.text
    assert _one("SELECT do_text FROM slots WHERE id = ?", (slot["id"],))["do_text"] == "테스트 할 일"
    assert "테스트 할 일" in client.get("/today").text


def test_슬롯_완료_토글(client):
    client.get("/today")
    slot = _one("SELECT id FROM slots WHERE date = ? LIMIT 1", (TODAY,))
    client.post(f"/slot/done/{slot['id']}", data={"done": "1"})
    assert _one("SELECT done FROM slots WHERE id = ?", (slot["id"],))["done"] == 1
    client.post(f"/slot/done/{slot['id']}", data={"done": "0"})
    assert _one("SELECT done FROM slots WHERE id = ?", (slot["id"],))["done"] == 0


def test_허용되지_않은_필드는_거절한다(client):
    """f-string 으로 컬럼명을 만들기 때문에 이 검증이 뚫리면 SQL 이 조작된다."""
    client.get("/today")
    slot = _one("SELECT id FROM slots WHERE date = ? LIMIT 1", (TODAY,))
    for bad in ("id", "date", "do_text = 'x' --", "done; DROP TABLE slots"):
        res = client.post("/save/field", data={
            "entity": "slot", "id": slot["id"], "field": bad, "value": "x"})
        assert res.status_code == 400, f"{bad!r} 이 통과했다"
    assert _one("SELECT COUNT(*) AS c FROM slots")["c"] > 0, "slots 테이블이 사라졌다"


def test_블록_구분_저장과_상속(client):
    """슬롯 구분이 비면 블록 구분을 상속한다. 집계가 이 규칙에 기대고 있다."""
    client.get("/today")
    cat = _one("SELECT id FROM categories WHERE name = '업무'")
    block = _one("SELECT id FROM blocks WHERE date = ? AND block_label = 'B1'", (TODAY,))
    client.post("/save/field", data={
        "entity": "block", "id": block["id"], "field": "bcat", "value": cat["id"]})
    row = _one(
        "SELECT COALESCE(s.category_id, b.category_id) AS eff FROM slots s "
        "JOIN blocks b ON b.id = s.block_id WHERE s.block_id = ? LIMIT 1",
        (block["id"],))
    assert row["eff"] == cat["id"]


def test_하루_저장_왕복(client):
    client.get("/today")
    res = client.post(f"/save/day/{TODAY}", data={
        "vow": "오늘의 다짐", "memo": "메모", "day_review": "하루 평가",
        "dplan1": "달성1", "dplan2": "달성2", "dplan3": "달성3",
    })
    assert res.status_code in (200, 303), res.text
    meta = _one("SELECT * FROM daily_meta WHERE date = ?", (TODAY,))
    assert meta is not None, "daily_meta 가 저장되지 않았다"


def test_내일_목표_저장(client):
    client.get("/today")
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    res = client.post("/meta/tomorrow-goal", data={"date": tomorrow, "text": "내일 할 것"})
    assert res.status_code == 200, res.text


# -- 블록 이월(내일로) --------------------------------------------------------


TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def _블록(라벨, 날짜=None):
    return _one("SELECT id, plan_text FROM blocks WHERE date = ? AND block_label = ?",
                (날짜 or TODAY, 라벨))


def _슬롯들(라벨, 날짜=None):
    return _rows("SELECT s.id, s.start_time, s.do_text, s.is_routine FROM slots s "
                 "JOIN blocks b ON b.id = s.block_id "
                 "WHERE b.date = ? AND b.block_label = ? ORDER BY s.slot_index",
                 (날짜 or TODAY, 라벨))


def _DO적기(client, 라벨, 자리, 글, 날짜=None):
    """그 블록의 n번째 슬롯 DO 칸에 적는다(화면의 자동저장과 같은 경로)."""
    s = _슬롯들(라벨, 날짜)[자리]
    client.post("/save/field", data={"entity": "slot", "id": s["id"],
                                     "field": "do_text", "value": 글})
    return s


def test_슬롯_DO를_내일_같은_시간_칸으로_넘긴다(client):
    """PLAN 칸을 안 쓰고 30분 칸에만 적는 것이 실제 사용 방식이다."""
    client.get("/today")
    s0 = _DO적기(client, "B1", 0, "첫 칸 할 일")
    s2 = _DO적기(client, "B1", 2, "셋째 칸 할 일")
    b = _블록("B1")

    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["moved"] == 2
    assert res.json()["skipped"] == 0

    내일 = {s["start_time"]: s["do_text"] for s in _슬롯들("B1", TOMORROW)}
    assert 내일[s0["start_time"]] == "첫 칸 할 일"
    assert 내일[s2["start_time"]] == "셋째 칸 할 일"


def test_이월은_오늘_칸을_지우지_않는다(client):
    client.get("/today")
    s = _DO적기(client, "B1", 0, "남아야 할 일")
    b = _블록("B1")
    client.post("/block/rollover", data={"block_id": b["id"]})
    assert _one("SELECT do_text FROM slots WHERE id = ?", (s["id"],))["do_text"] == "남아야 할 일"


def test_아직_저장_안_된_DO도_넘어간다(client):
    """적자마자 누르면 자동저장이 아직 서버에 안 닿는다. 화면 값을 함께 보낸다."""
    client.get("/today")
    b = _블록("B1")
    s = _슬롯들("B1")[0]
    assert (s["do_text"] or "") == ""
    res = client.post("/block/rollover",
                      data={"block_id": b["id"], f"do_{s['id']}": "방금 적은 일"})
    assert res.status_code == 200, res.text
    assert res.json()["moved"] == 1
    내일 = {x["start_time"]: x["do_text"] for x in _슬롯들("B1", TOMORROW)}
    assert 내일[s["start_time"]] == "방금 적은 일"


def test_화면에서_지운_칸은_안_넘어간다(client):
    client.get("/today")
    s = _DO적기(client, "B1", 0, "지울 일")
    b = _블록("B1")
    res = client.post("/block/rollover",
                      data={"block_id": b["id"], f"do_{s['id']}": "  "})
    assert res.status_code == 400
    assert res.json()["error"] == "empty"
    assert _one("SELECT id FROM blocks WHERE date = ?", (TOMORROW,)) is None


def test_PLAN과_DO를_함께_넘긴다(client):
    client.get("/today")
    _DO적기(client, "B1", 0, "칸에 적은 일")
    b = _블록("B1")
    client.post("/save/field", data={"entity": "block", "id": b["id"],
                                     "field": "plan_text", "value": "블록 계획"})
    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.json()["moved"] == 1
    assert res.json()["plan"] is True
    assert _블록("B1", TOMORROW)["plan_text"] == "블록 계획"
    assert any((s["do_text"] or "") == "칸에 적은 일" for s in _슬롯들("B1", TOMORROW))


def test_넘길_것이_하나도_없으면_거절한다(client):
    client.get("/today")
    b = _블록("B1")
    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.status_code == 400
    assert res.json()["error"] == "empty"
    # 거절할 때는 내일 골격도 만들지 않는다(열어 본 적 없는 날이 생기면 안 된다).
    assert _one("SELECT id FROM blocks WHERE date = ?", (TOMORROW,)) is None


def test_내일_같은_칸이_차_있어도_오늘_것으로_덮어쓴다(client):
    """이월은 '어제 하려던 것을 그 자리에 다시 세운다'는 뜻이다. 밀어내면 시간표가 어긋난다."""
    client.get("/today")
    client.get(f"/day/{TOMORROW}")
    첫칸 = _슬롯들("B1", TOMORROW)[0]
    client.post("/save/field", data={"entity": "slot", "id": 첫칸["id"],
                                     "field": "do_text", "value": "내일 먼저 잡힌 일"})
    s = _DO적기(client, "B1", 0, "덮어쓸 일")
    b = _블록("B1")

    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.json()["moved"] == 1
    assert res.json()["skipped"] == 0
    내일 = _슬롯들("B1", TOMORROW)
    assert 내일[0]["do_text"] == "덮어쓸 일"
    assert s["start_time"] == 내일[0]["start_time"]
    # 오늘이 안 적은 칸은 내일 것이 그대로 남는다
    assert all(not (x["do_text"] or "") for x in 내일[1:])


def test_내일_블록이_꽉_차_있어도_다_덮어쓴다(client):
    client.get("/today")
    client.get(f"/day/{TOMORROW}")
    for x in _슬롯들("B1", TOMORROW):
        client.post("/save/field", data={"entity": "slot", "id": x["id"],
                                         "field": "do_text", "value": "이미 참"})
    _DO적기(client, "B1", 0, "넘길 일 하나")
    _DO적기(client, "B1", 1, "넘길 일 둘")
    b = _블록("B1")

    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["moved"] == 2
    assert res.json()["skipped"] == 0
    내일 = _슬롯들("B1", TOMORROW)
    assert [x["do_text"] for x in 내일[:2]] == ["넘길 일 하나", "넘길 일 둘"]
    assert all(x["do_text"] == "이미 참" for x in 내일[2:])


def test_내일_블록이_더_짧으면_못_넘긴_개수를_알려_준다(client):
    """요일마다 블록 길이가 다를 수 있다. 같은 시각도 같은 순번도 없으면 그것만 건너뛴다."""
    client.get("/today")
    client.get(f"/day/{TOMORROW}")
    내일칸 = _슬롯들("B1", TOMORROW)
    with db.get_conn() as conn:      # 내일 B1 을 한 칸짜리로 줄인다
        conn.execute("DELETE FROM slots WHERE id IN ({})".format(
            ",".join(str(x["id"]) for x in 내일칸[1:])))
    _DO적기(client, "B1", 0, "넘길 일 하나")
    _DO적기(client, "B1", 1, "넘길 일 둘")
    b = _블록("B1")

    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["moved"] == 1
    assert res.json()["skipped"] == 1
    assert [x["do_text"] for x in _슬롯들("B1", TOMORROW)] == ["넘길 일 하나"]


def test_고정_할일_칸은_안_넘긴다(client):
    """내일도 템플릿이 다시 채우므로, 함께 넘기면 같은 것이 두 칸에 생긴다."""
    client.get("/today")
    s = _슬롯들("B1")[0]
    with db.get_conn() as conn:
        conn.execute("UPDATE slots SET do_text = '고정 할일', is_routine = 1 WHERE id = ?",
                     (s["id"],))
    b = _블록("B1")
    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.status_code == 400, "고정 할일만 있는데 넘겼다"
    assert res.json()["error"] == "empty"


def test_이월은_같은_라벨_블록으로만_간다(client):
    client.get("/today")
    _DO적기(client, "B4", 0, "B4 일")
    b = _블록("B4")
    client.post("/block/rollover", data={"block_id": b["id"]})
    assert any((x["do_text"] or "") == "B4 일" for x in _슬롯들("B4", TOMORROW))
    assert all(not (x["do_text"] or "") for x in _슬롯들("B1", TOMORROW))


def test_내일_골격이_없어도_이월된다(client):
    """내일을 아직 한 번도 안 열어 본 상태. 서버가 골격을 만들어 두고 넣어야 한다."""
    client.get("/today")
    assert _one("SELECT id FROM blocks WHERE date = ?", (TOMORROW,)) is None
    _DO적기(client, "B2", 0, "내일 것")
    b = _블록("B2")
    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.status_code == 200, res.text
    assert any((x["do_text"] or "") == "내일 것" for x in _슬롯들("B2", TOMORROW))


def test_이월은_없는_블록이면_404(client):
    client.get("/today")
    res = client.post("/block/rollover", data={"block_id": "999999", "plan": "x"})
    assert res.status_code == 404


def test_두_번_이월해도_같은_칸_하나로_남는다(client):
    """덮어쓰기라서 두 번 눌러도 같은 글이 두 칸에 쌓이지 않는다."""
    client.get("/today")
    s = _DO적기(client, "B1", 0, "같은 일")
    b = _블록("B1")
    client.post("/block/rollover", data={"block_id": b["id"]})
    client.post("/block/rollover", data={"block_id": b["id"]})
    내일 = [x["do_text"] for x in _슬롯들("B1", TOMORROW) if (x["do_text"] or "")]
    assert 내일 == ["같은 일"], f"같은 글이 여러 칸에 쌓였다: {내일}"
    assert s is not None


def test_PLAN은_이어_붙지_않고_갈아끼운다(client):
    client.get("/today")
    client.get(f"/day/{TOMORROW}")
    내일블록 = _블록("B1", TOMORROW)
    client.post("/save/field", data={"entity": "block", "id": 내일블록["id"],
                                     "field": "plan_text", "value": "내일 먼저 적은 계획"})
    b = _블록("B1")
    client.post("/save/field", data={"entity": "block", "id": b["id"],
                                     "field": "plan_text", "value": "오늘 못 한 계획"})

    res = client.post("/block/rollover", data={"block_id": b["id"]})
    assert res.status_code == 200, res.text
    assert _블록("B1", TOMORROW)["plan_text"] == "오늘 못 한 계획"


def test_이월_응답이_옮겨_갈_자리를_알려_준다(client):
    """화면이 그 자리(/day/내일#blk-순번)로 곧장 넘어가려면 블록 순번이 필요하다."""
    client.get("/today")
    _DO적기(client, "B4", 0, "B4 일")
    b = _블록("B4")
    res = client.post("/block/rollover", data={"block_id": b["id"]})
    d = res.json()
    assert d["date"] == TOMORROW
    assert d["label"] == "B4"
    자리 = _one("SELECT block_order FROM blocks WHERE date = ? AND block_label = ?",
               (TOMORROW, "B4"))
    assert d["block_order"] == 자리["block_order"]


# -- 수집함 ------------------------------------------------------------------


def test_수집함_추가_배정_완료_삭제(client):
    client.get("/today")
    res = client.post("/inbox/add", data={"text": "수집 항목"})
    assert res.status_code == 200
    item_id = res.json().get("id")
    assert item_id, res.text

    block = _one("SELECT id FROM blocks WHERE date = ? AND block_label = 'B2'", (TODAY,))
    res = client.post("/inbox/assign", data={"item_id": item_id, "block_id": block["id"]})
    assert res.status_code == 200, res.text
    assert "수집 항목" in (
        _one("SELECT COALESCE(plan_text,'') AS p FROM blocks WHERE id = ?", (block["id"],))["p"]
    )

    client.post(f"/inbox/done/{item_id}", data={})
    client.post(f"/inbox/delete/{item_id}", data={})
    assert _one("SELECT id FROM inbox WHERE id = ?", (item_id,)) is None


def test_빈_수집함_항목은_거절(client):
    client.get("/today")
    res = client.post("/inbox/add", data={"text": "   "})
    assert res.status_code in (400, 422), res.status_code


# -- 주간 -------------------------------------------------------------------


def test_주간_저장_왕복(client):
    assert client.get("/week").status_code == 200
    res = client.post(f"/week/save/{MONDAY}", data={
        "vow": "주간 다짐", "memo": "주간 메모",
        "theme_B1": "B1 테마", "theme_B2": "B2 테마",
        "appointments": "약속",
    })
    assert res.status_code in (200, 303), res.text
    themes = _rows("SELECT block_label, theme_text FROM weekly_block_themes "
                   "WHERE week_start = ?", (MONDAY,))
    assert {t["block_label"] for t in themes} >= {"B1", "B2"}


def test_주간_블록_테마가_오늘_블록_이름으로_상속된다(client):
    client.get("/week")
    client.post(f"/week/save/{MONDAY}", data={"theme_B1": "이번주 B1"})
    html = client.get(f"/day/{MONDAY}").text
    assert "이번주 B1" in html, "주간 테마가 그 주의 오늘 화면에 안 보인다"


# -- 장기(간트) --------------------------------------------------------------


def _add_item(client, title, start, end, area_id=None, parent_id=""):
    if area_id is None:
        area_id = _one("SELECT id FROM lt_area ORDER BY display_order LIMIT 1")["id"]
    res = client.post("/plan/item/add", data={
        "area_id": area_id, "title": title, "start": start, "end": end,
        "parent_id": parent_id, "block": "B1"})
    assert res.status_code == 200, res.text
    return res.json().get("id")


def test_장기_항목_추가_수정_삭제(client):
    client.get("/plan")
    iid = _add_item(client, "항목 A", "2026-08-01", "2026-08-31")
    assert iid
    client.post("/plan/item/update", data={
        "id": iid, "title": "항목 A2", "start": "2026-08-05", "end": "2026-08-20",
        "progress": "50", "block": "B2", "hidden": "0", "masked": "0"})
    row = _one("SELECT * FROM lt_item WHERE id = ?", (iid,))
    assert row["title"] == "항목 A2"
    assert row["start_date"] == "2026-08-05"
    assert row["progress"] == 50
    client.post("/plan/item/delete", data={"id": iid})
    assert _one("SELECT id FROM lt_item WHERE id = ?", (iid,)) is None


def test_장기_한_칸씩_따로_저장된다(client):
    """편집칸 자동저장은 손 뗀 칸 하나만 보낸다. 나머지 값이 딸려 오지 않아도 지켜져야 한다."""
    client.get("/plan")
    iid = _add_item(client, "원래 이름", "2026-08-01", "2026-08-31")
    보낼것 = [
        ({"title": "새 이름"}, "title", "새 이름"),
        ({"start": "2026-08-05"}, "start_date", "2026-08-05"),
        ({"end": "2026-09-10"}, "end_date", "2026-09-10"),
        ({"progress": "40"}, "progress", 40),
        ({"block": "B3,B5"}, "block_label", "B3,B5"),
        ({"hidden": "1"}, "hidden", 1),
        ({"masked": "1"}, "masked", 1),
    ]
    for data, 칸, 기대 in 보낼것:
        res = client.post("/plan/item/update", data=dict(data, id=iid))
        assert res.status_code == 200, f"{data} → {res.text}"
        row = _one("SELECT * FROM lt_item WHERE id = ?", (iid,))
        assert row[칸] == 기대, f"{data} 를 혼자 보냈더니 {칸} 이 {row[칸]}"
    # 한 칸씩 보내는 동안 다른 칸이 지워지지 않았다
    row = _one("SELECT * FROM lt_item WHERE id = ?", (iid,))
    assert row["title"] == "새 이름" and row["start_date"] == "2026-08-05"


def test_장기_덜_친_날짜는_안_보낸다(client):
    """한 칸 날짜 입력은 8자리를 다 쳐야 값이 선다. 빈 값이 와도 기간이 지워지면 안 된다."""
    client.get("/plan")
    iid = _add_item(client, "기간 지키기", "2026-08-01", "2026-08-31")
    client.post("/plan/item/update", data={"id": iid, "start": ""})
    row = _one("SELECT start_date, end_date FROM lt_item WHERE id = ?", (iid,))
    assert (row["start_date"], row["end_date"]) == ("2026-08-01", "2026-08-31")


def test_장기_항목_하루_이동과_리사이즈(client):
    client.get("/plan")
    iid = _add_item(client, "이동", "2026-08-10", "2026-08-20")
    client.post("/plan/item/shift", data={"id": iid, "days": "3"})
    row = _one("SELECT start_date, end_date FROM lt_item WHERE id = ?", (iid,))
    assert (row["start_date"], row["end_date"]) == ("2026-08-13", "2026-08-23")
    client.post("/plan/item/resize", data={"id": iid, "edge": "end", "days": "2"})
    row = _one("SELECT start_date, end_date FROM lt_item WHERE id = ?", (iid,))
    assert row["end_date"] == "2026-08-25"


def test_리사이즈가_기간을_뒤집지_못한다(client):
    client.get("/plan")
    iid = _add_item(client, "뒤집기", "2026-08-10", "2026-08-12")
    client.post("/plan/item/resize", data={"id": iid, "edge": "end", "days": "-99"})
    row = _one("SELECT start_date, end_date FROM lt_item WHERE id = ?", (iid,))
    assert row["start_date"] <= row["end_date"], (
        f"시작이 끝보다 뒤가 됐다: {row['start_date']} ~ {row['end_date']}"
    )


def test_자기_자신을_상위로_넣을_수_없다(client):
    client.get("/plan")
    iid = _add_item(client, "자기참조", "2026-08-01", "2026-08-31")
    res = client.post("/plan/item/reparent", data={"id": iid, "parent_id": iid})
    assert res.status_code == 400
    assert _one("SELECT parent_id FROM lt_item WHERE id = ?", (iid,))["parent_id"] is None


def test_자기_하위를_상위로_넣을_수_없다(client):
    """이걸 허용하면 순환이 생겨 장기 탭에서 막대가 통째로 사라진다."""
    client.get("/plan")
    parent = _add_item(client, "상위", "2026-08-01", "2026-08-31")
    child = _add_item(client, "하위", "2026-08-05", "2026-08-10", parent_id=parent)
    assert _one("SELECT parent_id FROM lt_item WHERE id = ?", (child,))["parent_id"] == parent
    res = client.post("/plan/item/reparent", data={"id": parent, "parent_id": child})
    assert res.status_code == 400, "순환을 허용했다"


def test_영역_추가_이름변경_이동_삭제(client):
    client.get("/plan")
    res = client.post("/plan/area/add", data={"name": "새 영역"})
    assert res.status_code == 200, res.text
    aid = res.json().get("id")
    client.post("/plan/area/update", data={"id": aid, "name": "바뀐 영역", "tone": "teal"})
    row = _one("SELECT name, tone FROM lt_area WHERE id = ?", (aid,))
    assert (row["name"], row["tone"]) == ("바뀐 영역", "teal")
    client.post("/plan/area/move", data={"id": aid, "dir": "up"})
    client.post("/plan/area/delete", data={"id": aid})
    assert client.get("/plan").status_code == 200


def test_영역을_지우면_그_안의_항목도_함께_정리된다(client):
    client.get("/plan")
    aid = client.post("/plan/area/add", data={"name": "지울 영역"}).json()["id"]
    iid = _add_item(client, "딸린 항목", "2026-08-01", "2026-08-31", area_id=aid)
    client.post("/plan/area/delete", data={"id": aid})
    left = _one("SELECT id FROM lt_item WHERE id = ?", (iid,))
    if left is not None:
        assert _one("SELECT id FROM lt_area WHERE id = ?", (aid,)) is not None, (
            "영역은 지워졌는데 항목이 없는 영역을 가리키며 남았다(고아 데이터)"
        )
    assert client.get("/plan").status_code == 200


# -- 구분(카테고리) ----------------------------------------------------------


def test_구분_추가_수정_이동_삭제(client):
    client.get("/settings")
    res = client.post("/settings/category/add", data={"name": "새 구분", "tone": "purple"})
    assert res.status_code == 200, res.text
    cid = res.json().get("id")
    client.post("/settings/category/update",
                data={"id": cid, "name": "바뀐 구분", "tone": "orange", "is_active": "1"})
    row = _one("SELECT name, tone FROM categories WHERE id = ?", (cid,))
    assert (row["name"], row["tone"]) == ("바뀐 구분", "orange")
    client.post("/settings/category/move", data={"id": cid, "dir": "up"})
    client.post("/settings/category/delete", data={"id": cid})
    assert _one("SELECT is_active FROM categories WHERE id = ?", (cid,))["is_active"] == 0


def test_잘못된_색은_저장되지_않는다(client):
    client.get("/settings")
    res = client.post("/settings/category/add", data={"name": "이상한색", "tone": "무지개"})
    if res.status_code == 200:
        cid = res.json()["id"]
        tone = _one("SELECT tone FROM categories WHERE id = ?", (cid,))["tone"]
        from app.config import TONE_KEYS
        assert tone in TONE_KEYS, f"팔레트에 없는 색이 저장됐다: {tone}"


def test_지운_구분을_쓰던_날도_화면이_열린다(client):
    """소프트 삭제라 참조는 남는다. 그 상태에서 오늘·주간·분석이 깨지면 안 된다."""
    client.get("/today")
    cid = client.post("/settings/category/add",
                      data={"name": "임시", "tone": "blue"}).json()["id"]
    block = _one("SELECT id FROM blocks WHERE date = ? AND block_label = 'B3'", (TODAY,))
    client.post("/save/field", data={"entity": "block", "id": block["id"],
                                     "field": "bcat", "value": cid})
    client.post("/settings/category/delete", data={"id": cid})
    for path in ("/today", "/week", "/analytics", "/settings"):
        assert client.get(path).status_code == 200, f"{path} 가 깨졌다"


# -- 블록 시간 설정 -----------------------------------------------------------


def _times(pairs):
    out = {}
    for i, (s, e) in enumerate(pairs):
        out[f"start_{i}"] = s
        out[f"end_{i}"] = e
    return out


DEFAULT_PAIRS = [("07:30", "09:30"), ("09:30", "11:30"), ("11:30", "12:30"),
                 ("12:30", "14:30"), ("14:30", "16:30"), ("16:30", "19:00"),
                 ("19:00", "21:00"), ("21:00", "23:00")]


def test_블록시간_저장과_요일_덮어쓰기(client):
    client.get("/settings")
    res = client.post("/settings/blocktimes", data=_times(DEFAULT_PAIRS))
    assert res.status_code == 200, res.text
    wed = [("06:00", "08:00"), ("08:00", "10:00"), ("10:00", "11:00"),
           ("11:00", "13:00"), ("13:00", "15:00"), ("15:00", "17:30"),
           ("17:30", "19:30"), ("19:30", "21:30")]
    res = client.post("/settings/blocktimes", data=dict(_times(wed), scope="2"))
    assert res.status_code == 200, res.text
    db._settings_cache = None
    assert db.get_day_blocks(2)[0][2] == "06:00"
    assert db.get_day_blocks(0)[0][2] == "07:30"
    client.post("/settings/blocktimes/reset", data={"scope": "2"})
    db._settings_cache = None
    assert db.get_day_blocks(2)[0][2] == "07:30"


@pytest.mark.parametrize("pairs,왜", [
    ([("10:00", "08:00")] + DEFAULT_PAIRS[1:], "시작이 끝보다 늦음"),
    ([("07:30", "07:50")] + DEFAULT_PAIRS[1:], "30분 배수가 아님"),
    ([("07:30", "09:30"), ("09:00", "11:30")] + DEFAULT_PAIRS[2:], "앞 블록과 겹침"),
    ([("25:99", "09:30")] + DEFAULT_PAIRS[1:], "시각 형식이 틀림"),
    ([("", "09:30")] + DEFAULT_PAIRS[1:], "빈 값"),
])
def test_잘못된_블록시간은_400으로_막힌다(client, pairs, 왜):
    client.get("/settings")
    res = client.post("/settings/blocktimes", data=_times(pairs))
    assert res.status_code == 400, f"{왜} 인데 통과했다"


def test_잘못된_요일_범위는_400(client):
    client.get("/settings")
    for scope in ("7", "-1", "abc", "99"):
        res = client.post("/settings/blocktimes",
                          data=dict(_times(DEFAULT_PAIRS), scope=scope))
        assert res.status_code == 400, f"scope={scope} 가 통과했다"


def test_블록시간을_바꾸면_빈_날의_골격이_다시_만들어진다(client):
    client.get("/today")
    before = len(_rows("SELECT id FROM slots WHERE date = ?", (TODAY,)))
    shorter = [("07:30", "08:30")] + DEFAULT_PAIRS[1:]
    assert client.post("/settings/blocktimes", data=_times(shorter)).status_code == 200
    db._settings_cache = None
    client.get("/today")
    after = len(_rows("SELECT id FROM slots WHERE date = ?", (TODAY,)))
    assert after < before, "블록을 줄였는데 슬롯 수가 그대로다"


def test_내용이_있는_날은_블록시간을_바꿔도_보존된다(client):
    """이 보호가 깨지면 설정 한 번에 그날 기록이 조용히 사라진다."""
    client.get("/today")
    slot = _one("SELECT id FROM slots WHERE date = ? ORDER BY slot_index LIMIT 1", (TODAY,))
    client.post("/save/field", data={"entity": "slot", "id": slot["id"],
                                     "field": "do_text", "value": "지우면 안 되는 기록"})
    shorter = [("07:30", "08:30")] + DEFAULT_PAIRS[1:]
    client.post("/settings/blocktimes", data=_times(shorter))
    db._settings_cache = None
    client.get("/today")
    kept = _one("SELECT do_text FROM slots WHERE id = ?", (slot["id"],))
    assert kept is not None and kept["do_text"] == "지우면 안 되는 기록"


# -- 구분 템플릿 42칸 ---------------------------------------------------------


def test_템플릿_42칸_저장(client):
    client.get("/settings")
    tid = client.post("/settings/template/add", data={"name": "T1"}).json()["id"]
    cid = _one("SELECT id FROM categories WHERE name = '코어'")["id"]
    for weekday in range(7):
        for label in ("B1", "B2", "B3", "B4", "B5", "B6"):
            res = client.post("/settings/template/cell", data={
                "template_id": tid, "weekday": weekday,
                "block_label": label, "category_id": cid})
            assert res.status_code == 200, f"{weekday}/{label} 저장 실패: {res.text}"
    n = _one("SELECT COUNT(*) AS c FROM cat_template_cell WHERE template_id = ?", (tid,))["c"]
    assert n == 42, f"42칸이 아니라 {n}칸이 저장됐다"
    client.post("/settings/template/rename", data={"id": tid, "name": "T2"})
    client.post("/settings/template/delete", data={"id": tid})
    assert _one("SELECT COUNT(*) AS c FROM cat_template_cell WHERE template_id = ?",
                (tid,))["c"] == 0, "템플릿을 지웠는데 칸이 남았다"


@pytest.mark.parametrize("weekday", ["7", "-1", "abc", "99"])
def test_템플릿_요일_범위_검증(client, weekday):
    client.get("/settings")
    tid = client.post("/settings/template/add", data={"name": "T"}).json()["id"]
    res = client.post("/settings/template/cell", data={
        "template_id": tid, "weekday": weekday, "block_label": "B1", "category_id": ""})
    assert res.status_code in (400, 422), f"weekday={weekday} 가 통과했다"


# -- 주간 템플릿: 세션시간 · 블록 이름 · 칸 단위 구분 ---------------------------


def _새_템플릿(client, 이름="T"):
    client.get("/settings")
    return client.post("/settings/template/add", data={"name": 이름}).json()["id"]


def _시간표(tid, scope="", **바꿀것):
    """8블록 기본 시간표에 바꿀 칸만 얹어 폼 데이터로 만든다."""
    from app.config import DAY_BLOCKS

    data = {"template_id": tid, "scope": scope}
    for i, (_l, _c, s, e) in enumerate(DAY_BLOCKS):
        data[f"start_{i}"] = 바꿀것.get(f"start_{i}", s)
        data[f"end_{i}"] = 바꿀것.get(f"end_{i}", e)
    return data


def _블록시각(날짜, 라벨):
    return _one("SELECT start_time, end_time FROM blocks WHERE date = ? AND block_label = ?",
                (날짜, 라벨))


def test_템플릿_세션시간이_그_주에만_적용된다(client):
    tid = _새_템플릿(client, "늦게 시작하는 주")
    # B1 을 08:00~09:30 으로(30분 배수 유지). 뒤 블록은 그대로 두어 겹치지 않는다.
    res = client.post("/settings/template/times",
                      data=_시간표(tid, "", start_0="08:00"))
    assert res.status_code == 200, res.text

    client.get(f"/week/{MONDAY}")
    res = client.post("/week/apply-template",
                      data={"week_start": MONDAY, "template_id": tid})
    assert res.status_code == 200, res.text
    d = res.json()
    assert d["days"] == 7 and d["skipped_days"] == 0

    assert _블록시각(MONDAY, "B1")["start_time"] == "08:00"
    # 그 주에만 적힌다. 다음 주는 설정값 그대로.
    다음주 = (datetime.date.fromisoformat(MONDAY) + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    client.get(f"/day/{다음주}")
    assert _블록시각(다음주, "B1")["start_time"] == "07:30"


def test_적어_둔_것이_있는_날은_세션시간을_안_바꾼다(client):
    """골격을 다시 만들면 칸이 어긋나 글이 사라진다. 건너뛰고 몇 날인지 알려 준다."""
    tid = _새_템플릿(client, "시간표만")
    client.post("/settings/template/times", data=_시간표(tid, "", start_0="08:00"))
    client.get(f"/day/{MONDAY}")
    칸 = _one("SELECT s.id FROM slots s JOIN blocks b ON b.id = s.block_id "
             "WHERE b.date = ? AND b.block_label = 'B1' ORDER BY s.slot_index LIMIT 1",
             (MONDAY,))
    client.post("/save/field", data={"entity": "slot", "id": 칸["id"],
                                     "field": "do_text", "value": "지키고 싶은 글"})

    res = client.post("/week/apply-template",
                      data={"week_start": MONDAY, "template_id": tid})
    d = res.json()
    assert d["skipped_days"] == 1 and d["days"] == 6
    assert _블록시각(MONDAY, "B1")["start_time"] == "07:30", "적어 둔 날인데 시간표를 바꿨다"
    assert _one("SELECT do_text FROM slots WHERE id = ?", (칸["id"],))["do_text"] == "지키고 싶은 글"


def test_템플릿_세션시간_빼기(client):
    tid = _새_템플릿(client, "뺄 것")
    client.post("/settings/template/times", data=_시간표(tid, "", start_0="08:00"))
    client.post("/settings/template/times/clear",
                data={"template_id": tid, "scope": ""})
    row = _one("SELECT times_common, times_wd FROM cat_template WHERE id = ?", (tid,))
    assert row["times_common"] is None and row["times_wd"] is None
    # 세션시간만 담았던 템플릿이라 이제 담은 것이 없다
    res = client.post("/week/apply-template",
                      data={"week_start": MONDAY, "template_id": tid})
    assert res.status_code == 400 and res.json()["error"] == "empty-template"


def test_템플릿_요일_세션시간은_그_요일만_바꾼다(client):
    tid = _새_템플릿(client, "수요일만 늦게")
    client.post("/settings/template/times", data=_시간표(tid, "2", start_0="09:00"))
    client.post("/week/apply-template", data={"week_start": MONDAY, "template_id": tid})
    수요일 = (datetime.date.fromisoformat(MONDAY) + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    assert _블록시각(수요일, "B1")["start_time"] == "09:00"
    assert _블록시각(MONDAY, "B1")["start_time"] == "07:30", "월요일까지 따라 바뀌었다"


def test_템플릿_블록_이름이_그_주_이름으로_들어간다(client):
    tid = _새_템플릿(client, "이름만")
    res = client.post("/settings/template/blockname",
                      data={"template_id": tid, "block_label": "B1", "name": "아침 공부"})
    assert res.status_code == 200, res.text
    client.post("/settings/template/blockname",
                data={"template_id": tid, "block_label": "B3", "name": "본업"})
    res = client.post("/week/apply-template",
                      data={"week_start": MONDAY, "template_id": tid})
    assert res.json()["names"] == 2
    이름 = {r["block_label"]: r["theme_text"] for r in
           _rows("SELECT block_label, theme_text FROM weekly_block_themes WHERE week_start = ?",
                 (MONDAY,))}
    assert 이름["B1"] == "아침 공부" and 이름["B3"] == "본업"
    assert "B2" not in 이름, "비워 둔 칸까지 덮어썼다"


def test_템플릿_블록_이름_비우면_안_담는다(client):
    tid = _새_템플릿(client, "이름 지우기")
    client.post("/settings/template/blockname",
                data={"template_id": tid, "block_label": "B1", "name": "아침"})
    client.post("/settings/template/blockname",
                data={"template_id": tid, "block_label": "B1", "name": "  "})
    assert _one("SELECT block_names FROM cat_template WHERE id = ?", (tid,))["block_names"] is None


def test_템플릿_칸_단위_구분이_그_칸에만_들어간다(client):
    """B1p4 = 1블록의 네 번째 30분 칸. 시각이 아니라 순번으로 잡는다."""
    tid = _새_템플릿(client, "칸 구분")
    cid = _one("SELECT id FROM categories WHERE name = '점검'")["id"]
    res = client.post("/settings/template/slot-cell", data={
        "template_id": tid, "weekday": 0, "block_label": "B1", "p": 4,
        "category_id": cid})
    assert res.status_code == 200, res.text
    res = client.post("/week/apply-template",
                      data={"week_start": MONDAY, "template_id": tid})
    assert res.json()["slots"] == 1
    칸들 = _rows("SELECT s.category_id FROM slots s JOIN blocks b ON b.id = s.block_id "
               "WHERE b.date = ? AND b.block_label = 'B1' ORDER BY s.slot_index", (MONDAY,))
    assert [c["category_id"] for c in 칸들] == [None, None, None, cid]


def test_템플릿_블록_구분과_칸_구분이_함께_간다(client):
    """블록 구분은 빈 칸이 상속하고, 적어 둔 칸만 그 위에 덮어쓴다."""
    tid = _새_템플릿(client, "종합")
    코어 = _one("SELECT id FROM categories WHERE name = '코어'")["id"]
    점검 = _one("SELECT id FROM categories WHERE name = '점검'")["id"]
    client.post("/settings/template/cell", data={
        "template_id": tid, "weekday": 0, "block_label": "B1", "category_id": 코어})
    client.post("/settings/template/slot-cell", data={
        "template_id": tid, "weekday": 0, "block_label": "B1", "p": 2,
        "category_id": 점검})
    res = client.post("/week/apply-template",
                      data={"week_start": MONDAY, "template_id": tid})
    d = res.json()
    assert d["applied"] == 1 and d["slots"] == 1
    assert _one("SELECT category_id FROM blocks WHERE date = ? AND block_label = 'B1'",
                (MONDAY,))["category_id"] == 코어
    칸들 = _rows("SELECT s.category_id FROM slots s JOIN blocks b ON b.id = s.block_id "
               "WHERE b.date = ? AND b.block_label = 'B1' ORDER BY s.slot_index", (MONDAY,))
    assert [c["category_id"] for c in 칸들][:2] == [None, 점검], "빈 칸은 블록 구분을 상속해야 한다"


def test_템플릿_세션시간이_구분보다_먼저_적용된다(client):
    """시간표가 바뀌면 골격이 다시 만들어진다. 구분을 먼저 넣으면 그것이 지워진다."""
    tid = _새_템플릿(client, "시간표+구분")
    코어 = _one("SELECT id FROM categories WHERE name = '코어'")["id"]
    client.post("/settings/template/times", data=_시간표(tid, "", start_0="08:00"))
    client.post("/settings/template/cell", data={
        "template_id": tid, "weekday": 0, "block_label": "B1", "category_id": 코어})
    client.post("/week/apply-template", data={"week_start": MONDAY, "template_id": tid})
    row = _one("SELECT start_time, category_id FROM blocks "
               "WHERE date = ? AND block_label = 'B1'", (MONDAY,))
    assert row["start_time"] == "08:00"
    assert row["category_id"] == 코어, "시간표를 다시 만들며 구분이 지워졌다"


@pytest.mark.parametrize("p", ["0", "-1", "17", "abc"])
def test_템플릿_칸_번호_범위_검증(client, p):
    tid = _새_템플릿(client, "범위")
    res = client.post("/settings/template/slot-cell", data={
        "template_id": tid, "weekday": 0, "block_label": "B1", "p": p,
        "category_id": ""})
    assert res.status_code in (400, 422), f"p={p} 가 통과했다"


def test_템플릿_세션시간_30분_배수가_아니면_거절한다(client):
    tid = _새_템플릿(client, "잘못된 시간")
    res = client.post("/settings/template/times",
                      data=_시간표(tid, "", end_0="09:20"))
    assert res.status_code == 400
    assert "30분" in res.json()["error"]


def test_템플릿을_지우면_칸_구분도_함께_지워진다(client):
    tid = _새_템플릿(client, "지울 것")
    cid = _one("SELECT id FROM categories WHERE name = '코어'")["id"]
    client.post("/settings/template/slot-cell", data={
        "template_id": tid, "weekday": 0, "block_label": "B1", "p": 1,
        "category_id": cid})
    client.post("/settings/template/delete", data={"id": tid})
    assert _one("SELECT COUNT(*) AS c FROM cat_template_slot WHERE template_id = ?",
                (tid,))["c"] == 0


# -- 고결감 -----------------------------------------------------------------


def test_고결감_추가_수정_다시보기메모_삭제(client):
    assert client.get("/reflect").status_code == 200
    res = client.post("/reflect/add", data={
        "kind": "고민", "title": "제목", "text": "내용", "tags": "#태그",
        "review_date": "2026-09-01"})
    assert res.status_code == 200, res.text
    rid = res.json().get("id")
    assert rid, res.text
    client.post(f"/reflect/update/{rid}", data={
        "kind": "결심", "title": "제목2", "text": "내용2", "tags": "",
        "review_date": "", "event_date": TODAY})
    row = _one("SELECT * FROM reflection WHERE id = ?", (rid,))
    assert row["title"] == "제목2"
    client.post(f"/reflect/review-note/{rid}", data={"note": "다시 보니"})
    assert _one("SELECT review_note FROM reflection WHERE id = ?", (rid,))["review_note"] == "다시 보니"
    client.post(f"/reflect/delete/{rid}", data={})
    assert _one("SELECT id FROM reflection WHERE id = ?", (rid,)) is None


def test_캘린더_다시올리기가_다시보기_사본을_사본으로_만든다(client, monkeypatch):
    """'캘린더 안 됨' 을 눌러 되살릴 때, 다시보기 사본은 사본 서식으로 올라가야 한다.

    사본 이벤트의 설명은 '다시 볼 내용 + 원본' 이라 create_event 로 되살리면
    정작 그날 볼 내용이 빠진 채 캘린더에 선다."""
    from app.integrations import gcal_write

    rid = client.post("/reflect/add", data={
        "kind": "고민", "title": "원본", "text": "본문", "tags": "",
        "review_date": ""}).json()["id"]
    client.post(f"/reflect/review-note/{rid}", data={"note": "그날 볼 것"})
    # 다시 볼 날을 잡으면 사본 행이 생긴다(캘린더는 꺼져 있어 synced=0 이다)
    client.post(f"/reflect/update/{rid}", data={
        "kind": "고민", "title": "원본", "text": "본문", "tags": "",
        "review_date": "2026-09-01", "event_date": TODAY})
    child = _one("SELECT * FROM reflection WHERE source_id = ?", (rid,))
    assert child and not child["synced"]

    # 카드의 '캘린더 안 됨' 은 알림이 아니라 누르는 버튼이다(막다른 표시를 두지 않는다)
    html = client.get("/reflect").text
    assert f'class="rf-sync off" data-id="{child["id"]}"' in html

    seen = {}
    monkeypatch.setattr(gcal_write, "create_review_copy",
                        lambda *a: seen.setdefault("copy", a) and "ev-1" or "ev-1")
    monkeypatch.setattr(gcal_write, "create_event",
                        lambda *a: seen.setdefault("plain", a) and "ev-2" or "ev-2")

    r = client.post(f"/reflect/sync/{child['id']}").json()
    assert r["synced"] is True
    assert "copy" in seen and "plain" not in seen, "사본을 예사 일정으로 만들었다"
    assert "그날 볼 것" in seen["copy"], "다시 볼 내용이 안 실렸다"
    assert _one("SELECT * FROM reflection WHERE id = ?", (child["id"],))["synced"] == 1

    # 원본은 그대로 예사 일정이다
    seen.clear()
    client.post(f"/reflect/sync/{rid}")
    assert "plain" in seen and "copy" not in seen


def test_고결감_uid가_자동으로_붙는다(client):
    client.get("/reflect")
    rid = client.post("/reflect/add", data={
        "kind": "감상", "title": "t", "text": "x", "tags": "", "review_date": ""}).json()["id"]
    uid = _one("SELECT uid FROM reflection WHERE id = ?", (rid,))["uid"]
    assert uid and len(uid.split("-")) == 3, f"공용 키 형식이 아니다: {uid}"


# -- 설정 저장 --------------------------------------------------------------


def test_설정_저장_왕복(client):
    client.get("/settings")
    res = client.post("/settings/save", data={
        "start_view": "week", "default_theme": "dark", "collapse_blocks": "0",
        "show_did": "0", "hide_task_titles": "숨길 제목"})
    assert res.status_code == 200
    db._settings_cache = None
    s = db.get_settings()
    assert s["start_view"] == "week"
    assert s["default_theme"] == "dark"
    assert s["hide_task_titles"] == "숨길 제목"
    assert client.get("/", follow_redirects=False).headers["location"] == "/week"


def test_허용되지_않은_설정키는_저장되지_않는다(client):
    client.get("/settings")
    client.post("/settings/save", data={"악의적키": "값", "DB_PATH": "/etc/passwd"})
    db._settings_cache = None
    assert "악의적키" not in db.get_settings()
    assert "DB_PATH" not in db.get_settings()


def test_요일_컨셉_7칸_저장(client):
    client.get("/settings")
    res = client.post("/settings/weekday-concepts",
                      data={f"wd{i}": f"컨셉{i}" for i in range(7)})
    assert res.status_code == 200
    db._settings_cache = None
    assert db.get_weekday_concepts() == [f"컨셉{i}" for i in range(7)]


def test_AI_설정_저장(client):
    client.get("/settings")
    res = client.post("/settings/ai/save",
                      data={"base_url": "https://api.example.com/v1", "model": "m1"})
    assert res.status_code == 200, res.text
    db._settings_cache = None
    s = db.get_settings()
    assert s["ai_base_url"] == "https://api.example.com/v1"
    assert s["ai_model"] == "m1"


# -- .env 편집 ---------------------------------------------------------------


def test_env_화면에_키값이_그대로_나오지_않는다(client):
    """설정 탭이 API 키를 평문으로 뿌리면 화면 캡처·캐시에 그대로 남는다."""
    html = client.get("/settings").text
    assert "sk-test-secret" not in html, ".env 의 실제 키가 화면에 노출된다"


def test_env_저장이_가려진_값을_원래대로_되돌린다(client, test_env_file):
    TEST_ENV = test_env_file
    before = TEST_ENV.read_text(encoding="utf-8")
    res = client.post("/settings/env/save", data={
        "content": "AI_API_KEY=********\nAI_MODEL=바뀐모델\nEMPTY=\n"})
    assert res.status_code == 200, res.text
    after = TEST_ENV.read_text(encoding="utf-8")
    assert "sk-test-secret" in after, "가림표시를 그대로 저장해 원래 키가 날아갔다"
    assert "바뀐모델" in after
    assert before != after


# -- 기간 삭제 · 내보내기 ------------------------------------------------------


def test_기간삭제는_지정한_기간만_지운다(client):
    client.get("/today")
    old = "2020-03-05"
    client.get(f"/day/{old}")
    assert _one("SELECT id FROM blocks WHERE date = ?", (old,)) is not None
    res = client.post("/settings/purge", data={"start": "2020-01-01", "end": "2020-12-31"})
    assert res.status_code == 200, res.text
    assert _one("SELECT id FROM blocks WHERE date = ?", (old,)) is None
    assert _one("SELECT id FROM blocks WHERE date = ?", (TODAY,)) is not None, (
        "기간 밖의 오늘 기록까지 지워졌다"
    )


def test_기간삭제는_날짜가_비면_거절한다(client):
    client.get("/today")
    for data in ({"start": "", "end": ""}, {"start": "2020-01-01"}, {}):
        res = client.post("/settings/purge", data=data)
        assert res.status_code in (400, 422), f"{data} 가 통과했다"
    assert _one("SELECT id FROM blocks WHERE date = ?", (TODAY,)) is not None


def test_기간삭제는_날짜가_아닌_값을_받아도_기록을_지우지_않는다(client):
    """형식 검증이 없어 문자열 비교로 도는 자리다. 최소한 남의 날짜를 지우면 안 된다."""
    client.get("/today")
    res = client.post("/settings/purge", data={"start": "abc", "end": "def"})
    assert res.status_code in (200, 400), res.status_code
    assert _one("SELECT id FROM blocks WHERE date = ?", (TODAY,)) is not None


def test_기간삭제는_거꾸로_준_기간에_아무것도_지우지_않는다(client):
    client.get("/today")
    res = client.post("/settings/purge", data={"start": "2030-01-01", "end": "2020-01-01"})
    assert res.status_code in (200, 400)
    assert _one("SELECT id FROM blocks WHERE date = ?", (TODAY,)) is not None


def test_CSV_내보내기(client):
    client.get("/today")
    res = client.get("/settings/export.csv?start=2020-01-01&end=2030-12-31")
    assert res.status_code == 200, res.text
    assert "text/csv" in res.headers.get("content-type", "")
    assert res.text.startswith("﻿") or "날짜" in res.text


def test_CSV_내보내기는_기간_없이_부르면_거절한다(client):
    assert client.get("/settings/export.csv").status_code == 422


def test_백업이_임시폴더에만_쓴다(client, tmp_root):
    TMP_ROOT = tmp_root
    res = client.post("/settings/backup", data={})
    assert res.status_code == 200, res.text
    made = list((TMP_ROOT / "backups").glob("*")) if (TMP_ROOT / "backups").exists() else []
    assert made, "백업 파일이 생기지 않았다"


def test_마지막_구분은_지울_수_없다(client):
    """다 지우면 오늘·주간의 구분 콤보박스가 텅 빈다. 하나는 남아야 한다."""
    client.get("/settings")
    거절 = 0
    for row in _rows("SELECT id FROM categories WHERE is_active = 1"):
        res = client.post("/settings/category/delete", data={"id": row["id"]})
        if res.status_code == 400:
            거절 += 1
    assert 거절 == 1, f"마지막 하나에서만 막혀야 하는데 {거절}번 막혔다"
    남은것 = _one("SELECT COUNT(*) AS c FROM categories WHERE is_active = 1")["c"]
    assert 남은것 == 1, f"{남은것}개가 남았다"
    assert client.get("/today").status_code == 200


def test_마지막_구분은_숨기기로도_없앨_수_없다(client):
    """/settings/category/update 의 is_active=0 도 같은 결과라 함께 막아야 한다."""
    client.get("/settings")
    for row in _rows("SELECT id FROM categories WHERE is_active = 1")[:-1]:
        client.post("/settings/category/delete", data={"id": row["id"]})
    마지막 = _one("SELECT id FROM categories WHERE is_active = 1")
    res = client.post("/settings/category/update",
                      data={"id": 마지막["id"], "is_active": "0"})
    assert res.status_code == 400, "마지막 구분을 숨기는 것이 통과했다"
    assert _one("SELECT is_active FROM categories WHERE id = ?",
                (마지막["id"],))["is_active"] == 1


def test_마지막_구분이라도_이름과_색은_바꿀_수_있다(client):
    """숨기기만 막는 것이지, 마지막 구분을 못 고치게 하는 것이 아니다."""
    client.get("/settings")
    for row in _rows("SELECT id FROM categories WHERE is_active = 1")[:-1]:
        client.post("/settings/category/delete", data={"id": row["id"]})
    마지막 = _one("SELECT id FROM categories WHERE is_active = 1")
    res = client.post("/settings/category/update",
                      data={"id": 마지막["id"], "name": "새 이름", "tone": "teal"})
    assert res.status_code == 200, res.text
    row = _one("SELECT name, tone FROM categories WHERE id = ?", (마지막["id"],))
    assert (row["name"], row["tone"]) == ("새 이름", "teal")


def test_구분을_새로_만들면_다시_지울_수_있다(client):
    client.get("/settings")
    for row in _rows("SELECT id FROM categories WHERE is_active = 1")[:-1]:
        client.post("/settings/category/delete", data={"id": row["id"]})
    마지막 = _one("SELECT id FROM categories WHERE is_active = 1")
    client.post("/settings/category/add", data={"name": "새 구분", "tone": "blue"})
    assert client.post("/settings/category/delete",
                       data={"id": 마지막["id"]}).status_code == 200


# -- 주간·분석 '기록된 시간' 집계 ------------------------------------------------


def _기록된시간(client):
    """주간 화면에 실제로 찍히는 '기록된 시간' 숫자를 읽는다."""
    import re

    html = client.get("/week").text
    m = re.search(r"기록된 시간.*?<strong>([\d.]+)</strong>h", html, re.S)
    assert m, "주간 화면에서 '기록된 시간' 을 못 찾았다"
    return float(m.group(1))


def _내용있는슬롯(client, 개수, 날짜=None):
    """코어 블록 슬롯에 계획을 적고 완료까지 체크한다(= 기록으로 잡히는 상태)."""
    날짜 = 날짜 or MONDAY
    client.get(f"/day/{날짜}")
    슬롯 = _rows("SELECT s.id FROM slots s JOIN blocks b ON b.id = s.block_id "
               "WHERE s.date = ? AND b.is_core = 1 ORDER BY s.slot_index LIMIT ?",
               (날짜, 개수))
    for s in 슬롯:
        client.post("/save/field", data={"entity": "slot", "id": s["id"],
                                         "field": "do_text", "value": "일함"})
        client.post(f"/slot/done/{s['id']}", data={"done": "1"})
    return 슬롯


def test_아무것도_안_한_주는_기록된_시간이_0이다(client):
    """예전에는 점심·저녁 블록이 '기타'로 시드돼 빈 주에도 24.5시간이 떴다."""
    assert _기록된시간(client) == 0.0


def test_구분을_안_골라도_기록한_시간은_잡힌다(client):
    """코어 블록 B1~B6 은 기본 구분이 없다. 예전에는 이 시간이 통째로 빠졌다."""
    _내용있는슬롯(client, 6)
    assert _기록된시간(client) == 3.0, "여섯 칸(3시간)을 기록했는데 숫자가 안 맞는다"


def test_구분_없는_시간은_미지정_줄로_보인다(client):
    _내용있는슬롯(client, 4)
    html = client.get("/week").text
    assert "미지정" in html, "구분 없는 시간이 어느 줄에도 안 나온다"
    assert "var(--tone-gray)" in html, "미지정 줄에 색이 안 붙었다"


def test_구분을_정하면_같은_시간이_그_구분으로_옮겨간다(client):
    """총합은 그대로고 이름표만 '미지정' 에서 그 구분으로 바뀐다."""
    _내용있는슬롯(client, 4)
    before = _기록된시간(client)
    assert "미지정" in client.get("/week").text

    cat = _one("SELECT id, name FROM categories WHERE name = '코어'")
    block = _one("SELECT id FROM blocks WHERE date = ? AND block_label = 'B1'", (MONDAY,))
    client.post("/save/field", data={"entity": "block", "id": block["id"],
                                     "field": "bcat", "value": cat["id"]})

    assert _기록된시간(client) == before, "구분만 정했는데 총 시간이 달라졌다"
    html = client.get("/week").text
    assert "미지정" not in html, "구분을 다 정했는데 미지정 줄이 남아 있다"
    assert cat["name"] in html


def test_내용이_없는_슬롯은_구분이_있어도_안_센다(client):
    """구분만 정해 두고 아무것도 안 한 시간이 '기록된 시간'에 잡히면 안 된다."""
    client.get(f"/day/{MONDAY}")
    cat = _one("SELECT id FROM categories WHERE name = '코어'")
    for b in _rows("SELECT id FROM blocks WHERE date = ? AND is_core = 1", (MONDAY,)):
        client.post("/save/field", data={"entity": "block", "id": b["id"],
                                         "field": "bcat", "value": cat["id"]})
    assert _기록된시간(client) == 0.0, "구분만 정했는데 시간이 잡힌다"


def test_고정_할일이_채운_칸만으로는_안_센다(client):
    """템플릿이 넣어 준 계획은 사람이 적은 것이 아니다. 체크하면 그때 센다."""
    client.get(f"/day/{MONDAY}")
    슬롯 = _one("SELECT s.id FROM slots s JOIN blocks b ON b.id = s.block_id "
              "WHERE s.date = ? AND b.is_core = 1 ORDER BY s.slot_index LIMIT 1", (MONDAY,))
    with db.get_conn() as conn:
        conn.execute("UPDATE slots SET do_text = '고정 할일', is_routine = 1 WHERE id = ?",
                     (슬롯["id"],))
    assert _기록된시간(client) == 0.0
    client.post(f"/slot/done/{슬롯['id']}", data={"done": "1"})
    assert _기록된시간(client) == 0.5, "체크했는데도 안 센다"


def _분석KPI(client, 이름):
    """분석 화면 KPI 줄에서 숫자 하나를 읽는다."""
    import re

    html = client.get("/analytics").text
    m = re.search(이름 + r".*?<strong>([\d.]+)</strong>", html, re.S)
    assert m, f"분석 화면에서 '{이름}' 을 못 찾았다"
    return float(m.group(1))


def test_화면만_열어_본_날은_기록한_날로_안_센다(client):
    """주간 탭을 한 번 열면 그 주 7일치 골격이 만들어진다. 그게 기록은 아니다."""
    client.get("/week")
    assert _분석KPI(client, "기록한 날") == 0
    assert _분석KPI(client, "연속 기록") == 0
    assert _분석KPI(client, "평균 완료율") == 0


def test_하루를_채우면_기록한_날과_완료율이_맞는다(client):
    client.get("/week")
    _내용있는슬롯(client, 6, 날짜=TODAY)
    assert _분석KPI(client, "기록한 날") == 1
    assert _분석KPI(client, "연속 기록") == 1
    assert _분석KPI(client, "평균 완료율") == 100


def test_빈_날이_평균_완료율을_끌어내리지_않는다(client):
    """예전에는 골격만 있는 빈 날이 0%로 섞여 하루를 다 채워도 17% 가 됐다."""
    client.get("/week")
    _내용있는슬롯(client, 6, 날짜=TODAY)
    assert _분석KPI(client, "평균 완료율") == 100, "빈 날이 평균에 섞였다"
    assert _분석KPI(client, "기록한 날") == 1


def test_한_일만_적은_날도_기록한_날이다(client):
    """세 숫자가 같은 기준(SLOT_HAS_CONTENT)을 쓰는지 본다."""
    client.get("/week")
    slot = _one("SELECT s.id FROM slots s JOIN blocks b ON b.id = s.block_id "
                "WHERE s.date = ? AND b.is_core = 1 ORDER BY s.slot_index LIMIT 1", (TODAY,))
    client.post("/save/field", data={"entity": "slot", "id": slot["id"],
                                     "field": "did_text", "value": "계획엔 없었지만 한 일"})
    assert _분석KPI(client, "기록한 날") == 1
    assert _분석KPI(client, "연속 기록") == 1
    assert _분석KPI(client, "기록된 시간") == 0.5


def test_고정_할일만_있는_날은_기록한_날이_아니다(client):
    client.get("/week")
    slot = _one("SELECT s.id FROM slots s JOIN blocks b ON b.id = s.block_id "
                "WHERE s.date = ? AND b.is_core = 1 ORDER BY s.slot_index LIMIT 1", (TODAY,))
    with db.get_conn() as conn:
        conn.execute("UPDATE slots SET do_text = '고정 할일', is_routine = 1 WHERE id = ?",
                     (slot["id"],))
    assert _분석KPI(client, "기록한 날") == 0
    client.post(f"/slot/done/{slot['id']}", data={"done": "1"})
    assert _분석KPI(client, "기록한 날") == 1, "체크했는데도 안 센다"


def test_여러_날_평균은_기록한_날끼리만_낸다(client):
    """하루만으로는 평균의 분모가 안 드러난다. 100%인 날과 50%인 날, 빈 날을 섞어 본다."""
    사흘전 = (datetime.date.today() - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    for 날 in (사흘전, TODAY):
        client.get(f"/day/{날}")
    # 사흘 전: 두 칸 계획하고 한 칸만 완료 → 50%
    슬롯 = _rows("SELECT s.id FROM slots s JOIN blocks b ON b.id = s.block_id "
               "WHERE s.date = ? AND b.is_core = 1 ORDER BY s.slot_index LIMIT 2", (사흘전,))
    for x in 슬롯:
        client.post("/save/field", data={"entity": "slot", "id": x["id"],
                                         "field": "do_text", "value": "일함"})
    client.post(f"/slot/done/{슬롯[0]['id']}", data={"done": "1"})
    # 오늘: 두 칸 다 완료 → 100%. 그 사이 날(어제)은 골격만 있고 비어 있다.
    _내용있는슬롯(client, 2, 날짜=TODAY)

    assert _분석KPI(client, "기록한 날") == 2, "빈 날이 섞였다"
    assert _분석KPI(client, "평균 완료율") == 75, "50% 와 100% 의 평균이 아니다"


def test_전체_기간으로_봐도_같은_기준이다(client):
    """rng=all 은 start 를 MIN(date) 로 잡는 다른 갈래라 따로 확인한다."""
    client.get("/week")
    assert client.get("/analytics?rng=all").status_code == 200
    import re

    def kpi(html, 이름):
        m = re.search(이름 + r".*?<strong>([\d.]+)</strong>", html, re.S)
        assert m, 이름
        return float(m.group(1))

    html = client.get("/analytics?rng=all").text
    assert kpi(html, "기록한 날") == 0, "열어만 본 날이 전체 기간에서는 세어진다"

    _내용있는슬롯(client, 6, 날짜=TODAY)
    html = client.get("/analytics?rng=all").text
    assert kpi(html, "기록한 날") == 1
    assert kpi(html, "기록된 시간") == 3.0


def test_기록이_하나도_없어도_전체_기간_화면이_열린다(client):
    for rng in ("7", "30", "all", "이상한값"):
        assert client.get(f"/analytics?rng={rng}").status_code == 200, rng
        assert client.post("/analytics/ai", data={"rng": rng}).status_code in (200, 400)


def test_분석_탭도_같은_기준으로_센다(client):
    """주간과 분석의 총 시간이 갈리면 어느 쪽을 믿어야 할지 알 수 없다."""
    _내용있는슬롯(client, 6, 날짜=TODAY)
    html = client.get("/analytics").text
    assert "미지정" in html, "분석 탭에 구분 없는 시간이 안 나온다"
    assert "3.0" in html, "분석 탭 총 시간(3.0h)이 안 보인다"


# -- 분석 -------------------------------------------------------------------


def test_분석_화면이_기록_없이도_열린다(client):
    assert client.get("/analytics").status_code == 200


def test_분석_화면이_기록이_있어도_열린다(client):
    client.get("/today")
    slot = _one("SELECT id FROM slots WHERE date = ? LIMIT 1", (TODAY,))
    client.post("/save/field", data={"entity": "slot", "id": slot["id"],
                                     "field": "do_text", "value": "계획"})
    client.post("/save/field", data={"entity": "slot", "id": slot["id"],
                                     "field": "did_text", "value": "한 일"})
    client.post(f"/slot/done/{slot['id']}", data={"done": "1"})
    res = client.get("/analytics")
    assert res.status_code == 200
    assert "%" in res.text


# -- 마이그레이션 -------------------------------------------------------------


def test_init_db_는_몇_번_돌려도_같다(fresh_db):
    def snapshot():
        with db.get_conn() as conn:
            tables = sorted(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"))
            cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            areas = conn.execute("SELECT COUNT(*) FROM lt_area").fetchone()[0]
            return tables, cats, areas

    first = snapshot()
    db.init_db()
    db.init_db()
    assert snapshot() == first, "init_db 를 다시 돌리면 상태가 달라진다"


def test_옛_스키마_버전에서도_마이그레이션이_돈다(fresh_db):
    """옛 .sql 덤프를 복원하면 user_version 이 0 이라 마이그레이션이 다시 한 번 돈다."""
    with db.get_conn() as conn:
        conn.execute("PRAGMA user_version = 0")
    db.init_db()
    with db.get_conn() as conn:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_meta)")}
    assert ver == db.SCHEMA_VERSION
    assert {"gratitude", "goal_tags", "concept", "day_review"} <= cols


def test_없어진_테이블은_되살아나지_않는다(fresh_db):
    with db.get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS lt_plan (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 0")
    db.init_db()
    with db.get_conn() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "lt_plan" not in names
    assert "weekday_concept" not in names


def test_설정_캐시가_저장_후_갱신된다(fresh_db):
    db.get_settings()
    db.set_setting("start_view", "week")
    assert db.get_settings()["start_view"] == "week"


def test_요일_컨셉_저장값이_깨져도_7칸을_준다(fresh_db):
    for bad in ("not-json", "{}", "[1,2]", ""):
        db.set_setting("weekday_concepts", bad)
        assert len(db.get_weekday_concepts()) == 7, f"{bad!r} 에서 7칸이 아니다"


def test_블록시간_저장값이_깨져도_기본값으로_돈다(fresh_db):
    for bad in ("not-json", "[]", '[{"start":"x"}]', "{}"):
        db.set_setting(db.BLOCK_TIMES_KEY, bad)
        blocks = db.get_day_blocks(0)
        assert len(blocks) == 8, f"{bad!r} 에서 블록이 8개가 아니다"


def test_PWA_자산이_전부_응답한다(client):
    for path, ctype in (("/manifest.webmanifest", "manifest"),
                        ("/sw.js", "javascript"),
                        ("/favicon.ico", "icon"),
                        ("/apple-touch-icon.png", "png")):
        res = client.get(path)
        assert res.status_code == 200, path
        assert ctype in res.headers.get("content-type", ""), path


def test_버전_엔드포인트(client):
    res = client.get("/version")
    assert res.status_code == 200
    assert res.json()["v"].isdigit()
    assert res.headers.get("cache-control") == "no-store"


def test_서버시각은_KST(client):
    res = client.get("/api/now")
    assert res.status_code == 200
    assert "+09:00" in res.json()["iso"], res.json()


def test_정적파일_캐시_정책(client):
    with_v = client.get("/static/app.js?v=1")
    without_v = client.get("/static/app.js")
    assert "immutable" in with_v.headers.get("cache-control", "")
    assert without_v.headers.get("cache-control") == "no-cache"
    assert client.get("/today").headers.get("cache-control") == "no-cache"
