# 2단계 엣지케이스 검증: slots_for_day 슬롯 범위 초과 검증
from starlette.testclient import TestClient

from app.config import slots_for_day, hhmm_to_min


class TestSlotsForDayEdgeCaseBugReport:
    """
    보고 항목: Slot end exceeds block boundary when block duration is not 30-minute multiple

    검증:
    1. slots_for_day는 정말 범위를 초과하는 슬롯을 반환하는가?
    2. 설정 저장 시 30분 배수 검증이 있는가?
    3. 따라서 실제로 그런 블록이 생성되는가?
    """

    def test_slots_for_day_returns_exceeding_slots_when_non_30min(self):
        """slots_for_day 함수는 블록이 30분 배수가 아니면 슬롯을 범위 초과해서 반환한다."""
        # 08:00~08:45 = 45분 (30분 배수 아님)
        blocks = [("Odd", True, "08:00", "08:45")]
        result = slots_for_day(blocks=blocks)

        # 슬롯이 2개 생성됨
        assert len(result) == 2

        # 첫 슬롯: 08:00~08:30 (OK, 블록 내)
        assert result[0] == (0, "Odd", "08:00", "08:30")

        # 두 번째 슬롯: 08:30~09:00 (문제: 블록 끝 08:45를 초과함)
        assert result[1] == (1, "Odd", "08:30", "09:00")

        # 슬롯 끝(09:00 = 540분)이 블록 끝(08:45 = 525분)을 초과
        slot_end_min = hhmm_to_min(result[1][3])
        block_end_min = hhmm_to_min("08:45")
        assert slot_end_min > block_end_min, f"슬롯 끝 {slot_end_min}이 블록 끝 {block_end_min}을 초과"

    def test_settings_api_rejects_non_30min_blocks(self, client: TestClient):
        """설정 API (/settings/blocktimes)는 30분 배수가 아닌 블록을 거절한다."""
        # 08:00~08:45 (45분 = 30분 배수 아님)를 설정하려고 함
        response = client.post(
            "/settings/blocktimes",
            data={
                "scope": "",  # 공통 시간표
                "label_0": "B1",
                "start_0": "08:00",
                "end_0": "08:45",  # 45분 duration
            },
        )

        # 400 Bad Request 거절됨
        assert response.status_code == 400
        data = response.json()
        assert not data.get("ok")
        assert "30분 단위" in data.get("error", "")

    def test_settings_api_accepts_30min_multiple_blocks(self, client: TestClient):
        """설정 API는 30분 배수인 블록 8개를 수락한다.

        이 엔드포인트는 8블록을 통째로 받는다(start_0~start_7). 한 칸만 보내면
        나머지가 빈 값이라 'HH:MM 형식이 잘못됨'으로 400 이 나는 것이 정상이다.
        """
        from app.config import DAY_BLOCKS

        data = {"scope": ""}
        for i, (_label, _core, s_t, e_t) in enumerate(DAY_BLOCKS):
            data[f"start_{i}"] = s_t
            data[f"end_{i}"] = e_t      # 기본 시간표는 전부 30분 배수다

        response = client.post("/settings/blocktimes", data=data)

        assert response.status_code == 200, response.text
        assert response.json().get("ok") is True

    def test_settings_api_rejects_partial_payload(self, client: TestClient):
        """한 칸만 보내면 거절한다(위 테스트가 왜 8칸을 다 보내는지에 대한 근거)."""
        response = client.post(
            "/settings/blocktimes",
            data={"scope": "", "start_0": "08:00", "end_0": "08:30"},
        )
        assert response.status_code == 400
        assert "HH:MM" in response.json().get("error", "")


class TestRealImpactAnalysis:
    """
    실제 영향도 분석: 이것이 사용자에게 영향을 주는 버그인가?
    """

    def test_normal_usage_never_creates_non_30min_blocks(self, client: TestClient):
        """정상적인 설정 사용자는 절대 30분 배수가 아닌 블록을 만들 수 없다."""
        # 기본 DAY_BLOCKS를 보면 모든 블록이 30분 배수다
        from app.config import DAY_BLOCKS

        for label, is_core, start, end in DAY_BLOCKS:
            duration_min = hhmm_to_min(end) - hhmm_to_min(start)
            # 모든 기본 블록이 30분 배수여야 함
            assert duration_min % 30 == 0, f"{label}: {start}~{end}는 {duration_min}분 (30의 배수 아님)"

    def test_slots_for_day_called_only_with_validated_blocks(self, client, fresh_db):
        """30분 배수가 아닌 블록은 설정 저장에서 막혀 slots_for_day 까지 못 간다.

        예전에는 이 사실을 주석으로만 적어 두고 아무것도 눌러 보지 않았다.
        실제로 저장을 시도해 거절되는지, 그리고 값이 안 바뀌었는지 확인한다.
        """
        from app.db import BLOCK_TIMES_KEY, get_settings

        before = get_settings().get(BLOCK_TIMES_KEY)

        form = {}
        for i in range(8):
            form[f"start_{i}"] = "07:00"
            form[f"end_{i}"] = "07:40"      # 40분 = 30의 배수가 아니다
        r = client.post("/settings/blocktimes", data=form)
        assert r.status_code == 400, r.text
        assert "30분" in r.json()["error"]
        assert get_settings().get(BLOCK_TIMES_KEY) == before, "거절했는데 값이 바뀌었다"

        # 형식이 아예 틀린 것도 막힌다
        form["start_0"] = "0700"
        r = client.post("/settings/blocktimes", data=form)
        assert r.status_code == 400
        assert "HH:MM" in r.json()["error"]
