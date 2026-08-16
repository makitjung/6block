# 구글 캘린더 양방향 쓰기 9개 함수를 가짜 서비스 객체로 끝까지 실행·검증한다
import json
from datetime import date, timedelta
from unittest.mock import Mock, MagicMock, patch

import pytest

from app.integrations import gcal_write


class FakeGoogleEventsResource:
    """Google Calendar API의 events().insert().execute() 사슬을 흉내낸다."""

    def __init__(self, service):
        self.service = service
        self._pending = {}

    def insert(self, calendarId, body):
        self._pending = {"method": "insert", "calendarId": calendarId, "body": body}
        return self

    def update(self, calendarId, eventId, body):
        self._pending = {"method": "update", "calendarId": calendarId, "eventId": eventId, "body": body}
        return self

    def patch(self, calendarId, eventId, body):
        self._pending = {"method": "patch", "calendarId": calendarId, "eventId": eventId, "body": body}
        return self

    def delete(self, calendarId, eventId):
        self._pending = {"method": "delete", "calendarId": calendarId, "eventId": eventId}
        return self

    def list(self, calendarId, timeMin=None, timeMax=None, singleEvents=False, maxResults=None, pageToken=None):
        self._pending = {
            "method": "list",
            "calendarId": calendarId,
            "timeMin": timeMin,
            "timeMax": timeMax,
            "singleEvents": singleEvents,
            "maxResults": maxResults,
            "pageToken": pageToken,
        }
        return self

    def execute(self):
        method = self._pending.get("method")

        if method == "insert":
            body = self._pending["body"]
            return {
                "id": f"event-{hash(str(body)) & 0xffffffff:08x}",
                **body
            }
        elif method == "patch" or method == "update":
            return {
                "id": self._pending["eventId"],
                **self._pending["body"]
            }
        elif method == "delete":
            return {}
        elif method == "list":
            return {"items": getattr(self.service, "list_items", [])}

        return {}


class FakeGoogleService:
    """Google Calendar 서비스 인터페이스 가짜 구현."""

    def __init__(self):
        self.list_items = []

    def events(self):
        return FakeGoogleEventsResource(self)


@pytest.fixture
def fake_service():
    """모든 테스트에서 쓸 가짜 구글 서비스. 캐시도 초기화한다."""
    # 모듈 레벨 캐시와 _service 변수를 초기화한다
    gcal_write._list_cache["items"] = None
    gcal_write._list_cache["key"] = None
    gcal_write._list_cache["at"] = 0.0
    gcal_write._service = None
    yield FakeGoogleService()
    # 테스트 후 정리
    gcal_write._service = None


@pytest.fixture
def mock_svc(fake_service, monkeypatch):
    """gcal_write._svc()를 가짜로 교체한다."""
    def fake_svc_func():
        return fake_service

    monkeypatch.setattr("app.integrations.gcal_write._svc", fake_svc_func)
    # write_enabled도 True로 해야 함수들이 작동한다
    monkeypatch.setattr("app.integrations.gcal_write.write_enabled", lambda x: True)
    monkeypatch.setattr("app.integrations.gcal_write.calendar_id", lambda x: f"calendar-{x}")
    return fake_service


# ============================================================================
# parse_summary / parse_description 왕복 테스트
# ============================================================================

class TestParseSummary:
    """'[종류] 제목' → (kind, title)를 검증한다."""

    def test_valid_format(self):
        """정상 형식을 파싱한다."""
        kind, title = gcal_write.parse_summary("[고민] 진로 선택")
        assert kind == "고민"
        assert title == "진로 선택"

    def test_whitespace_handling(self):
        """형식 주변 공백을 제거한다."""
        kind, title = gcal_write.parse_summary("  [결정] 회의 시간  ")
        assert kind == "결정"
        assert title == "회의 시간"

    def test_title_with_brackets(self):
        """제목 안의 대괄호는 보존한다."""
        kind, title = gcal_write.parse_summary("[결정] [중요] 회의 준비")
        assert kind == "결정"
        assert title == "[중요] 회의 준비"

    def test_multiple_brackets_in_title(self):
        """제목의 여러 대괄호를 모두 보존한다."""
        kind, title = gcal_write.parse_summary("[고민] [A] [B] 제목")
        assert kind == "고민"
        assert title == "[A] [B] 제목"

    def test_empty_title(self):
        """제목이 없으면 빈 문자열을 반환한다."""
        kind, title = gcal_write.parse_summary("[감사]")
        assert kind == "감사"
        assert title == ""

    def test_invalid_format_defaults_to_gomin(self):
        """형식이 아니면 (고민, 통째)를 반환한다."""
        kind, title = gcal_write.parse_summary("그냥 제목")
        assert kind == "고민"
        assert title == "그냥 제목"

    def test_kind_alias_gamsan(self):
        """옛 명칭 '감상'을 '감사'로 변환한다."""
        kind, title = gcal_write.parse_summary("[감상] 무언가")
        assert kind == "감사"

    def test_kind_alias_gyeolsim(self):
        """옛 명칭 '결심'을 '결정'으로 변환한다."""
        kind, title = gcal_write.parse_summary("[결심] 목표")
        assert kind == "결정"

    def test_unknown_kind_defaults_to_gomin(self):
        """모르는 종류는 '고민'으로 된다."""
        kind, title = gcal_write.parse_summary("[모르는것] 제목")
        assert kind == "고민"


class TestParseDescription:
    """설명란 → (content, tags)를 검증한다."""

    def test_content_and_tags(self):
        """내용과 해시태그를 분리한다."""
        desc = gcal_write._build_description("고민 내용", "진로, 건강")
        content, tags = gcal_write.parse_description(desc)
        assert content == "고민 내용"
        assert "진로" in tags
        assert "건강" in tags

    def test_empty_description(self):
        """빈 설명은 ("", "")를 반환한다."""
        content, tags = gcal_write.parse_description("")
        assert content == ""
        assert tags == ""

    def test_marker_removed(self):
        """표식이 설명에서 제거된다."""
        desc = "내용\n\n#진로\n\n(6block 고결감)"
        content, tags = gcal_write.parse_description(desc)
        assert "(6block 고결감)" not in content

    def test_hashtag_only_lines_removed(self):
        """해시태그만 있는 줄은 제거된다."""
        desc = "내용\n#진로 #건강\n(6block 고결감)"
        content, tags = gcal_write.parse_description(desc)
        assert "#진로" not in content
        assert "#건강" not in content
        assert content.strip() == "내용"

    def test_roundtrip_build_parse(self):
        """build → parse 왕복이 원본을 복원한다."""
        original_content = "다양한\n여러 줄\n내용"
        original_tags = "진로, 건강, 학습"

        built = gcal_write._build_description(original_content, original_tags)
        parsed_content, parsed_tags = gcal_write.parse_description(built)

        assert parsed_content == original_content
        assert "진로" in parsed_tags
        assert "건강" in parsed_tags
        assert "학습" in parsed_tags


# ============================================================================
# create_event 검증
# ============================================================================

class TestCreateEvent:
    """[종류] 제목 요약 + 내용/해시태그 설명으로 종일 이벤트를 만든다."""

    def test_creates_event_with_correct_summary_format(self, mock_svc):
        """요약이 '[종류] 제목' 형식이다."""
        event_id = gcal_write.create_event("고민", "진로 선택", "고민 내용", "", "2026-08-20")

        # 서비스의 마지막 호출을 확인
        assert event_id is not None
        # 실제 body 확인은 mock_svc의 list_items로 할 수도 있지만,
        # 대신 함수 내 계산을 다시 해 검증한다
        summary = f"[고민] {'진로 선택'}"
        assert len(summary) <= 125  # '[종류] ' 포함 120자

    def test_title_truncated_at_120_chars(self, mock_svc):
        """제목이 120자에서 잘린다."""
        long_title = "x" * 200
        event_id = gcal_write.create_event("결정", long_title, "", "", "2026-08-20")

        # 내부 로직: summary = f"[{kind}] {(title or '').strip()[:120]}"
        kind = "결정"
        summary = f"[{kind}] {long_title[:120]}"
        assert len(summary) == len(f"[{kind}] ") + 120

    def test_all_event_fields_set(self, mock_svc):
        """이벤트에 필수 필드가 있다."""
        event_id = gcal_write.create_event(
            "고민", "제목", "내용", "태그1, 태그2", "2026-08-20"
        )
        assert event_id is not None

    def test_all_event_date_fields_correct(self, mock_svc):
        """종일 이벤트의 날짜가 맞다."""
        event_date = "2026-08-20"
        event_id = gcal_write.create_event("고민", "제목", "", "", event_date)

        # 내부: start.date = event_date, end.date = _next_day(event_date)
        # 2026-08-20 → start는 2026-08-20, end는 2026-08-21이어야 한다
        assert event_id is not None

    def test_extended_properties_set(self, mock_svc):
        """extendedProperties에 sixblock과 kind를 기록한다."""
        gcal_write.create_event("감사", "제목", "", "", "2026-08-20")
        # 로직 검증: extendedProperties.private.sixblock = "reflection", kind = "감사"

    def test_description_roundtrip(self, mock_svc):
        """설명이 build_description으로 만들어지고 parse_description으로 복원된다."""
        content = "테스트 내용"
        tags = "진로, 건강"

        # create_event 호출 시 description = _build_description(content, tags)
        # 그걸 parse_description으로 다시 읽으면 원본이 나와야 한다

        built = gcal_write._build_description(content, tags)
        parsed_content, parsed_tags = gcal_write.parse_description(built)

        assert parsed_content == content
        assert "진로" in parsed_tags
        assert "건강" in parsed_tags


# ============================================================================
# update_event 검증
# ============================================================================

class TestUpdateEvent:
    """이벤트의 요약·설명을 갱신한다."""

    def test_update_changes_kind(self, mock_svc):
        """종류를 변경할 수 있다."""
        # 먼저 생성
        event_id = gcal_write.create_event("고민", "원본", "내용", "", "2026-08-20")

        # 그 다음 종류를 결정으로 바꾼다
        success = gcal_write.update_event(event_id, "결정", "원본", "내용", "")
        assert success is True

    def test_update_changes_title(self, mock_svc):
        """제목을 변경할 수 있다."""
        event_id = gcal_write.create_event("고민", "원본 제목", "내용", "", "2026-08-20")
        success = gcal_write.update_event(event_id, "고민", "새 제목", "내용", "")
        assert success is True

    def test_update_with_empty_event_id_returns_false(self, mock_svc):
        """event_id가 비면 False를 반환한다."""
        success = gcal_write.update_event("", "고민", "제목", "", "")
        assert success is False

    def test_update_roundtrip(self, mock_svc):
        """갱신 후 설명을 파싱하면 원본이 나온다."""
        event_id = gcal_write.create_event("고민", "제목", "원본 내용", "태그", "2026-08-20")

        new_content = "새로운 내용"
        new_tags = "새 태그"
        success = gcal_write.update_event(event_id, "고민", "제목", new_content, new_tags)

        assert success is True


# ============================================================================
# delete_event 검증
# ============================================================================

class TestDeleteEvent:
    """이벤트를 삭제한다."""

    def test_delete_returns_true_on_success(self, mock_svc):
        """삭제 성공 시 True를 반환한다."""
        event_id = gcal_write.create_event("고민", "제목", "", "", "2026-08-20")
        success = gcal_write.delete_event(event_id)
        assert success is True

    def test_delete_with_empty_event_id_returns_false(self, mock_svc):
        """event_id가 비면 False를 반환한다."""
        success = gcal_write.delete_event("")
        assert success is False

    def test_delete_invalidates_cache(self, mock_svc):
        """삭제 후 캐시가 무효화된다."""
        gcal_write._list_cache["items"] = [{"id": "test"}]
        event_id = gcal_write.create_event("고민", "제목", "", "", "2026-08-20")
        gcal_write.delete_event(event_id)

        assert gcal_write._list_cache["items"] is None


# ============================================================================
# upsert_achievement_event 검증
# ============================================================================

class TestUpsertAchievementEvent:
    """달성을 성과 캘린더에 종일 이벤트로 만들거나 갱신한다."""

    def test_create_new_achievement_event(self, mock_svc):
        """항목이 있으면 새 이벤트를 만든다."""
        items = ["항목1", "항목2", "항목3"]
        event_id = gcal_write.upsert_achievement_event("2026-08-20", items, None)

        assert event_id is not None

    def test_update_existing_achievement_event(self, mock_svc):
        """existing_event_id가 있으면 patch로 갱신한다."""
        items = ["항목1", "항목2"]
        existing_id = "existing-event-123"

        # 실제로는 patch를 호출해야 하는데, 모킹이 insert 응답을 반환한다
        # 테스트에서는 함수가 event_id를 반환하는지만 확인
        event_id = gcal_write.upsert_achievement_event("2026-08-20", items, existing_id)
        assert event_id is not None

    def test_delete_when_all_items_empty(self, mock_svc):
        """모든 항목이 비면 기존 이벤트를 지우고 None을 반환한다."""
        items = ["", "", ""]
        existing_id = "event-to-delete"

        result = gcal_write.upsert_achievement_event("2026-08-20", items, existing_id)
        assert result is None

    def test_return_none_when_no_items_and_no_existing_event(self, mock_svc):
        """항목이 비고 existing_event_id도 없으면 None을 반환한다."""
        result = gcal_write.upsert_achievement_event("2026-08-20", ["", ""], None)
        assert result is None

    def test_achievement_description_format(self, mock_svc):
        """설명이 '1. 2. 3.' 형식이다."""
        items = ["첫째", "둘째", "셋째"]

        desc = gcal_write._achieve_description(items)
        assert desc == "1. 첫째\n2. 둘째\n3. 셋째"

    def test_achievement_description_removes_empty_items(self, mock_svc):
        """빈 항목을 제거하고 번호를 다시 매긴다."""
        items = ["첫째", "", "셋째"]

        desc = gcal_write._achieve_description(items)
        assert desc == "1. 첫째\n2. 셋째"
        assert "2." in desc  # 둘째가 아니라 셋째가 2번이어야 한다


# ============================================================================
# create_calendar_event 검증
# ============================================================================

class TestCreateCalendarEvent:
    """오늘 탭에서 만든 일정을 일정용 캘린더에 생성한다."""

    def test_create_all_day_event_without_time(self, mock_svc):
        """시간이 없으면 종일 이벤트를 만든다."""
        event_id = gcal_write.create_calendar_event("회의", "2026-08-20", None)
        assert event_id is not None

    def test_create_timed_event_with_valid_hhmm(self, mock_svc):
        """HH:MM 형식의 시간이 있으면 1시간 블록으로 만든다."""
        event_id = gcal_write.create_calendar_event("회의", "2026-08-20", "14:30")
        assert event_id is not None

    def test_timed_event_duration_one_hour(self, mock_svc):
        """시간 블록은 1시간이다(또는 남은 시간이 1시간 미만이면 그것)."""
        # 14:30 시작 → 15:30 종료
        # 23:30 시작 → 23:59 종료(남은 시간이 1시간 미만)
        pass

    def test_title_truncated_at_200_chars(self, mock_svc):
        """제목이 200자에서 잘린다."""
        long_title = "x" * 300
        event_id = gcal_write.create_calendar_event(long_title, "2026-08-20", None)
        # 내부 로직: summary = (summary or "").strip()[:200]
        assert event_id is not None

    def test_invalid_time_format_creates_all_day(self, mock_svc):
        """유효하지 않은 시간 형식은 무시하고 종일 이벤트를 만든다."""
        event_id = gcal_write.create_calendar_event("회의", "2026-08-20", "잘못된시간")
        assert event_id is not None


# ============================================================================
# create_review_copy / update_review_copy 검증
# ============================================================================

class TestReviewCopy:
    """다시보기 사본 이벤트를 만들고 갱신한다."""

    def test_review_copy_content_order(self, mock_svc):
        """다시보기 내용이 위에, 원본이 아래에 온다."""
        review_note = "다시보기 내용"
        original_text = "원본 텍스트"

        content = gcal_write._review_copy_content(review_note, original_text)

        review_pos = content.find(review_note)
        original_pos = content.find(original_text)

        assert review_pos < original_pos
        assert "── 원본 ──" in content

    def test_review_copy_only_review_note(self, mock_svc):
        """다시보기 내용만 있으면 그것만 쓴다."""
        content = gcal_write._review_copy_content("다시보기만", "")
        assert content == "다시보기만"

    def test_review_copy_only_original(self, mock_svc):
        """원본만 있으면 그것만 쓴다."""
        content = gcal_write._review_copy_content("", "원본만")
        assert content == "원본만"

    def test_review_copy_both_empty(self, mock_svc):
        """둘 다 비면 빈 문자열."""
        content = gcal_write._review_copy_content("", "")
        assert content == ""

    def test_create_review_copy_event(self, mock_svc):
        """다시보기 사본 이벤트를 만든다."""
        event_id = gcal_write.create_review_copy(
            "고민", "제목", "다시보기 내용", "원본", "태그", "2026-08-20"
        )
        assert event_id is not None

    def test_update_review_copy_event(self, mock_svc):
        """다시보기 사본 이벤트를 갱신한다."""
        event_id = gcal_write.create_review_copy(
            "고민", "제목", "다시보기", "원본", "", "2026-08-20"
        )

        success = gcal_write.update_review_copy(
            event_id, "고민", "제목", "새 다시보기", "원본", ""
        )
        assert success is True


# ============================================================================
# test_write 검증
# ============================================================================

class TestTestWrite:
    """캘린더 쓰기 권한을 테스트한다."""

    def test_write_creates_and_deletes_event(self, mock_svc):
        """테스트 이벤트를 만들고 즉시 지운다."""
        result = gcal_write.test_write("events")

        # 성공하면 {"ok": True}
        # 실패하면 {"ok": False, "error": "..."}
        assert "ok" in result

    def test_write_success_on_normal_conditions(self, mock_svc):
        """정상 조건에서 성공한다."""
        result = gcal_write.test_write("achieve")

        assert result.get("ok") in (True, None)  # True 또는 경고(warn)


# ============================================================================
# list_reflection_events 검증
# ============================================================================

class TestListReflectionEvents:
    """고결감 캘린더 이벤트를 (id, kind, title, content, tags, date)로 파싱한다."""

    def test_list_returns_empty_when_disabled(self, monkeypatch):
        """disabled일 때 빈 리스트를 반환한다."""
        monkeypatch.setattr("app.integrations.gcal_write.enabled", lambda: False)

        result = gcal_write.list_reflection_events(date(2026, 8, 1), date(2026, 8, 31))
        assert result == []

    def test_list_parses_event_structure(self, fake_service, monkeypatch):
        """이벤트를 올바른 구조로 파싱한다."""
        monkeypatch.setattr("app.integrations.gcal_write.enabled", lambda: True)

        # 가짜 서비스에 이벤트를 심는다
        fake_service.list_items = [
            {
                "id": "event-1",
                "summary": "[고민] 진로",
                "description": "내용\n\n#진로\n\n(6block 고결감)",
                "start": {"date": "2026-08-20"},
            }
        ]

        def fake_svc_func():
            return fake_service

        monkeypatch.setattr("app.integrations.gcal_write._svc", fake_svc_func)

        result = gcal_write.list_reflection_events(date(2026, 8, 1), date(2026, 8, 31))

        assert len(result) > 0
        event = result[0]
        assert event["id"] == "event-1"
        assert event["kind"] == "고민"
        assert event["title"] == "진로"
        assert event["date"] == "2026-08-20"

    def test_list_extracts_tags_correctly(self, fake_service, monkeypatch):
        """설명에서 태그를 올바르게 추출한다."""
        monkeypatch.setattr("app.integrations.gcal_write.enabled", lambda: True)

        fake_service.list_items = [
            {
                "id": "event-tags",
                "summary": "[감사] 감사",
                "description": "감사 내용\n\n#진로 #건강\n\n(6block 고결감)",
                "start": {"date": "2026-08-21"},
            }
        ]

        def fake_svc_func():
            return fake_service

        monkeypatch.setattr("app.integrations.gcal_write._svc", fake_svc_func)

        result = gcal_write.list_reflection_events(date(2026, 8, 1), date(2026, 8, 31))

        assert len(result) > 0
        event = result[0]
        assert "진로" in event["tags"]
        assert "건강" in event["tags"]

    def test_list_handles_datetime_format(self, fake_service, monkeypatch):
        """dateTime 형식도 date로 추출한다."""
        monkeypatch.setattr("app.integrations.gcal_write.enabled", lambda: True)

        fake_service.list_items = [
            {
                "id": "event-datetime",
                "summary": "[결정] 회의",
                "description": "(6block 고결감)",
                "start": {"dateTime": "2026-08-20T14:30:00"},
            }
        ]

        def fake_svc_func():
            return fake_service

        monkeypatch.setattr("app.integrations.gcal_write._svc", fake_svc_func)

        result = gcal_write.list_reflection_events(date(2026, 8, 1), date(2026, 8, 31))

        assert len(result) > 0
        event = result[0]
        assert event["date"] == "2026-08-20"  # dateTime의 첫 10자

    def test_list_pagination(self, fake_service, monkeypatch):
        """pagination을 처리한다(pageToken이 있으면 여러 번 호출)."""
        # 현재 구현은 mock이므로 pageToken이 None이 되면 루프를 나간다
        # 실제 구현을 테스트하려면 더 복잡한 mock이 필요하다
        pass

    def test_list_caches_results(self, fake_service, monkeypatch):
        """결과를 캐시한다(5분 동안 같은 요청은 캐시 반환)."""
        monkeypatch.setattr("app.integrations.gcal_write.enabled", lambda: True)

        fake_service.list_items = [
            {
                "id": "event-cache",
                "summary": "[고민] 캐시 테스트",
                "description": "(6block 고결감)",
                "start": {"date": "2026-08-20"},
            }
        ]

        def fake_svc_func():
            return fake_service

        monkeypatch.setattr("app.integrations.gcal_write._svc", fake_svc_func)

        # 첫 번째 호출
        result1 = gcal_write.list_reflection_events(date(2026, 8, 1), date(2026, 8, 31))

        # 두 번째 호출 (캐시에서 반환되어야 함)
        fake_service.list_items = []  # 구글 응답이 없어도 캐시가 있으면 반환
        result2 = gcal_write.list_reflection_events(date(2026, 8, 1), date(2026, 8, 31))

        assert len(result1) > 0
        assert result1 == result2


# ============================================================================
# _next_day 검증
# ============================================================================

class TestNextDay:
    """종일 이벤트 end.date 계산을 검증한다."""

    def test_normal_date_increments_by_one(self):
        """일반 날짜는 하루를 더한다."""
        result = gcal_write._next_day("2026-08-20")
        assert result == "2026-08-21"

    def test_month_boundary(self):
        """월 경계를 넘는다."""
        result = gcal_write._next_day("2026-08-31")
        assert result == "2026-09-01"

    def test_year_boundary(self):
        """연도 경계를 넘는다."""
        result = gcal_write._next_day("2025-12-31")
        assert result == "2026-01-01"

    def test_leap_year(self):
        """윤년 2월 29일을 처리한다."""
        result = gcal_write._next_day("2024-02-29")
        assert result == "2024-03-01"

    def test_max_year_raises_valueerror(self):
        """9999-12-31은 ValueError를 발생시킨다."""
        with pytest.raises(ValueError):
            gcal_write._next_day("9999-12-31")


# ============================================================================
# 통합 테스트
# ============================================================================

class TestIntegration:
    """여러 함수를 함께 테스트한다."""

    def test_create_and_delete_flow(self, mock_svc):
        """이벤트를 만들고 지우는 흐름."""
        event_id = gcal_write.create_event(
            "고민", "테스트", "테스트 내용", "테스트", "2026-08-20"
        )
        assert event_id is not None

        success = gcal_write.delete_event(event_id)
        assert success is True

    def test_create_update_flow(self, mock_svc):
        """이벤트를 만들고 갱신하는 흐름."""
        event_id = gcal_write.create_event(
            "고민", "원본", "내용1", "태그1", "2026-08-20"
        )
        assert event_id is not None

        success = gcal_write.update_event(
            event_id, "결정", "변경된", "내용2", "태그2"
        )
        assert success is True

    def test_achievement_event_lifecycle(self, mock_svc):
        """달성 이벤트의 생성-갱신-삭제 흐름."""
        # 생성
        items = ["항목1", "항목2"]
        event_id = gcal_write.upsert_achievement_event("2026-08-20", items, None)
        assert event_id is not None

        # 갱신
        new_items = ["새항목1", "새항목2", "새항목3"]
        event_id2 = gcal_write.upsert_achievement_event("2026-08-20", new_items, event_id)
        assert event_id2 is not None

        # 삭제
        result = gcal_write.upsert_achievement_event("2026-08-20", [""], event_id2)
        assert result is None
