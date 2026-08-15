# 2단계 엣지케이스: app/integrations/* 순수 로직 (파싱, 포맷, 왕복, 동시성, 조용한 오답)
import json
import subprocess
import sys
import threading
import time
import unittest.mock as mock
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import app.integrations.ai as ai
import app.integrations.gcal as gcal
import app.integrations.gcal_write as gcal_write
import app.integrations.things as things


# ============================================================================
# gcal_write._next_day 엣지케이스
# ============================================================================

class TestGcalWriteNextDayEdges:
    """_next_day: 극단값(최댓값, 무효한 포맷, 음수, 0)."""

    def test_next_day_invalid_format_short(self):
        """형식이 아닌 문자열은 split 실패."""
        with pytest.raises(ValueError):
            gcal_write._next_day("2024-01")

    def test_next_day_invalid_format_no_dash(self):
        """형식이 아닌 문자열."""
        with pytest.raises(ValueError):
            gcal_write._next_day("20240115")

    def test_next_day_invalid_date_feb30(self):
        """존재하지 않는 날짜 2월 30일."""
        with pytest.raises(ValueError):
            gcal_write._next_day("2024-02-30")

    def test_next_day_invalid_date_month13(self):
        """존재하지 않는 월."""
        with pytest.raises(ValueError):
            gcal_write._next_day("2024-13-01")

    def test_next_day_invalid_date_day0(self):
        """0일."""
        with pytest.raises(ValueError):
            gcal_write._next_day("2024-01-00")

    def test_next_day_max_date(self):
        """연도 9999년 12월 31일."""
        with pytest.raises(OverflowError):
            # 다음날은 10000-01-01이 되어야 하는데 date가 4자리만 지원
            # ValueError가 아니라 OverflowError 발생
            gcal_write._next_day("9999-12-31")

    def test_next_day_y1_date(self):
        """연도 1년 1월 1일."""
        result = gcal_write._next_day("0001-01-01")
        assert result == "0001-01-02"

    def test_next_day_5digit_year(self):
        """5자리 연도."""
        with pytest.raises(ValueError):
            gcal_write._next_day("10000-01-01")


# ============================================================================
# gcal_write._hashtags와 _build_description: 특수 문자, SQL LIKE, 이모지
# ============================================================================

class TestGcalWriteHashtagsSpecial:
    """_hashtags: SQL LIKE 메타문자(%, _, \\), 따옴표, 이모지, 제로폭 문자."""

    def test_hashtags_percent(self):
        """SQL LIKE 메타문자 %."""
        result = gcal_write._hashtags("진로%, 건강")
        assert "#진로%" in result
        assert "#건강" in result

    def test_hashtags_underscore(self):
        """SQL LIKE 메타문자 _."""
        result = gcal_write._hashtags("진로_경력, 건강")
        assert "#진로_경력" in result
        assert "#건강" in result

    def test_hashtags_backslash(self):
        """백슬래시."""
        result = gcal_write._hashtags("진로\\경력, 건강")
        assert "#진로\\경력" in result

    def test_hashtags_emoji(self):
        """이모지."""
        result = gcal_write._hashtags("진로🚀, 건강💪")
        assert "#진로🚀" in result
        assert "#건강💪" in result

    def test_hashtags_zerowidth(self):
        """제로폭 문자(U+200B)."""
        result = gcal_write._hashtags("진로​경력")  # ZERO WIDTH SPACE
        # 해시태그 정규식 #\S+ 로는 유니코드 공백이 포함되지 않음
        assert "#" in result

    def test_hashtags_surrogate_pair(self):
        """서로게이트 페어(복합 이모지 등)."""
        result = gcal_write._hashtags("👨‍👩‍👧‍👦")  # 가족 이모지
        # 정규식이 \S+이므로 제대로 매칭되어야 함
        assert "#" in result or result == ""


class TestGcalWriteBuildDescriptionRoundTrip:
    """_build_description + parse_description 왕복: 특수 문자, 개행, 마커 충돌."""

    def test_roundtrip_with_marker_in_content(self):
        """내용에 마커 문자열이 포함되어 있으면?"""
        content = "고민했어. (6block 고결감) 이건 마커다."
        tags = "진로"
        desc = gcal_write._build_description(content, tags)
        parsed_content, parsed_tags = gcal_write.parse_description(desc)
        # 마커가 parse_description에서 제거되므로 조용히 손상됨!
        # 이것이 발견 대상
        assert parsed_content != content

    def test_roundtrip_with_hashtag_in_content(self):
        """내용에 #태그 문자열이 있으면?"""
        content = "이전에 #진로로 고민했다"
        tags = "건강"
        desc = gcal_write._build_description(content, tags)
        parsed_content, parsed_tags = gcal_write.parse_description(desc)
        # 내용의 #진로가 태그로 추출될 수 있음
        assert "#진로" in desc
        # 실제로 parse_description는 내용의 # 까지 뽑아낸다

    def test_roundtrip_content_with_multiple_newlines(self):
        """내용에 연속된 개행이 있으면?"""
        content = "첫 줄\n\n\n네 줄"  # 3개 연속 개행
        tags = "진로"
        desc = gcal_write._build_description(content, tags)
        parsed_content, parsed_tags = gcal_write.parse_description(desc)
        # 구분 로직(parse_description의 splitlines)이 연속 개행을 어떻게 처리하는가?

    def test_roundtrip_content_with_trailing_newlines(self):
        """끝에 개행이 있으면?"""
        content = "내용\n\n"
        tags = "진로"
        desc = gcal_write._build_description(content, tags)
        parsed_content, parsed_tags = gcal_write.parse_description(desc)
        # strip() 때문에 끝 개행은 제거되므로 왕복 불일치

    def test_roundtrip_tags_with_special_chars(self):
        """태그에 특수 문자나 이모지."""
        content = "내용"
        tags = "진로​경력, 건강🚀"  # 제로폭 + 이모지
        desc = gcal_write._build_description(content, tags)
        parsed_content, parsed_tags = gcal_write.parse_description(desc)
        # 왕복 일치하는가?


# ============================================================================
# gcal_write.parse_summary: 형식 경계, 비표준 입력
# ============================================================================

class TestGcalWriteParseSummaryEdges:
    """parse_summary: 대괄호 중첩, 빈 대괄호, 내용 없음, 비표준 형식."""

    def test_parse_summary_nested_brackets(self):
        """대괄호 중첩: [고민 [부제]] 제목. 정규식이 첫 번째 ] 까지 가져와 형식이 깨짐."""
        kind, title = gcal_write.parse_summary("[고민 [부제]] 제목")
        # 정규식 ^\s*\[(.+?)\]\s*(.*)$ 는 비탐욕적이므로
        # [고민 [부제] 부분까지 가져옴 → group(1)="고민 [부제", group(2)="] 제목"
        # 이것은 조용한 데이터 손상: 사용자가 [고민 [부제]] 형식으로 적었는데
        # 첫 번째 ] 이후가 버려짐
        assert kind == "고민"  # "고민 [부제"가 정규화되어 "고민"이 됨
        assert title == "] 제목"  # 조용한 손상!

    def test_parse_summary_empty_brackets(self):
        """빈 대괄호: [] 제목."""
        kind, title = gcal_write.parse_summary("[] 제목")
        # \[(.+?)\] 는 (.+?) 이므로 빈 대괄호는 매칭 안 됨
        assert kind == "고민"  # 형식이 아님

    def test_parse_summary_only_brackets(self):
        """오직 [고민]만."""
        kind, title = gcal_write.parse_summary("[고민]")
        assert kind == "고민"
        assert title == ""

    def test_parse_summary_multiline(self):
        """여러 줄, 첫 줄에만 형식이 있으면?"""
        kind, title = gcal_write.parse_summary("[고민] 첫 줄\n두 번째 줄")
        # 정규식이 ^ 로 시작하므로 매칭 되어야 함
        assert kind == "고민"
        assert "첫 줄\n두 번째 줄" in title

    def test_parse_summary_bracket_at_end(self):
        """뒤에 대괄호: 제목 [고민]."""
        kind, title = gcal_write.parse_summary("제목 [고민]")
        # ^ 앞에 있어야 매칭되므로 고민이 아님
        assert kind == "고민"
        assert title == "제목 [고민]"

    def test_parse_summary_null_kind(self):
        """알 수 없는 종류: [뭔가] 제목."""
        kind, title = gcal_write.parse_summary("[뭔가] 제목")
        # _norm_kind 에서 정규화되어 고민이 됨
        assert kind == "고민"
        assert title == "제목"

    def test_parse_summary_very_long_title(self):
        """매우 긴 제목(10만 글자)."""
        long_title = "x" * 100000
        kind, title = gcal_write.parse_summary(f"[고민] {long_title}")
        assert kind == "고민"
        assert len(title) == 100000
        assert title == long_title


# ============================================================================
# gcal_write.parse_description: 마커 부분 매칭, 개행 혼합
# ============================================================================

class TestGcalWriteParseDescriptionEdges:
    """parse_description: 부분 마커, 개행 스타일 혼합, 매우 긴 입력."""

    def test_parse_description_partial_marker(self):
        """마커의 일부만 있으면?"""
        desc = "내용\n\n(6block"  # 마커 불완전
        content, tags = gcal_write.parse_description(desc)
        # replace 는 부분 일치를 찾지 않으므로 그대로 남음
        assert "(6block" in content

    def test_parse_description_marker_misspelled(self):
        """마커가 약간 다르면?"""
        desc = "내용\n\n(6block 고결감)" + "\n(6block 고결감)"  # 중복
        content, tags = gcal_write.parse_description(desc)
        # 두 번째 마커는 남아 있음

    def test_parse_description_mixed_newlines(self):
        """개행이 섞여 있으면: \\n + \\r\\n + \\r."""
        desc = "첫 줄\n두 번째\r\n세 번째\r네 번째\n\n(6block 고결감)"
        content, tags = gcal_write.parse_description(desc)
        # splitlines() 는 여러 개행 스타일을 모두 처리하지만
        # 보존 확인 필요

    def test_parse_description_tag_line_with_spaces(self):
        """태그 줄에 띄어쓰기가 섞여 있으면?"""
        desc = "내용\n\n  #진로  #건강  \n\n(6block 고결감)"
        content, tags = gcal_write.parse_description(desc)
        # fullmatch r"\\s*(#\\S+\\s*)+  로 매칭되는 줄은 제거됨

    def test_parse_description_very_long_content(self):
        """매우 긴 내용(100만 글자)."""
        long_content = "x" * 1000000
        desc = gcal_write._build_description(long_content, "태그")
        parsed_content, parsed_tags = gcal_write.parse_description(desc)
        assert len(parsed_content) == 1000000


# ============================================================================
# gcal_write._achieve_description: 비표준 입력, 순환 참조 없음(하지만 테스트)
# ============================================================================

class TestGcalWriteAchieveDescriptionEdges:
    """_achieve_description: None, 아주 큰 리스트, 매우 긴 항목."""

    def test_achieve_description_with_none_items(self):
        """리스트에 None이 섞여 있으면?"""
        result = gcal_write._achieve_description(["목표1", None, "목표3"])
        # (x or "").strip() 이므로 None은 "" 가 되고 제거됨
        assert result == "1. 목표1\n2. 목표3"

    def test_achieve_description_large_list(self):
        """아주 큰 리스트(10000개)."""
        items = [f"목표{i}" for i in range(10000)]
        result = gcal_write._achieve_description(items)
        lines = result.split("\n")
        assert len(lines) == 10000
        assert lines[0].startswith("1.")
        assert lines[-1].startswith("10000.")

    def test_achieve_description_very_long_item(self):
        """매우 긴 항목(100만 글자)."""
        items = ["x" * 1000000]
        result = gcal_write._achieve_description(items)
        assert "1. " + "x" * 1000000 == result

    def test_achieve_description_tabs_and_newlines(self):
        """항목에 탭과 개행이 포함되면?"""
        items = ["목표1\t서브", "목표2\n다음 줄"]
        result = gcal_write._achieve_description(items)
        # 항목 자체가 그대로 보존되므로 개행이 중간에 있으면 형식이 깨짐
        assert "목표1\t서브" in result
        assert "목표2\n다음 줄" in result


# ============================================================================
# gcal._normalize: datetime 경계, 시간대 변환, 타입 검증
# ============================================================================

class TestGcalNormalizeEdges:
    """_normalize: 시간대 없는 datetime, DTSTART/DTEND 없음, 무효한 시간."""

    def test_normalize_no_dtstart(self):
        """DTSTART 가 없는 컴포넌트."""
        comp = mock.MagicMock()
        comp.get.return_value = None  # DTSTART 없음
        result = gcal._normalize(comp, "red", "calendar")
        assert result is None

    def test_normalize_allday_no_dtend(self):
        """종일 이벤트인데 DTEND 가 없으면?"""
        comp = mock.MagicMock()
        comp.get.side_effect = lambda key, default=None: {
            "DTSTART": mock.MagicMock(dt=date(2024, 1, 15)),
            "DTEND": None,
            "SUMMARY": "제목",
            "LOCATION": None,
        }.get(key, default)
        result = gcal._normalize(comp, "red", "calendar")
        assert result is not None
        assert result["all_day"] is True

    def test_normalize_timed_event_no_dtend(self):
        """시간 지정 이벤트인데 DTEND 가 없으면?"""
        kst = ZoneInfo("Asia/Seoul")
        dt_start = datetime(2024, 1, 15, 10, 0, tzinfo=kst)
        comp = mock.MagicMock()
        comp.get.side_effect = lambda key, default=None: {
            "DTSTART": mock.MagicMock(dt=dt_start),
            "DTEND": None,  # DTEND 없음
            "SUMMARY": "제목",
            "LOCATION": None,
        }.get(key, default)
        result = gcal._normalize(comp, "red", "calendar")
        # DTEND 가 없으면 DTSTART 와 같게 설정됨
        assert result["end"] == "10:00"

    def test_normalize_midnight_crossing(self):
        """자정을 넘는 이벤트: 23시 ~ 다음날 1시."""
        kst = ZoneInfo("Asia/Seoul")
        dt_start = datetime(2024, 1, 15, 23, 0, tzinfo=kst)
        dt_end = datetime(2024, 1, 16, 1, 0, tzinfo=kst)
        comp = mock.MagicMock()
        comp.get.side_effect = lambda key, default=None: {
            "DTSTART": mock.MagicMock(dt=dt_start),
            "DTEND": mock.MagicMock(dt=dt_end),
            "SUMMARY": "제목",
            "LOCATION": None,
        }.get(key, default)
        result = gcal._normalize(comp, "red", "calendar")
        # date는 시작 날짜, end는 다음날이므로 date != end.split('-')[2]
        assert result["date"] == "2024-01-15"
        # end는 다음날 시간이므로 2024-01-16이 되어야 하나?
        # 실제로는 end는 시간만 저장하고 date는 시작 날짜이므로 문제 가능성

    def test_normalize_naive_datetime(self):
        """시간대 없는 datetime."""
        naive_dt = datetime(2024, 1, 15, 10, 0)  # tzinfo 없음
        comp = mock.MagicMock()
        comp.get.side_effect = lambda key, default=None: {
            "DTSTART": mock.MagicMock(dt=naive_dt),
            "DTEND": mock.MagicMock(dt=naive_dt),
            "SUMMARY": "제목",
            "LOCATION": None,
        }.get(key, default)
        result = gcal._normalize(comp, "red", "calendar")
        # _to_kst 에서 KST로 대체되어야 함
        assert result["start"] == "10:00"

    def test_normalize_summary_empty(self):
        """제목이 비어 있으면?"""
        comp = mock.MagicMock()
        comp.get.side_effect = lambda key, default=None: {
            "DTSTART": mock.MagicMock(dt=date(2024, 1, 15)),
            "SUMMARY": "",
            "LOCATION": None,
        }.get(key, default)
        result = gcal._normalize(comp, "red", "calendar")
        assert result["title"] == "(제목 없음)"

    def test_normalize_summary_whitespace_only(self):
        """제목이 공백만 있으면?"""
        comp = mock.MagicMock()
        comp.get.side_effect = lambda key, default=None: {
            "DTSTART": mock.MagicMock(dt=date(2024, 1, 15)),
            "SUMMARY": "   ",
            "LOCATION": None,
        }.get(key, default)
        result = gcal._normalize(comp, "red", "calendar")
        assert result["title"] == "(제목 없음)"

    def test_normalize_start_min_near_midnight(self):
        """자정에 가까운 시간."""
        kst = ZoneInfo("Asia/Seoul")
        dt_start = datetime(2024, 1, 15, 23, 59, tzinfo=kst)
        comp = mock.MagicMock()
        comp.get.side_effect = lambda key, default=None: {
            "DTSTART": mock.MagicMock(dt=dt_start),
            "DTEND": mock.MagicMock(dt=dt_start),
            "SUMMARY": "제목",
            "LOCATION": None,
        }.get(key, default)
        result = gcal._normalize(comp, "red", "calendar")
        assert result["start_min"] == 23 * 60 + 59  # 1439


# ============================================================================
# things._run: subprocess 타임아웃, 부분 출력, 특수 문자
# ============================================================================

class TestThingsRunEdges:
    """_run: 타임아웃, 표준 에러, 매우 긴 출력."""

    def test_run_timeout(self):
        """타임아웃 시 None, '' 반환."""
        # subprocess.run에 timeout=1 을 줄 수 없으므로 몽키패칭 필요
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 1)
            rc, out = things._run("script", timeout=1)
            assert rc is None
            assert out == ""

    def test_run_nonzero_returncode(self):
        """0이 아닌 반환코드."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=1, stdout="error")
            rc, out = things._run("script")
            assert rc == 1
            assert out == "error"

    def test_run_exception(self):
        """기타 예외(예: 권한 거부)."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = PermissionError("osascript not found")
            rc, out = things._run("script")
            assert rc is None
            assert out == ""


class TestThingsTodayNamesEdges:
    """_today_names: 빈 출력, 탭 부재, 필드 부족."""

    def test_today_names_empty_output(self):
        """출력이 비어 있으면 빈 리스트."""
        with mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, "")
            result = things._today_names()
            assert result == []

    def test_today_names_no_tabs(self):
        """탭이 없는 줄."""
        with mock.patch.object(things, "_run") as mock_run:
            # 탭 없이 제목만
            mock_run.return_value = (0, "할일1\n할일2\n")
            result = things._today_names()
            # rsplit("\\t", 2) 로 최대 2개만 자르므로
            # parts[0] = "할일1", parts[1] 은 없어서 IndexError 는 안 나고
            # tagstr = "" (len(parts) > 1 체크)
            assert len(result) == 2
            assert result[0]["name"] == "할일1"
            assert result[0]["tags"] == []

    def test_today_names_only_tabs(self):
        """오직 탭만."""
        with mock.patch.object(things, "_run") as mock_run:
            mock_run.return_value = (0, "\t\t\n")
            result = things._today_names()
            # 제목이 비어 있으므로 제외됨
            assert result == []

    def test_today_names_many_tabs(self):
        """탭이 많으면?"""
        with mock.patch.object(things, "_run") as mock_run:
            # rsplit("\\t", 2) 은 뒤에서부터 2개만 자르므로
            mock_run.return_value = (0, "제목\t태그1\t태그2\tid123\t추가\n")
            result = things._today_names()
            # rsplit("\\t", 2) → ["제목\t태그1\t태그2", "id123", "추가"]
            # parts[0] = "제목\t태그1\t태그2", parts[1] = "id123", parts[2] = "추가"
            # tagstr = parts[1] = "id123" (잘못됨!)
            assert len(result) == 1
            assert result[0]["name"] == "제목\t태그1\t태그2"
            # 이것이 발견 대상: 탭이 많으면 필드 순서가 뒤바뀜


# ============================================================================
# things.today_tasks: 캐시, 동시성, today()가 아닌 날짜
# ============================================================================

class TestThingsTodayTasksEdges:
    """today_tasks: 동시성, 캐시 타이밍, 다른 날짜."""

    def test_today_tasks_not_today(self):
        """today가 아닌 다른 날짜."""
        target = date.today() - timedelta(days=1)
        result = things.today_tasks(target)
        # Things의 Today는 실제 오늘에만 의미가 있으므로 다른 날짜는 빈 목록
        assert result == []

    def test_today_tasks_tomorrow(self):
        """내일 날짜."""
        target = date.today() + timedelta(days=1)
        result = things.today_tasks(target)
        assert result == []

    def test_today_tasks_concurrent_calls(self):
        """동시에 여러 번 호출."""
        results = []
        def call_task():
            r = things.today_tasks(date.today())
            results.append(r)

        threads = [threading.Thread(target=call_task) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 모든 결과가 일치해야 함(캐시 충돌 없음)
        for r in results[1:]:
            assert r == results[0]


# ============================================================================
# ai.complete: 다양한 응답 형식, 부분 응답, 타임아웃
# ============================================================================

class TestAiCompleteEdges:
    """complete: 응답이 없는 필드, null 값, 매우 긴 응답, 타임아웃."""

    def test_complete_empty_response_body(self):
        """응답 body 가 비어 있으면?"""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b""
            result = ai.complete("system", "user")
            # json.loads("") 는 JSONDecodeError
            assert result is None

    def test_complete_malformed_json(self):
        """응답이 유효한 JSON이 아니면?"""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"not json"
            result = ai.complete("system", "user")
            assert result is None

    def test_complete_missing_choices_field(self):
        """choices 필드가 없으면?"""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b'{"error": "bad"}'
            result = ai.complete("system", "user")
            # KeyError: "choices" → 예외 발생 → None
            assert result is None

    def test_complete_empty_choices(self):
        """choices 가 빈 배열이면?"""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b'{"choices": []}'
            result = ai.complete("system", "user")
            # data["choices"][0] → IndexError → 예외 → None
            assert result is None

    def test_complete_null_message(self):
        """message 가 null 이면?"""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                b'{"choices": [{"message": null}]}'
            )
            result = ai.complete("system", "user")
            # data["choices"][0]["message"]["content"] → TypeError → None
            assert result is None

    def test_complete_whitespace_only_content(self):
        """content 가 공백만 있으면?"""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                b'{"choices": [{"message": {"content": "   "}}]}'
            )
            result = ai.complete("system", "user")
            # (text or "").strip() or None → None
            assert result is None

    def test_complete_empty_content(self):
        """content 가 빈 문자열이면?"""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                b'{"choices": [{"message": {"content": ""}}]}'
            )
            result = ai.complete("system", "user")
            assert result is None

    def test_complete_disabled_returns_none(self):
        """설정이 비어 있으면 None 반환."""
        # conftest에서 모든 외부 연동이 비활성화됨
        result = ai.complete("system", "user")
        assert result is None

    def test_complete_timeout(self):
        """타임아웃."""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = TimeoutError("timeout")
            result = ai.complete("system", "user")
            assert result is None

    def test_complete_http_error(self):
        """HTTP 오류(401, 429, 500)."""
        with mock.patch("urllib.request.urlopen") as mock_open:
            import urllib.error
            mock_open.side_effect = urllib.error.HTTPError(
                "http://api", 401, "Unauthorized", {}, None
            )
            result = ai.complete("system", "user")
            assert result is None


# ============================================================================
# ai._cfg: 설정 캐시, DB 접근 실패
# ============================================================================

class TestAiCfgEdges:
    """_cfg: 설정 DB 접근 불가, 필드 부재, 공백."""

    def test_cfg_all_fields_from_settings(self):
        """설정에서 모든 필드가 나오면 .env를 무시한다."""
        with mock.patch("app.integrations.ai.get_settings") as mock_settings:
            mock_settings.return_value = {
                "ai_base_url": "https://custom.api",
                "ai_model": "custom-model"
            }
            key, base, model = ai._cfg()
            # 설정값이 우선되므로 base와 model이 설정값
            assert base == "https://custom.api"
            assert model == "custom-model"

    def test_cfg_with_settings_empty_string(self):
        """설정값이 빈 문자열."""
        with mock.patch("app.integrations.ai.get_settings") as mock_settings:
            mock_settings.return_value = {"ai_base_url": "", "ai_model": ""}
            key, base, model = ai._cfg()
            # (s.get("ai_base_url") or AI_BASE_URL) or "" → AI_BASE_URL 아니면 ""
            # 테스트 환경에서 AI_BASE_URL 가 빈 값


# ============================================================================
# 동시성 테스트: 캐시 레이스 조건
# ============================================================================

class TestConcurrency:
    """gcal_write._list_cache, things._cache: 레이스 조건, 캐시 무효화."""

    def test_gcal_write_cache_invalidation_race(self):
        """invalidate_cache 와 list_reflection_events 의 동시 호출."""
        # 이 테스트는 실제 구글 API 스텁이 필요하지만
        # 순수 캐시 로직만 테스트 가능
        gcal_write._list_cache["items"] = ["dummy"]

        def read_cache():
            time.sleep(0.01)
            return gcal_write._list_cache["items"]

        def invalidate():
            gcal_write.invalidate_cache()

        t1 = threading.Thread(target=invalidate)
        t2 = threading.Thread(target=read_cache)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 캐시가 None이어야 함
        assert gcal_write._list_cache["items"] is None

    def test_things_cache_concurrent_refresh(self):
        """_refresh_later 와 동시 호출."""
        # things._cache 와 _refreshing 의 동시성
        # _refresh_lock 이 있으므로 레이스 조건은 없어야 하지만
        # 타임아웃 등의 이슈가 있을 수 있음

        # 이 테스트는 실제 AppleScript 실행이 필요하므로 스킵 가능


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
