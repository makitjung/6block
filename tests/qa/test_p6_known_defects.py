# 4페르소나 QA 에서 확정된 결함 5건이 고쳐졌음을 못 박는 회귀 방지 테스트.
# 고치기 전에는 전부 xfail 이었다. 지금은 실제로 통과해야 한다.
import pytest

from app.common import MAX_YEAR, _parse_date
from app.integrations import gcal_write

# ---------------------------------------------------------------------------
# 결함 A (Medium). 연도 극단값에서 처리되지 않은 OverflowError → 화면 500
#   서로 다른 두 곳(day.py 다음날, common.py 그 주 일요일)이 각각 터졌다.
#   계산부마다 막지 않고 _parse_date 입구 한 곳에서 MAX_YEAR 로 걸러 둘 다 닫았다.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("d", [
    "9999-12-31",   # 다음날 계산과 그 주 일요일 계산이 둘 다 터지던 날
    "9999-12-30",   # 그 주 일요일 계산만 터지던 날
    "9999-12-27",   # 터지기 시작하던 첫날(월요일)
    "9500-06-01",   # MAX_YEAR 위쪽 일반값
])
def test_연도_극단값_하루화면이_500이_되지_않는다(client, d):
    """이제 _parse_date 가 걸러 /today 로 보낸다(개인용 서버라 오류 화면보다 오늘이 낫다)."""
    r = client.get(f"/day/{d}", follow_redirects=False)
    assert r.status_code in (200, 307), r.status_code


@pytest.mark.parametrize("d", ["9999-12-31", "9999-12-27", "9500-06-01"])
def test_연도_극단값_주간화면도_500이_되지_않는다(client, d):
    r = client.get(f"/week/{d}", follow_redirects=False)
    assert r.status_code in (200, 307), r.status_code


def test_연도_극단값_api도_500이_되지_않는다(client):
    r = client.get("/api/day/9999-12-31")
    assert r.status_code != 500


def test_parse_date_가_상한을_지킨다():
    """MAX_YEAR 까지는 받고 그 위는 형식 오류와 똑같이 None 이다."""
    assert _parse_date(f"{MAX_YEAR}-12-31") is not None
    assert _parse_date(f"{MAX_YEAR + 1}-01-01") is None
    assert _parse_date("9999-12-31") is None
    # 평범한 날짜는 종전대로
    assert _parse_date("2026-08-15") is not None
    assert _parse_date("2026-02-30") is None
    assert _parse_date("") is None


def test_정상_날짜는_영향_없다(client):
    """상한은 극단값만 막고 실제 쓰는 범위는 건드리지 않는다."""
    for d in ("2026-08-15", "2030-01-01", "1999-12-31"):
        assert client.get(f"/day/{d}").status_code == 200


# ---------------------------------------------------------------------------
# 결함 B (Medium). 종일 이벤트의 다음날 계산에 가드가 없었다
# ---------------------------------------------------------------------------


def test_종일이벤트_다음날_계산이_명확한_오류를_낸다():
    """OverflowError 대신 ValueError 로 알린다. 부르는 쪽이 이미 잡아 '반영 실패'로 처리한다."""
    assert gcal_write._next_day("2026-12-31") == "2027-01-01"
    with pytest.raises(ValueError) as ei:
        gcal_write._next_day("9999-12-31")
    assert "9999-12-31" in str(ei.value)
    assert not isinstance(ei.value, OverflowError)


def test_고결감_저장은_다시볼날짜가_이상해도_성공한다(client):
    """다시 볼 날짜가 범위를 벗어나면 없는 것으로 보고 기록 자체는 저장한다."""
    r = client.post("/reflect/add", data={
        "kind": "고민", "title": "제목", "text": "내용",
        "tags": "", "review_date": "9999-12-31",
    })
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True

    from app.db import get_conn
    with get_conn() as c:
        row = c.execute(
            "SELECT review_date FROM reflection WHERE id = ?", (r.json()["id"],)
        ).fetchone()
    assert row["review_date"] is None      # 못 쓰는 날짜라 저장하지 않았다


# ---------------------------------------------------------------------------
# 결함 C (Low). parse_summary 가 docstring 의 '통째 제목' 약속을 어겼다
# ---------------------------------------------------------------------------


def test_중첩_대괄호_제목이_통째로_보존된다():
    """docstring: "'[종류] 제목' → (kind, title). 형식이 아니면 (고민, 통째 제목)." """
    assert gcal_write.parse_summary("[고민 [부제]] 제목") == ("고민", "[고민 [부제]] 제목")


@pytest.mark.parametrize("summary,want", [
    ("[고민] 제목", ("고민", "제목")),
    ("[결정] [중요] 회의", ("결정", "[중요] 회의")),      # 제목 안의 대괄호는 보존
    ("[결심] 옛 명칭", ("결정", "옛 명칭")),              # 별칭 정규화
    ("[감상] 옛 명칭", ("감사", "옛 명칭")),
    ("[독서] 채근담 [상편]", ("고민", "채근담 [상편]")),   # 모르는 종류 → 고민
    ("대괄호 없음", ("고민", "대괄호 없음")),
    ("[] 빈 종류", ("고민", "[] 빈 종류")),
    ("", ("고민", "")),
])
def test_기존_동작은_그대로다(summary, want):
    """정규식을 바꾸면서 다른 경우가 함께 바뀌지 않았는지 못 박는다."""
    assert gcal_write.parse_summary(summary) == want


def test_앱이_만든_요약은_정상_왕복한다():
    for kind in ("고민", "결정", "감사"):
        s = f"[{kind}] 사용자가 [대괄호] 넣은 제목"
        assert gcal_write.parse_summary(s) == (kind, "사용자가 [대괄호] 넣은 제목")


# ---------------------------------------------------------------------------
# 결함 D (Low). /save/field 가 그룹 키 없이 오면 200 을 주면서 값을 버렸다
# ---------------------------------------------------------------------------


def _meta_col(date_str, col):
    from app.db import get_conn
    with get_conn() as c:
        row = c.execute(
            f"SELECT {col} FROM daily_meta WHERE date = ?", (date_str,)
        ).fetchone()
        return row[col] if row else None


def test_save_field_가_value_만으로도_저장한다(client):
    """다른 클라이언트(Record 앱·스크립트)가 {entity, field, value} 만 보내도 반영된다."""
    d = "2026-08-15"
    client.get(f"/day/{d}")
    r = client.post("/save/field", data={
        "entity": "meta", "id": d, "field": "dplan1", "value": "최소폼",
    })
    assert r.status_code == 200
    assert _meta_col(d, "daily_plan").split("\n")[0] == "최소폼"


def test_최소폼이_다른_칸을_지우지_않는다(client):
    """한 칸만 보냈을 때 나머지 두 칸은 저장돼 있던 값을 지킨다(_merge3 의 본래 약속)."""
    d = "2026-08-15"
    client.get(f"/day/{d}")
    client.post("/save/field", data={
        "entity": "meta", "id": d, "field": "dplan1", "value": "가",
        "dplan1": "가", "dplan2": "나", "dplan3": "다",
    })
    client.post("/save/field", data={          # 2번 칸만, 그룹 키 없이
        "entity": "meta", "id": d, "field": "dplan2", "value": "바뀜",
    })
    assert _meta_col(d, "daily_plan") == "가\n바뀜\n다"


def test_화면이_보내는_폼은_종전대로_동작한다(client):
    """app.js 가 3칸을 함께 보내는 경로가 깨지지 않았는지 확인한다."""
    d = "2026-08-15"
    client.get(f"/day/{d}")
    r = client.post("/save/field", data={
        "entity": "meta", "id": d, "field": "dplan1", "value": "달성1",
        "dplan1": "달성1", "dplan2": "", "dplan3": "",
    })
    assert r.status_code == 200
    assert _meta_col(d, "daily_plan") == "달성1\n\n"
