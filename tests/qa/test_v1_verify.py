# 적대적 검증자의 버그 검증 테스트
"""
두 개의 보고된 버그를 검증한다.
"""

from app.config import hhmm_to_min
from app.common import today_str
from app.db import get_conn


class TestHhmmToMinValidation:
    """
    Item 1: No input format validation: malformed times parsed silently as valid

    보고: hhmm_to_min('0800')이 '08:00'과 같은 480을 반환한다.
    기대: ValueError를 던져야 한다.

    반박:
    1. 함수 문서: "'HH:MM' 문자열을 자정 기준 분으로 변환"
    2. 실제 호출처 분석:
       - app/routes/settings.py: _valid_hhmm으로 먼저 검증 후 호출
       - app/config.py DAY_BLOCKS: 코드 내장 데이터 (개발자 입력)
    3. _valid_hhmm은 정규표현식 r"^\d{2}:\d{2}$"로 정확히 HH:MM만 허용
    4. 사용자는 settings.py 엔드포인트를 통해서만 시간을 입력 가능
    5. 따라서 실제 사용 경로에서는 형식이 검증된다.

    판정: hhmm_to_min이 형식 검증이 없는 것은 의도된 설계다.
    - 사용자 입력은 엔드포인트에서 검증
    - 내부 호출은 이미 검증된 값을 받는다
    - 코드 내 DAY_BLOCKS는 개발자가 관리
    """

    def test_valid_hhmm_guards_input(self):
        """settings.py의 _valid_hhmm이 실제로 형식을 검증하는가?"""
        from app.routes.settings import _valid_hhmm

        # 올바른 형식
        assert _valid_hhmm("08:00") is True
        assert _valid_hhmm("23:59") is True
        assert _valid_hhmm("00:00") is True

        # 잘못된 형식 - 모두 False
        assert _valid_hhmm("0800") is False   # 콜론 없음
        assert _valid_hhmm("08-00") is False  # 대시
        assert _valid_hhmm("08 00") is False  # 공백
        assert _valid_hhmm("8:00") is False   # 1자리 시간
        assert _valid_hhmm("08:0") is False   # 1자리 분

    def test_hhmm_to_min_defensive_handling(self):
        """hhmm_to_min이 콜론 위치를 고정으로 사용하는 이유"""
        # 함수 구현: int(hhmm[:2]) * 60 + int(hhmm[3:5])
        # "08:00"[3:5] = "00" (콜론은 무시)
        # "0800"[3:5] = "00" (3~5번째 문자)

        # 이것은 의도적 설계가 아니라 구현 단순화다.
        # 호출처에서 _valid_hhmm으로 검증하므로 문제 없다.

        assert hhmm_to_min("08:00") == 480
        assert hhmm_to_min("0800") == 480
        # 둘이 같지만, 이는 버그가 아니라 내부 구현 특성이다.


class TestSaveFieldMetaMerge3Detailed:
    """
    Item 2: /save/field 메타 3칸 필드 form 구조 불일치

    버그: /save/field로 메타 dplan1~3을 저장하면 실제로 저장되지 않는다.
    재현: test_p3_day_meta_merge3_dplan_path가 통과 → 버그 확인

    메커니즘:
    1. /save/field 엔드포인트: POST data = {entity, field, id, value}
    2. _merge3 함수 호출: form을 그대로 전달
    3. _merge3는 form["dplan1"], form["dplan2"], form["dplan3"]를 찾음
    4. 하지만 form에는 이들 키가 없음 (form["field"]="dplan1"이지만 form["dplan1"]은 없음)
    5. 따라서 저장되지 않음

    판정: real bug. 사용자 데이터 손실.
    """

    def test_save_field_meta_dplan_bug_confirmed(self, client, fresh_db):
        """
        bug를 재현하는 테스트.
        /save/field로 dplan1을 저장하면 DB에 저장되지 않는다.
        """
        d = today_str()
        client.get("/today")

        # /save/field로 메타 3칸 필드 저장 시도
        resp = client.post(
            "/save/field",
            data={
                "entity": "meta",
                "field": "dplan1",
                "id": d,
                "value": "save_field로 저장한 달성",
            },
        )

        # 상태코드는 200 (사용자는 저장되었다고 생각)
        assert resp.status_code in (200, 303)

        # 그러나 실제로는 저장되지 않음
        with get_conn() as conn:
            meta = conn.execute(
                "SELECT daily_plan FROM daily_meta WHERE date = ?", (d,)
            ).fetchone()

        # meta가 생성되지 않았거나, 생성되었어도 daily_plan이 빈 문자열
        if meta:
            # daily_plan이 비어있음 = 저장되지 않음 (버그)
            assert meta["daily_plan"] == "", f"save_field 저장이 작동하지 않음: {meta['daily_plan']}"
        else:
            # meta가 생성되지 않음 = 더욱 확실한 버그
            pass

    def test_save_day_works_correctly(self, client, fresh_db):
        """
        반면 /save/day는 올바르게 작동한다.
        이는 form 구조가 다르기 때문이다.
        """
        d = today_str()
        client.get("/today")

        # /save/day로 올바른 형식으로 저장
        resp = client.post(
            f"/save/day/{d}",
            data={
                "dplan1": "save_day로 저장한 달성 1",
                "dplan2": "save_day로 저장한 달성 2",
                "dplan3": "save_day로 저장한 달성 3",
            },
        )

        assert resp.status_code in (200, 303)

        # DB에서 확인 - 올바르게 저장됨
        with get_conn() as conn:
            meta = conn.execute(
                "SELECT daily_plan FROM daily_meta WHERE date = ?", (d,)
            ).fetchone()

        assert meta is not None, "daily_meta가 생성되지 않음"
        assert "save_day로 저장한 달성 1" in meta["daily_plan"]
        assert "save_day로 저장한 달성 2" in meta["daily_plan"]
        assert "save_day로 저장한 달성 3" in meta["daily_plan"]

        # /save/day는 form에 {"dplan1": ..., "dplan2": ..., "dplan3": ...} 구조
        # 따라서 _merge3에서 form["dplan1"]을 찾을 수 있음
