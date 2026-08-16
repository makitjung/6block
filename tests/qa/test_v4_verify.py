# 적대적 검증자: 버그 보고 반박 검증 (묶음 4)
import pytest
from datetime import date

import app.integrations.gcal_write as gcal_write


class TestNextDayOverflow:
    """_next_day 극단값 OverflowError 검증."""

    def test_next_day_max_date_overflow_occurs(self):
        """9999-12-31 + 1day는 범위를 벗어나 OverflowError 발생."""
        # 이 테스트는 OverflowError가 실제로 발생하는지 확인
        with pytest.raises(OverflowError):
            gcal_write._next_day("9999-12-31")

    def test_next_day_normal_dates_work(self):
        """일반적인 날짜는 정상 작동."""
        # 2024-01-15 + 1 = 2024-01-16
        result = gcal_write._next_day("2024-01-15")
        assert result == "2024-01-16"

    def test_next_day_year_end_leap_year(self):
        """윤년의 년말도 정상."""
        # 2024-12-31 + 1 = 2025-01-01 (2024는 윤년)
        result = gcal_write._next_day("2024-12-31")
        assert result == "2025-01-01"

    def test_next_day_feb_29_leap_year(self):
        """윤년의 2월 29일도 정상."""
        # 2024-02-29 + 1 = 2024-03-01
        result = gcal_write._next_day("2024-02-29")
        assert result == "2024-03-01"

    def test_next_day_min_date(self):
        """최솟값(0001-01-01)도 정상."""
        result = gcal_write._next_day("0001-01-01")
        assert result == "0001-01-02"

    def test_next_day_near_max_date(self):
        """9999-12-30은 정상."""
        result = gcal_write._next_day("9999-12-30")
        assert result == "9999-12-31"

    def test_next_day_at_max_boundary(self):
        """9999-12-31은 OverflowError (재확인)."""
        with pytest.raises(OverflowError) as exc_info:
            gcal_write._next_day("9999-12-31")
        # 에러 메시지 확인
        assert "out of range" in str(exc_info.value).lower()


class TestNextDayCallsitesException:
    """_next_day 호출 지점에서 OverflowError 처리 여부."""

    def test_create_event_with_max_date(self, client):
        """create_event가 9999-12-31 극단값으로 호출되면?

        이는 고결감 캘린더에 이벤트를 만드는 엔드포인트 시뮬레이션.
        """
        # 고결감 캘린더 쓰기가 비활성화되어 있으면 None 반환
        # 활성화되어 있으면 OverflowError가 발생할 가능성
        try:
            result = gcal_write.create_event(
                "고민",
                "극단값 테스트",
                "내용",
                "태그",
                "9999-12-31"
            )
            # 비활성화 상태: svc is None이므로 None 반환 (정상)
            assert result is None
        except OverflowError:
            # 활성화 상태: OverflowError 발생 (버그!)
            pytest.fail("create_event에서 OverflowError 처리 없음")

    def test_create_calendar_event_with_max_date(self, client):
        """create_calendar_event가 9999-12-31 극단값으로 호출되면?"""
        try:
            result = gcal_write.create_calendar_event(
                "일정",
                "9999-12-31",
                None
            )
            # 비활성화 상태: svc is None이므로 None 반환 (정상)
            assert result is None
        except OverflowError:
            # 활성화 상태: OverflowError 발생 (버그!)
            pytest.fail("create_calendar_event에서 OverflowError 처리 없음")

    def test_upsert_achievement_event_with_max_date(self, client):
        """upsert_achievement_event가 9999-12-31 극단값으로 호출되면?"""
        try:
            result = gcal_write.upsert_achievement_event(
                "9999-12-31",
                ["달성1", "달성2"]
            )
            # 비활성화 상태: svc is None이므로 existing_event_id 반환 (정상)
            assert result is None
        except OverflowError:
            # 활성화 상태: OverflowError 발생 (버그!)
            pytest.fail("upsert_achievement_event에서 OverflowError 처리 없음")


class TestIntegrationEdgeCaseInput:
    """라우트 엔드포인트에서 극단값 입력 처리."""

    def test_day_view_with_max_date_path(self, client):
        """GET /day/9999-12-31 은 OverflowError 가 그대로 올라온다(= 운영에서 500).

        확정된 결함이다. 고치면 이 테스트가 실패하므로, 고칠 때 200 을 기대하도록 바꾼다.
        재현 경로는 tests/qa/test_p6_maxdate.py 가 HTTP 요청으로 더 정확히 남긴다.
        """
        with pytest.raises(OverflowError):
            client.get("/day/9999-12-31")

    # 삭제: POST /save/achievement 는 존재하지 않는 엔드포인트였다(404).
    # 달성 저장은 /save/field(entity=meta, field=dplan1~3) 와 /save/day/{date} 가 담당하며,
    # 그 경로의 성과 캘린더 반영은 tests/qa/test_p5_final_verify.py 가 다룬다.
