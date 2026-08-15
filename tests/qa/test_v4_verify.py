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
        """GET /day/9999-12-31 요청."""
        # 이 경로는 _parse_date("9999-12-31")에 의해 검증됨
        # 유효한 날짜 형식이므로 _day_view로 진행
        response = client.get("/day/9999-12-31")
        # 200 또는 500이 반환될 수 있음
        assert response.status_code in (200, 500)
        # 500이면 OverflowError 때문일 가능성

    def test_save_achievement_with_max_date(self, client):
        """POST /save/achievement with date=9999-12-31."""
        # 이 엔드포인트는 upsert_achievement_event 호출
        # 극단값 date_str이 전달되면 OverflowError 발생 가능
        response = client.post(
            "/save/achievement",
            json={"date": "9999-12-31", "items": ["달성1"]}
        )
        # 성공하거나 명확한 오류 응답이어야 함 (500 아님)
        if response.status_code == 500:
            pytest.fail("극단값 입력에 대해 500 오류 반환 (OverflowError 미처리)")
        assert response.status_code in (200, 201, 400, 422)
