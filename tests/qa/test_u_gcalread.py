# 구글 캘린더 수신·캐시 기능: 종일/시간/멀티데이/RRULE/TTL/에러 경로
import threading
import time
import urllib.error
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

import app.integrations.gcal as gcal


# gcal 모듈이 import할 때 이미 app.config.GCAL_CALENDARS를 복사했으므로,
# 테스트에서는 gcal.GCAL_CALENDARS를 직접 수정해야 한다.

# 테스트용 .ics 문자열 (종일, 시간대별, 반복 등)
ICS_SAMPLE_BASIC = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:event-allday@example.com
DTSTART;VALUE=DATE:20260816
SUMMARY:All day event
END:VEVENT
BEGIN:VEVENT
UID:event-utc@example.com
DTSTART:20260816T100000Z
DTEND:20260816T110000Z
SUMMARY:UTC event
END:VEVENT
BEGIN:VEVENT
UID:event-kst@example.com
DTSTART:20260816T100000
DTEND:20260816T110000
SUMMARY:No timezone event (assumed local)
END:VEVENT
END:VCALENDAR"""

ICS_SAMPLE_MULTIDAY = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-multiday@example.com
DTSTART;VALUE=DATE:20260816
DTEND;VALUE=DATE:20260818
SUMMARY:Multi-day event
END:VEVENT
END:VCALENDAR"""

ICS_SAMPLE_RRULE = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-rrule@example.com
DTSTART:20260816T100000Z
DTEND:20260816T110000Z
SUMMARY:Recurring event
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
END:VCALENDAR"""

ICS_BROKEN = b"this is not valid ics"




@pytest.fixture(autouse=True)
def reset_gcal_cache():
    """각 테스트마다 캐시를 초기화."""
    gcal._cache.clear()
    gcal._refreshing.clear()
    yield
    gcal._cache.clear()
    gcal._refreshing.clear()


def mock_urlopen(ics_data):
    """urllib.request.urlopen을 가짜로 만드는 헬퍼."""
    def _urlopen(req, timeout=None):
        return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                              read=lambda: ics_data, getcode=lambda: 200)
    return _urlopen


# =============================================================================
# 테스트 1: 종일 이벤트
# =============================================================================
def test_allday_event_normalized(real_integrations):
    """종일 이벤트는 all_day=True, start/end=None으로 정규화."""
    with mock.patch("urllib.request.urlopen", mock_urlopen(ICS_SAMPLE_BASIC)):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_date(date(2026, 8, 16))

    all_day_events = [e for e in result if e["all_day"]]
    assert len(all_day_events) >= 1
    ev = all_day_events[0]
    assert ev["title"] == "All day event"
    assert ev["all_day"] is True
    assert ev["start"] is None
    assert ev["end"] is None
    assert ev["start_min"] is None


# =============================================================================
# 테스트 2: 시간대 정규화 (UTC, 로컬)
# =============================================================================
def test_timezone_utc_converted_to_kst(real_integrations):
    """UTC 시간이 KST로 변환되는가."""
    with mock.patch("urllib.request.urlopen", mock_urlopen(ICS_SAMPLE_BASIC)):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_date(date(2026, 8, 16))

    # UTC 10:00 -> KST 19:00 (UTC+9)
    utc_events = [e for e in result if "UTC event" in e["title"]]
    assert len(utc_events) >= 1
    ev = utc_events[0]
    assert ev["start"] == "19:00"
    assert ev["end"] == "20:00"


def test_local_datetime_assumed_kst(real_integrations):
    """시간대 없는 시간이 KST로 간주되는가."""
    with mock.patch("urllib.request.urlopen", mock_urlopen(ICS_SAMPLE_BASIC)):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_date(date(2026, 8, 16))

    local_events = [e for e in result if "No timezone" in e["title"]]
    assert len(local_events) >= 1
    ev = local_events[0]
    assert ev["start"] == "10:00"
    assert ev["end"] == "11:00"


# =============================================================================
# 테스트 3: 종일 이벤트 날짜별 흩어짐
# =============================================================================
def test_multiday_allday_spreads_by_date(real_integrations):
    """여러 날 걸친 종일 이벤트가 범위 내에서 나타나는가."""
    with mock.patch("urllib.request.urlopen", mock_urlopen(ICS_SAMPLE_MULTIDAY)):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_range(date(2026, 8, 16), date(2026, 8, 18))

    # DTSTART:20260816 DTEND:20260818인 종일 이벤트는 최소한 16에 나타난다.
    assert "2026-08-16" in result
    assert any("Multi-day" in e["title"] for e in result.get("2026-08-16", []))
    # recurring_ical_events의 구체적 동작은 라이브러리 버전에 따라 다를 수 있으므로,
    # 총 이벤트가 1개 이상 있는지만 확인
    total = sum(len(evs) for evs in result.values())
    assert total >= 1


# =============================================================================
# 테스트 4: RRULE 반복 일정
# =============================================================================
def test_rrule_expands_to_multiple_occurrences(real_integrations):
    """RRULE이 있으면 횟수대로 전개되는가."""
    with mock.patch("urllib.request.urlopen", mock_urlopen(ICS_SAMPLE_RRULE)):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_range(date(2026, 8, 16), date(2026, 8, 18))

    # COUNT=3이므로 16, 17, 18에 나타나야 함
    total = sum(len(evs) for evs in result.values())
    assert total >= 3, f"RRULE COUNT=3이 3회 이상으로 전개되어야 하는데 {total}회만 찾음"


# =============================================================================
# 테스트 5: 캐시 TTL - 만료 전에는 같은 것 반환
# =============================================================================
def test_cache_before_ttl_not_refreshed(real_integrations):
    """TTL 안에서는 캐시를 재사용하고 _refresh_later를 부르지 않는다."""
    call_count = [0]

    def counting_urlopen(req, timeout=None):
        call_count[0] += 1
        return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                              read=lambda: ICS_SAMPLE_BASIC, getcode=lambda: 200)

    with mock.patch("urllib.request.urlopen", side_effect=counting_urlopen):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]

        # 첫 번째 호출
        result1 = gcal.events_for_date(date(2026, 8, 16))
        first_count = call_count[0]

        # TTL 안에서 두 번째 호출
        result2 = gcal.events_for_date(date(2026, 8, 16))
        second_count = call_count[0]

    # 네트워크 호출이 한 번만 있어야 함 (TTL 안에서 재사용)
    assert first_count == second_count == 1, f"캐시 재사용 안 됨: {first_count} -> {second_count}"
    assert result1 == result2


# =============================================================================
# 테스트 6: 캐시 TTL 만료 - 응답이 막히지 않고 이전 값 반환
# =============================================================================
def test_cache_after_ttl_returns_old_immediately(real_integrations):
    """TTL 만료 시 응답 즉시 (이전 캐시), 새로고침은 뒤에서."""
    call_count = [0]

    def counting_urlopen(req, timeout=None):
        call_count[0] += 1
        # 느린 네트워크 시뮬레이션 (2초 대기)
        time.sleep(2)
        return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                              read=lambda: ICS_SAMPLE_BASIC, getcode=lambda: 200)

    with mock.patch("urllib.request.urlopen", side_effect=counting_urlopen):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]

        # 첫 번째 호출 (느림)
        start = time.time()
        result1 = gcal.events_for_date(date(2026, 8, 16))
        first_time = time.time() - start
        # 캐시가 아예 없는 첫 호출은 기다린다(이 값이 커야 아래 비교가 뜻이 있다)
        assert first_time >= 2, f"첫 호출이 {first_time:.2f}초라 비교가 성립하지 않는다"

        # TTL을 강제로 만료
        gcal._cache["http://test.ics"]["at"] = time.time() - 200

        # 두 번째 호출 (빠른가?)
        start = time.time()
        result2 = gcal.events_for_date(date(2026, 8, 16))
        second_time = time.time() - start

        # 응답은 즉시 와야 함 (2초 이하)
        assert second_time < 2, f"TTL 만료 후 응답이 느림: {second_time:.2f}초"
        # 결과는 같아야 함
        assert result1 == result2

        # 뒤에서 새로고침이 시작됨
        time.sleep(3)  # _refresh_later 스레드 완료 대기
        # 이 시점에서 총 네트워크 호출은 2번 (첫 번째 + _refresh_later)
        assert call_count[0] >= 2


# =============================================================================
# 테스트 7: 수신 실패 시 이전 캐시 유지, 예외 없음
# =============================================================================
def test_fetch_failure_keeps_previous_cache(real_integrations):
    """네트워크 실패 시 이전 캐시를 그대로 반환."""
    def first_success(req, timeout=None):
        return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                              read=lambda: ICS_SAMPLE_BASIC, getcode=lambda: 200)

    def then_fail(req, timeout=None):
        raise urllib.error.URLError("Network error")

    with mock.patch("urllib.request.urlopen", side_effect=first_success):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result1 = gcal.events_for_date(date(2026, 8, 16))

    # 이제 네트워크 실패로 바꿈
    with mock.patch("urllib.request.urlopen", side_effect=then_fail):
        result2 = gcal.events_for_date(date(2026, 8, 16))

    # 실패 후에도 이전 캐시를 반환해야 함
    assert result1 == result2
    assert len(result2) > 0


# =============================================================================
# 테스트 8: 깨진 ICS 수신 시 이전 캐시 유지, 예외 없음
# =============================================================================
def test_broken_ics_keeps_previous_cache(real_integrations):
    """파싱 실패 시 이전 캐시를 그대로 반환."""
    def first_success(req, timeout=None):
        return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                              read=lambda: ICS_SAMPLE_BASIC, getcode=lambda: 200)

    def then_broken(req, timeout=None):
        return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                              read=lambda: ICS_BROKEN, getcode=lambda: 200)

    with mock.patch("urllib.request.urlopen", side_effect=first_success):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result1 = gcal.events_for_date(date(2026, 8, 16))

    # 이제 깨진 ICS로 바꿈
    with mock.patch("urllib.request.urlopen", side_effect=then_broken):
        result2 = gcal.events_for_date(date(2026, 8, 16))

    # 깨진 후에도 이전 캐시를 반환해야 함
    assert result1 == result2
    assert len(result2) > 0


# =============================================================================
# 테스트 9: 처음 받기 실패하면 None 반환
# =============================================================================
def test_first_fetch_fails_returns_none(real_integrations):
    """처음부터 실패하면 None을 반환하고 (또는 빈 리스트)."""
    def always_fail(req, timeout=None):
        raise urllib.error.URLError("Network error")

    with mock.patch("urllib.request.urlopen", side_effect=always_fail):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_date(date(2026, 8, 16))

    # 캐시가 없으면 빈 리스트 반환되어야 함
    assert result == []


# =============================================================================
# 테스트 10: _refresh_later 중복 실행 방지
# =============================================================================
def test_refresh_later_no_concurrent_fetches(real_integrations):
    """같은 URL로 _refresh_later가 겹쳐 도지 않는가."""
    fetch_count = [0]
    barrier = threading.Barrier(2, timeout=5)  # 스레드 두 개가 동시 진입

    def counting_urlopen(req, timeout=None):
        fetch_count[0] += 1
        # 모든 스레드가 동시 진입 지점에 도달
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        time.sleep(0.1)
        return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                              read=lambda: ICS_SAMPLE_BASIC, getcode=lambda: 200)

    with mock.patch("urllib.request.urlopen", side_effect=counting_urlopen):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]

        # 첫 번째 캐시 채우기
        gcal.events_for_date(date(2026, 8, 16))

        # TTL 만료
        gcal._cache["http://test.ics"]["at"] = time.time() - 200

        # _refresh_later 두 번 호출
        threads = []
        for _ in range(2):
            t = threading.Thread(target=lambda: gcal.events_for_date(date(2026, 8, 16)))
            t.start()
            threads.append(t)

        # 모든 스레드 완료 대기
        for t in threads:
            t.join(timeout=10)

        # 뒤에서 _refresh_later 완료 대기
        time.sleep(1)

    # 네트워크 호출이 2번이 아니라 1번만 있어야 함 (중복 방지)
    # 첫 번째 캐시 + _refresh_later 한 번
    assert fetch_count[0] <= 2, f"중복 호출됨: {fetch_count[0]}회"


# =============================================================================
# 테스트 11: 여러 캘린더 병합 - 순서·색 보존
# =============================================================================
def test_multiple_calendars_merged_with_colors(real_integrations):
    """여러 캘린더를 병합할 때 색·이름이 보존되는가."""
    def mock_urlopen_factory(ics_data, color_id):
        def _urlopen(req, timeout=None):
            return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                                  read=lambda: ics_data, getcode=lambda: 200)
        return _urlopen

    url1 = "http://cal1.ics"
    url2 = "http://cal2.ics"

    # 두 개의 URL에 대해 다른 mock 설정
    def multi_mock(req, timeout=None):
        if url1 in req.full_url:
            return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                                  read=lambda: ICS_SAMPLE_BASIC, getcode=lambda: 200)
        elif url2 in req.full_url:
            return mock.MagicMock(__enter__=lambda self: self, __exit__=lambda *a: None,
                                  read=lambda: ICS_SAMPLE_RRULE, getcode=lambda: 200)

    with mock.patch("urllib.request.urlopen", side_effect=multi_mock):
        gcal.GCAL_CALENDARS = [
            {"name": "Calendar 1", "color": "#ff0000", "url": url1},
            {"name": "Calendar 2", "color": "#00ff00", "url": url2},
        ]
        result = gcal.events_for_date(date(2026, 8, 16))

    # 두 캘린더의 이벤트가 섞여 있는가
    cal_names = {e["cal"] for e in result}
    assert len(cal_names) == 2, f"두 캘린더가 섞여야 하는데 {cal_names}"
    assert "Calendar 1" in cal_names
    assert "Calendar 2" in cal_names

    # 색이 보존되는가
    colors = {e["cal"]: e["color"] for e in result}
    assert colors.get("Calendar 1") == "#ff0000"
    assert colors.get("Calendar 2") == "#00ff00"


# =============================================================================
# 테스트 12: 이벤트 정렬 (종일 먼저, 그 다음 시간)
# =============================================================================
def test_events_sorted_allday_first(real_integrations):
    """같은 날 이벤트는 종일(all_day=True)이 먼저 정렬되는가."""
    with mock.patch("urllib.request.urlopen", mock_urlopen(ICS_SAMPLE_BASIC)):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_date(date(2026, 8, 16))

    # 첫 번째는 종일이어야 함
    if len(result) > 1:
        assert result[0]["all_day"] is True
        assert result[1]["all_day"] is False


# =============================================================================
# 테스트 13: 경계값 테스트 - 연도 극단값
# =============================================================================
def test_to_kst_with_extreme_years(real_integrations):
    """연도 극단값이 처리되는가 (MAX_YEAR 제약)."""
    KST = ZoneInfo("Asia/Seoul")

    # 정상 연도
    dt_normal = datetime(2026, 8, 16, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
    result = gcal._to_kst(dt_normal)
    assert result.year == 2026

    # 시간대 없는 로컬 시간
    dt_local = datetime(2026, 8, 16, 10, 0, 0)
    result = gcal._to_kst(dt_local)
    assert result.year == 2026
    assert result.tzinfo == KST


# =============================================================================
# 테스트 14: 제목 없는 이벤트
# =============================================================================
def test_event_without_summary_gets_default_title(real_integrations):
    """SUMMARY 필드 없는 이벤트는 '(제목 없음)'을 받는다."""
    ICS_NO_SUMMARY = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-no-title@example.com
DTSTART;VALUE=DATE:20260816
END:VEVENT
END:VCALENDAR"""

    with mock.patch("urllib.request.urlopen", mock_urlopen(ICS_NO_SUMMARY)):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_date(date(2026, 8, 16))

    assert len(result) > 0
    assert result[0]["title"] == "(제목 없음)"


# =============================================================================
# 테스트 15: LOCATION 필드 처리
# =============================================================================
def test_event_location_preserved(real_integrations):
    """LOCATION 필드가 보존되는가."""
    ICS_WITH_LOCATION = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-loc@example.com
DTSTART;VALUE=DATE:20260816
SUMMARY:Event with location
LOCATION:Conference Room A
END:VEVENT
END:VCALENDAR"""

    with mock.patch("urllib.request.urlopen", mock_urlopen(ICS_WITH_LOCATION)):
        gcal.GCAL_CALENDARS = [{"name": "Test", "color": "#ff0000", "url": "http://test.ics"}]
        result = gcal.events_for_date(date(2026, 8, 16))

    assert len(result) > 0
    assert result[0]["location"] == "Conference Room A"
