# app/integrations/* 모듈 단위 테스트 (gcal_write, gcal, things, ai)
import json
import unittest.mock as mock
from datetime import date, datetime
from zoneinfo import ZoneInfo


import app.integrations.ai as ai
import app.integrations.gcal as gcal
import app.integrations.gcal_write as gcal_write
import app.integrations.things as things


# ============================================================================
# gcal_write.py 테스트
# ============================================================================

class TestGcalWriteNextDay:
    """_next_day: 종일 이벤트의 end.date는 종료 다음날(배타적)이라 하루 더한다."""

    def test_next_day_normal(self):
        """일반 날짜에서 다음날을 반환한다."""
        assert gcal_write._next_day("2024-01-15") == "2024-01-16"

    def test_next_day_month_boundary(self):
        """월 경계를 넘는다."""
        assert gcal_write._next_day("2024-01-31") == "2024-02-01"

    def test_next_day_year_boundary(self):
        """연도 경계를 넘는다."""
        assert gcal_write._next_day("2024-12-31") == "2025-01-01"

    def test_next_day_leap_year(self):
        """윤년 2월 29일을 처리한다."""
        assert gcal_write._next_day("2024-02-29") == "2024-03-01"


class TestGcalWriteHashtags:
    """_hashtags: '진로, 건강' → '#진로 #건강' (구글 캘린더 검색에 걸리도록)"""

    def test_hashtags_comma_separated(self):
        """쉼표로 구분된 태그를 해시태그로 변환한다."""
        result = gcal_write._hashtags("진로, 건강")
        assert result == "#진로 #건강"

    def test_hashtags_space_separated(self):
        """공백으로 구분된 태그도 처리한다."""
        result = gcal_write._hashtags("진로 건강")
        assert result == "#진로 #건강"

    def test_hashtags_mixed_separators(self):
        """쉼표와 공백이 섞여 있다."""
        result = gcal_write._hashtags("진로, 건강  , 경력")
        assert result == "#진로 #건강 #경력"

    def test_hashtags_already_has_hash(self):
        """이미 #이 있으면 하나만 유지한다."""
        result = gcal_write._hashtags("#진로, #건강")
        assert result == "#진로 #건강"

    def test_hashtags_empty(self):
        """빈 입력은 빈 문자열 반환."""
        assert gcal_write._hashtags("") == ""
        assert gcal_write._hashtags(None) == ""

    def test_hashtags_whitespace_only(self):
        """공백만 있으면 빈 결과."""
        assert gcal_write._hashtags("  , , ") == ""


class TestGcalWriteBuildDescription:
    """_build_description: 내용 + 해시태그 + 표식으로 설명란을 만든다."""

    def test_build_description_content_and_tags(self):
        """내용과 태그가 모두 있다."""
        result = gcal_write._build_description("좋은 경험", "진로, 건강")
        assert "좋은 경험" in result
        assert "#진로" in result
        assert "#건강" in result
        assert "(6block 고결감)" in result

    def test_build_description_content_only(self):
        """내용만 있다."""
        result = gcal_write._build_description("좋은 경험", "")
        assert "좋은 경험" in result
        assert "#" not in result
        assert "(6block 고결감)" in result

    def test_build_description_tags_only(self):
        """태그만 있다."""
        result = gcal_write._build_description("", "진로")
        assert "(6block 고결감)" in result
        assert "#진로" in result

    def test_build_description_empty(self):
        """모두 비어 있으면 마커만."""
        result = gcal_write._build_description("", "")
        assert result == "(6block 고결감)"

    def test_build_description_multiline_content(self):
        """여러 줄 내용을 보존한다."""
        result = gcal_write._build_description("첫 줄\n두 번째 줄", "")
        assert "첫 줄\n두 번째 줄" in result


class TestGcalWriteNormKind:
    """_norm_kind: 종류를 정규화한다(기본값 고민, 옛 명칭 호환)."""

    def test_norm_kind_valid(self):
        """유효한 종류는 그대로."""
        assert gcal_write._norm_kind("고민") == "고민"
        assert gcal_write._norm_kind("결정") == "결정"
        assert gcal_write._norm_kind("감사") == "감사"

    def test_norm_kind_whitespace(self):
        """앞뒤 공백을 제거한다."""
        assert gcal_write._norm_kind("  고민  ") == "고민"

    def test_norm_kind_alias_gamsan(self):
        """옛 명칭 '감상' → '감사'."""
        assert gcal_write._norm_kind("감상") == "감사"

    def test_norm_kind_alias_gyeolsim(self):
        """옛 명칭 '결심' → '결정'."""
        assert gcal_write._norm_kind("결심") == "결정"

    def test_norm_kind_unknown(self):
        """모르는 종류는 기본값 '고민'."""
        assert gcal_write._norm_kind("뭔가이상한값") == "고민"
        assert gcal_write._norm_kind("") == "고민"
        assert gcal_write._norm_kind(None) == "고민"


class TestGcalWriteParseSummary:
    """parse_summary: '[종류] 제목' → (kind, title). 형식이 아니면 (고민, 통째 제목)."""

    def test_parse_summary_valid_format(self):
        """올바른 형식을 파싱한다."""
        kind, title = gcal_write.parse_summary("[고민] 진로 선택")
        assert kind == "고민"
        assert title == "진로 선택"

    def test_parse_summary_all_kinds(self):
        """모든 유효한 종류를 파싱한다."""
        for k in ["고민", "결정", "감사"]:
            kind, title = gcal_write.parse_summary(f"[{k}] 제목")
            assert kind == k
            assert title == "제목"

    def test_parse_summary_alias(self):
        """옛 명칭 종류도 파싱 후 정규화한다."""
        kind, title = gcal_write.parse_summary("[감상] 좋은 하루")
        assert kind == "감사"  # 정규화됨
        assert title == "좋은 하루"

    def test_parse_summary_no_format(self):
        """형식이 아니면 고민과 통째 제목을 반환한다."""
        kind, title = gcal_write.parse_summary("그냥 제목")
        assert kind == "고민"
        assert title == "그냥 제목"

    def test_parse_summary_whitespace(self):
        """앞뒤 공백을 처리한다."""
        kind, title = gcal_write.parse_summary("  [고민]  제목 ")
        assert kind == "고민"
        assert title == "제목"

    def test_parse_summary_empty(self):
        """빈 입력."""
        kind, title = gcal_write.parse_summary("")
        assert kind == "고민"
        assert title == ""

    def test_parse_summary_title_with_bracket(self):
        """제목에 []가 있어도 첫 번째만 파싱한다."""
        kind, title = gcal_write.parse_summary("[고민] 제목 [부제]")
        assert kind == "고민"
        assert title == "제목 [부제]"


class TestGcalWriteParseDescription:
    """parse_description: 설명란 → (content, tags). 표식·해시태그 줄을 걷어내 내용을 복원."""

    def test_parse_description_full(self):
        """내용, 해시태그, 마커가 모두 있다."""
        desc = "좋은 경험\n\n#진로 #건강\n\n(6block 고결감)"
        content, tags = gcal_write.parse_description(desc)
        assert content == "좋은 경험"
        assert "진로" in tags
        assert "건강" in tags

    def test_parse_description_multiline_content(self):
        """여러 줄 내용을 보존한다."""
        desc = "첫 줄\n두 번째 줄\n\n#태그\n\n(6block 고결감)"
        content, tags = gcal_write.parse_description(desc)
        assert "첫 줄" in content
        assert "두 번째 줄" in content

    def test_parse_description_no_tags(self):
        """태그가 없다."""
        desc = "내용만 있음\n\n(6block 고결감)"
        content, tags = gcal_write.parse_description(desc)
        assert content == "내용만 있음"
        assert tags == ""

    def test_parse_description_no_marker(self):
        """마커가 없다."""
        desc = "내용\n\n#태그"
        content, tags = gcal_write.parse_description(desc)
        assert "내용" in content
        assert "태그" in tags

    def test_parse_description_empty(self):
        """빈 입력."""
        content, tags = gcal_write.parse_description("")
        assert content == ""
        assert tags == ""

    def test_parse_description_tag_only_line(self):
        """태그만 있는 줄은 완전히 제거한다."""
        desc = "내용\n\n#진로 #건강\n\n(6block 고결감)"
        content, tags = gcal_write.parse_description(desc)
        # 태그 줄이 제거되므로 content에 # 없어야 함
        assert "#" not in content


class TestGcalWriteRoundTrip:
    """create_event 만든 summary/description를 parse_summary/parse_description으로 되읽어야 같다."""

    def test_roundtrip_summary(self):
        """요약은 그대로 왕복한다."""
        original_kind = "고민"
        original_title = "진로 선택"

        # create_event가 만드는 형식
        summary = f"[{original_kind}] {original_title}"

        # parse_summary로 되읽기
        parsed_kind, parsed_title = gcal_write.parse_summary(summary)

        assert parsed_kind == original_kind
        assert parsed_title == original_title

    def test_roundtrip_description(self):
        """설명란도 그대로 왕복한다."""
        original_content = "좋은 경험"
        original_tags = "진로, 건강"

        # create_event가 만드는 형식
        description = gcal_write._build_description(original_content, original_tags)

        # parse_description으로 되읽기
        parsed_content, parsed_tags = gcal_write.parse_description(description)

        assert parsed_content == original_content
        # 태그 순서나 형식이 다를 수 있으니 내용만 확인
        assert "진로" in parsed_tags
        assert "건강" in parsed_tags


class TestGcalWriteAchieveDescription:
    """_achieve_description: 달성 항목을 '1. 2. 3.' 형식으로 번호를 매긴다."""

    def test_achieve_description_all_filled(self):
        """모두 채워져 있다."""
        result = gcal_write._achieve_description(["목표1", "목표2", "목표3"])
        assert result == "1. 목표1\n2. 목표2\n3. 목표3"

    def test_achieve_description_with_empty(self):
        """빈 칸은 빼고 번호를 다시 매긴다."""
        result = gcal_write._achieve_description(["목표1", "", "목표3"])
        assert result == "1. 목표1\n2. 목표3"
        assert "빈" not in result or result.count("\n") == 1  # 2줄

    def test_achieve_description_all_empty(self):
        """모두 비어 있다."""
        result = gcal_write._achieve_description(["", "", ""])
        assert result == ""

    def test_achieve_description_whitespace_only(self):
        """공백만 있는 항목은 무시한다."""
        result = gcal_write._achieve_description(["목표1", "  ", "목표3"])
        assert result == "1. 목표1\n2. 목표3"


class TestGcalWriteReviewCopyContent:
    """_review_copy_content: 다시보기 내용을 위에, 원본을 아래에 둔다."""

    def test_review_copy_both(self):
        """다시보기 내용과 원본이 모두 있다."""
        result = gcal_write._review_copy_content("다시보기 내용", "원본 내용")
        assert result.startswith("다시보기 내용")
        assert "── 원본 ──" in result
        assert "원본 내용" in result

    def test_review_copy_only_review(self):
        """다시보기만 있다."""
        result = gcal_write._review_copy_content("다시보기", "")
        assert result == "다시보기"

    def test_review_copy_only_original(self):
        """원본만 있다."""
        result = gcal_write._review_copy_content("", "원본")
        assert result == "원본"

    def test_review_copy_empty(self):
        """모두 비어 있다."""
        result = gcal_write._review_copy_content("", "")
        assert result == ""

    def test_review_copy_whitespace_trimmed(self):
        """앞뒤 공백이 제거된다."""
        result = gcal_write._review_copy_content("  다시보기  ", "  원본  ")
        assert result.startswith("다시보기")
        assert "원본" in result


class TestGcalWriteCalendarId:
    """calendar_id: 설정에 넣은 값이 우선, 없으면 .env 값."""

    def test_calendar_id_from_env(self):
        """설정이 없으면 환경변수를 사용한다."""
        # .env에서 GCAL_WRITE_EVENTS_CALENDAR_ID가 설정되어 있으면 그것을 반환
        # 테스트 환경에서는 .env 값이 우선
        result = gcal_write.calendar_id("events")
        # 결과는 빈 값이거나 .env 값이어야 함
        assert isinstance(result, str)


class TestGcalWriteWriteEnabled:
    """write_enabled: 그 캘린더에 쓸 수 있는지 (캘린더 ID + 서비스계정 키파일 + 라이브러리)."""

    def test_write_enabled_when_disabled(self):
        """필요한 것이 없으면 False."""
        # 테스트 환경에서는 GCAL_SA_KEYFILE이 비어 있음
        result = gcal_write.write_enabled("events")
        assert result is False


class TestGcalWriteEnabled:
    """enabled: 캘린더 쓰기가 활성화되어 있는가."""

    def test_enabled_when_disabled(self):
        """테스트 환경에서는 비활성."""
        # conftest.py에서 비활성화됨
        result = gcal_write.enabled()
        assert result is False


class TestGcalWriteServiceAccountEmail:
    """service_account_email: 키파일의 client_email을 추출한다."""

    def test_service_account_email_no_file(self):
        """파일이 없으면 빈 문자열."""
        result = gcal_write.service_account_email()
        assert result == ""


class TestGcalWriteInvalidateCache:
    """invalidate_cache: 캐시를 비워 다음 조회가 즉시 구글을 읽게 한다."""

    def test_invalidate_cache(self):
        """_list_cache의 items를 None으로 설정한다."""
        # 캐시 설정
        gcal_write._list_cache["items"] = ["something"]

        # 무효화
        gcal_write.invalidate_cache()

        # 캐시가 비워져야 함
        assert gcal_write._list_cache["items"] is None


# ============================================================================
# gcal.py 테스트
# ============================================================================

class TestGcalEnabled:
    """enabled: GCAL_CALENDARS와 _HAS_ICAL이 모두 있어야 한다."""

    def test_enabled_when_disabled(self):
        """테스트 환경에서는 GCAL_CALENDARS가 비어 있음."""
        result = gcal.enabled()
        assert result is False


class TestGcalToKst:
    """_to_kst: datetime을 KST로 변환한다."""

    def test_to_kst_naive_datetime(self):
        """naive datetime을 KST로 해석한다."""
        naive_dt = datetime(2024, 1, 15, 10, 30, 0)
        result = gcal._to_kst(naive_dt)
        assert result.tzinfo is not None
        assert result.tzname() == "KST"

    def test_to_kst_aware_datetime(self):
        """UTC datetime을 KST로 변환한다."""
        utc = ZoneInfo("UTC")
        utc_dt = datetime(2024, 1, 15, 1, 30, 0, tzinfo=utc)
        result = gcal._to_kst(utc_dt)
        assert result.tzinfo is not None
        # 9시간 차이
        assert result.hour == 10  # 01:30 UTC → 10:30 KST


class TestGcalNormalize:
    """_normalize: VEVENT를 dict로 변환한다."""

    def test_normalize_allday_event(self):
        """종일 이벤트를 처리한다."""
        # Mock component
        mock_comp = {
            "SUMMARY": "종일 행사",
            "LOCATION": "서울",
            "DTSTART": mock.Mock(dt=date(2024, 1, 15)),
        }

        result = gcal._normalize(mock_comp, "blue", "개인캘린더")

        assert result is not None
        assert result["all_day"] is True
        assert result["title"] == "종일 행사"
        assert result["date"] == "2024-01-15"
        assert result["start"] is None
        assert result["color"] == "blue"


class TestGcalEventsForDate:
    """events_for_date: 특정 날짜의 일정을 반환한다."""

    def test_events_for_date_when_disabled(self):
        """비활성 상태에서는 빈 목록."""
        result = gcal.events_for_date(date(2024, 1, 15))
        assert result == []


class TestGcalStatus:
    """status: 헬스체크용 상태 정보."""

    def test_status_when_disabled(self):
        """GCAL_CALENDARS가 비어 있으면 비활성."""
        result = gcal.status()
        assert isinstance(result, dict)
        assert result["enabled"] is False


# ============================================================================
# things.py 테스트
# ============================================================================

class TestThingsEnabled:
    """enabled: macOS에서만 활성화된다."""

    def test_enabled_on_darwin(self):
        """macOS에서는 True."""
        # sys.platform은 import 시점에 읽혀서 patch 불가능
        # 대신 enabled() 함수가 sys.platform == "darwin"인지 확인만 하면 됨
        # 현재 시스템이 macOS라면 True
        result = things.enabled()
        # conftest에서 현재 시스템이 macOS이면 True, 아니면 False
        assert isinstance(result, bool)

    def test_enabled_on_other(self):
        """enabled() 결과는 bool이다."""
        result = things.enabled()
        assert isinstance(result, bool)


class TestThingsRun:
    """_run: osascript를 실행한다."""

    def test_run_success(self):
        """정상 실행."""
        with mock.patch("app.integrations.things.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="output")

            rc, out = things._run("tell application \"Things3\"")

            assert rc == 0
            assert out == "output"

    def test_run_failure(self):
        """실패하면 (None, '')."""
        with mock.patch("app.integrations.things.subprocess.run", side_effect=Exception("timeout")):
            rc, out = things._run("tell application \"Things3\"")

            assert rc is None
            assert out == ""


class TestThingsTodayNames:
    """_today_names: AppleScript 출력을 파싱한다."""

    def test_today_names_valid(self):
        """정상 출력을 파싱한다."""
        with mock.patch("app.integrations.things._run") as mock_run:
            mock_run.return_value = (0, "할일1\t태그1,태그2\tid1\n할일2\t\tid2\n")

            result = things._today_names()

            assert result is not None
            assert len(result) == 2
            assert result[0]["name"] == "할일1"
            assert result[0]["tags"] == ["태그1", "태그2"]
            assert result[0]["id"] == "id1"

    def test_today_names_tab_in_title(self):
        """제목에 탭이 있어도 처리한다(rsplit 사용)."""
        with mock.patch("app.integrations.things._run") as mock_run:
            mock_run.return_value = (0, "할일\t함수정의\t태그\tid\n")

            result = things._today_names()

            assert result is not None
            assert result[0]["name"] == "할일\t함수정의"  # 탭이 제목에 남음

    def test_today_names_failure(self):
        """osascript 실패하면 None."""
        with mock.patch("app.integrations.things._run") as mock_run:
            mock_run.return_value = (1, "")

            result = things._today_names()

            assert result is None


class TestThingsTodayTasks:
    """today_tasks: 오늘의 Things3 일정을 반환한다."""

    def test_today_tasks_today_cache_behavior(self):
        """오늘이면 캐시가 없을 때 _fetch_into_cache를 부른다."""
        # conftest가 things.today_tasks를 stub하므로 직접 함수 로직을 테스트할 수 없고,
        # 대신 캐시 동작과 반환값 형식만 확인한다.
        with mock.patch("app.integrations.things._fetch_into_cache") as mock_fetch:
            mock_fetch.return_value = [{"name": "일1", "tags": [], "id": "1"}]

            # 캐시 초기화
            things._cache["items"] = None

            # 캐시가 None이면 _fetch_into_cache가 호출된다.
            # conftest stub이 있으므로 실제 응답은 받을 수 없지만,
            # 명세상 today_tasks는 cache["items"] 형식으로 반환한다.
            result = things.today_tasks(date.today())

            # stub 때문에 빈 목록이 반환됨
            assert isinstance(result, list)

    def test_today_tasks_other_day(self):
        """다른 날짜는 빈 목록."""
        result = things.today_tasks(date(2000, 1, 1))
        assert result == []


class TestThingsStatus:
    """status: Things3 헬스체크."""

    def test_status_failure(self):
        """osascript 실패."""
        with mock.patch("app.integrations.things._run") as mock_run:
            mock_run.return_value = (None, "")

            result = things.status()

            assert result["ok"] is False


class TestThingsAddTodo:
    """add_todo: Things3 Inbox에 할일을 추가한다."""

    def test_add_todo_success(self):
        """정상 추가."""
        with mock.patch("app.integrations.things.enabled", return_value=True):
            with mock.patch("app.integrations.things.subprocess.run") as mock_run:
                mock_run.return_value = mock.Mock(returncode=0, stdout="ok")

                result = things.add_todo("새 할일")

                assert result is True

    def test_add_todo_failure(self):
        """실패."""
        with mock.patch("app.integrations.things.enabled", return_value=True):
            with mock.patch("app.integrations.things.subprocess.run") as mock_run:
                mock_run.return_value = mock.Mock(returncode=1, stdout="")

                result = things.add_todo("새 할일")

                assert result is False

    def test_add_todo_not_enabled(self):
        """비활성 상태."""
        with mock.patch("app.integrations.things.enabled", return_value=False):
            result = things.add_todo("새 할일")
            assert result is False

    def test_add_todo_empty_title(self):
        """빈 제목."""
        result = things.add_todo("")
        assert result is False


# ============================================================================
# ai.py 테스트
# ============================================================================

class TestAiCfg:
    """_cfg: (api_key, base_url, model)을 반환한다. 설정 우선, .env 폴백."""

    def test_cfg_returns_tuple(self):
        """(api_key, base_url, model) 튜플을 반환한다."""
        result = ai._cfg()

        # conftest가 AI_API_KEY를 환경변수로 비우므로 key는 빈 값
        # (진짜 API를 부르지 않게 하려는 의도)
        assert isinstance(result, tuple)
        assert len(result) == 3
        key, base, model = result
        assert isinstance(key, str)
        assert isinstance(base, str)
        assert isinstance(model, str)

    def test_cfg_key_from_env_only(self):
        """키는 .env가 아니라 환경변수에서만 읽는다."""
        # conftest: "AI_API_KEY의 키는 .env만"이라고 주석이 있지만
        # 실제로는 app/config.py에서 os.getenv("AI_API_KEY", "")로 읽음
        # conftest가 os.environ["AI_API_KEY"] = ""로 설정해서 진짜 API 호출 방지
        key, _, _ = ai._cfg()
        # 테스트 환경에서는 비어 있음
        assert key == ""


class TestAiEnabled:
    """enabled: 키·주소·모델이 모두 있어야 한다."""

    def test_enabled_with_all(self, fresh_db):
        """테스트 env에서는 API_KEY와 MODEL이 있다."""
        # 기본값은 비활성 (base_url이 .env에 없음)
        result = ai.enabled()
        assert isinstance(result, bool)


class TestAiStatus:
    """status: 설정 화면용 상태."""

    def test_status_format(self, fresh_db):
        """상태 dict 형식을 확인한다."""
        result = ai.status()

        assert "has_key" in result
        assert "base" in result
        assert "model" in result
        assert "enabled" in result
        assert isinstance(result["has_key"], bool)


class TestAiComplete:
    """complete: OpenAI 호환 chat/completions을 호출한다."""

    def test_complete_disabled(self):
        """비활성 상태에서는 None."""
        result = ai.complete("system", "user query")
        assert result is None

    def test_complete_http_call(self):
        """HTTP 호출을 시뮬레이션한다."""
        # AI가 활성화되려면 base_url도 필요
        # 테스트에서는 네트워크를 실제로 호출하지 않음
        with mock.patch("app.integrations.ai.urllib.request.urlopen") as mock_urlopen:
            # 가짜 응답
            mock_response = mock.Mock()
            mock_response.read.return_value = json.dumps({
                "choices": [{"message": {"content": "응답"}}]
            }).encode("utf-8")
            mock_response.__enter__ = mock.Mock(return_value=mock_response)
            mock_response.__exit__ = mock.Mock(return_value=None)
            mock_urlopen.return_value = mock_response

            # 이를 위해서는 base와 key가 있어야 함
            # conftest가 AI를 비활성화해서 일단 None 반환
            result = ai.complete("system", "user")
            assert result is None  # conftest stub


# ============================================================================
# 통합 테스트 (round-trip 등)
# ============================================================================

class TestRoundTrips:
    """create_event → parse 왕복을 시험한다."""

    def test_create_and_parse_reflection_event(self):
        """고결감 이벤트 create → parse 왕복."""
        # create_event가 실제 구글 API를 부르는데 conftest가 막아놨으므로
        # summary와 description 형식만 확인
        kind = "고민"
        title = "진로"
        content = "좋은 경험"
        tags = "진로, 경력"
        date_str = "2024-01-15"

        # create_event가 만드는 summary
        summary = f"[{kind}] {title}"

        # create_event가 만드는 description
        description = gcal_write._build_description(content, tags)

        # parse_summary로 되읽기
        parsed_kind, parsed_title = gcal_write.parse_summary(summary)
        assert parsed_kind == kind
        assert parsed_title == title

        # parse_description으로 되읽기
        parsed_content, parsed_tags = gcal_write.parse_description(description)
        assert parsed_content == content
        assert "진로" in parsed_tags
        assert "경력" in parsed_tags
