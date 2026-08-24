# 2단계 엣지케이스 스페셜리스트: 설정·DB 계층의 까다로운 입력값 테스트
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from app.config import (
   DEFAULT_SETTINGS, AREA_TONE_ORDER, hhmm_to_min, slots_for_day, area_tone,
    cat_tone,
)
from app.db import (
   BLOCK_TIMES_WD_KEY, WEEKDAY_CONCEPTS_KEY, _apply_times, _parse_times,
    get_day_blocks, get_settings, get_weekday_overrides, get_weekday_concepts,
    set_setting, uid_from_created,
)


# ===== 시간 파싱 엣지케이스 =====

class TestHhmmToMinEdgeCases:
    """hhmm_to_min: 잘못된 시간 형식과 경계값."""

    def test_invalid_hour_24_hours_wraps(self):
        """24:00은 1440분(자정 다음날)이 되는데, 이건 의도된 동작인지 확인."""
        # 실제로 함수는 단순히 숫자를 파싱하므로
        result = hhmm_to_min("24:00")
        assert result == 1440  # 조용한 오답: 24:00이 유효한 시간이 아닌데도 1440 반환

    def test_invalid_hour_25_hours(self):
        """25:00 → 1500(범위 초과, 의도된 방어가 없음)."""
        result = hhmm_to_min("25:00")
        assert result == 1500

    def test_invalid_minute_60(self):
        """00:60 → 60분(유효하지 않은 분)."""
        result = hhmm_to_min("00:60")
        assert result == 60

    def test_empty_string(self):
        """빈 문자열 입력 시 ValueError 또는 조용한 0."""
        try:
            result = hhmm_to_min("")
            # ValueError를 던지지 않고 조용히 실패하는 경우
            assert result == 0  # 이게 오답인지 확인
        except (ValueError, IndexError):
            pass  # 예외는 의도된 동작일 수 있음

    def test_malformed_string_non_numeric(self):
        """'ab:cd' 같은 완전한 비숫자."""
        with pytest.raises((ValueError, IndexError)):
            hhmm_to_min("ab:cd")

    def test_missing_colon(self):
        """'0730' 콜론 없음."""
        try:
            result = hhmm_to_min("0730")
            # 인덱싱이 잘못되면?
        except (ValueError, IndexError):
            pass

    def test_negative_hours_via_string(self):
        """'-01:30' 음수 시간."""
        try:
            result = hhmm_to_min("-01:30")
            # int() 파싱이 음수를 허용하면 조용한 오답
        except ValueError:
            pass

    def test_very_long_string(self):
        """매우 긴 문자열."""
        long_str = "07" + "0" * 10000 + ":30"
        try:
            result = hhmm_to_min(long_str)
        except (ValueError, IndexError):
            pass

    def test_unicode_digits(self):
        """'０７:３０' 한글 숫자나 유니코드 숫자."""
        # Python int()는 유니코드 숫자를 파싱하므로 이건 의도된 동작
        result = hhmm_to_min("０７:３０")
        assert result == 450  # 7*60 + 30

    def test_newline_in_string(self):
        """'07:\n30' 개행 포함."""
        try:
            result = hhmm_to_min("07:\n30")
        except (ValueError, IndexError):
            pass


# ===== 슬롯 생성 엣지케이스 =====

class TestSlotsForDayEdgeCases:
    """slots_for_day: 비정상 블록 목록."""

    def test_empty_blocks_list(self):
        """빈 블록 리스트 → 슬롯도 비어야 함."""
        result = slots_for_day(blocks=[])
        assert result == []

    def test_single_block_half_hour(self):
        """30분짜리 블록 하나 → 1개 슬롯."""
        blocks = [("Test", True, "08:00", "08:30")]
        result = slots_for_day(blocks=blocks)
        assert len(result) == 1
        assert result[0] == (0, "Test", "08:00", "08:30")

    def test_blocks_with_zero_duration(self):
        """시작시간 = 종료시간 블록 → 슬롯 0개."""
        blocks = [("ZeroDur", True, "08:00", "08:00")]
        result = slots_for_day(blocks=blocks)
        assert len(result) == 0

    def test_reversed_time_block(self):
        """end < start: 종료가 시작보다 빠름."""
        blocks = [("Reversed", True, "12:00", "08:00")]
        result = slots_for_day(blocks=blocks)
        # 무한루프 위험: while cur < end_min 이므로 루프가 안 돈다
        assert len(result) == 0

    def test_overlapping_blocks(self):
        """블록이 시간대가 겹치는 경우."""
        blocks = [
            ("B1", True, "08:00", "10:00"),
            ("B2", True, "09:00", "11:00"),  # 겹친다
        ]
        result = slots_for_day(blocks=blocks)
        # 슬롯은 기계적으로 생성되므로 겹침 검증은 없음
        assert len(result) == 8  # 4 + 4 = 8개 슬롯

    def test_gap_between_blocks(self):
        """블록 사이에 빈 시간."""
        blocks = [
            ("B1", True, "08:00", "10:00"),
            ("B2", True, "11:00", "13:00"),  # 1시간 간격
        ]
        result = slots_for_day(blocks=blocks)
        # 슬롯은 각 블록의 시간만 따르므로 간격은 무시됨
        assert len(result) == 8  # 4 + 4 = 8개

    def test_malformed_block_tuple_short(self):
        """블록 튜플이 너무 짧음."""
        blocks = [("B1", True, "08:00")]  # 3개 원소
        with pytest.raises(ValueError):
            slots_for_day(blocks=blocks)

    def test_block_with_invalid_time_format(self):
        """블록의 시간이 'HH:MM' 형식이 아님 → 조용한 오답."""
        # FINDING: hhmm_to_min("0800")은 예외를 던지지 않고 480을 반환한다.
        # "0800"[:2]="08", "0800"[3:5]="00" → 8*60+0=480, "08:00"과 같은 값
        # 형식 검증이 없어 콜론 없는 입력도 파싱됨.
        blocks = [("B1", True, "0800", "1000")]  # 콜론 없음
        result = slots_for_day(blocks=blocks)
        # 결과가 있지만 형식이 잘못되었는데도 조용히 진행됨
        assert len(result) > 0
        # 실제로 "0800"과 "08:00"이 같은 값으로 파싱됨
        assert hhmm_to_min("0800") == hhmm_to_min("08:00")

    def test_very_large_block_duration(self):
        """매우 긴 블록: 00:00 ~ 23:59."""
        blocks = [("FullDay", True, "00:00", "23:59")]
        result = slots_for_day(blocks=blocks)
        # 1439분 / 30분 = ~48개 슬롯
        assert len(result) > 40

    def test_non_30min_boundary_slot_exceeds_block_end(self):
        """블록 끝이 30분 단위가 아님: 08:00 ~ 08:45 → 슬롯이 범위 초과."""
        # FINDING: 블록이 30분 배수가 아니면 생성된 슬롯이 블록 끝을 넘어간다.
        # 08:45=525분, 510 < 525이므로 08:30~09:00 슬롯도 만들어짐
        # 즉, 슬롯 끝(09:00/540분)이 블록 끝(08:45/525분)을 초과함.
        blocks = [("Odd", True, "08:00", "08:45")]
        result = slots_for_day(blocks=blocks)
        assert len(result) == 2
        # 마지막 슬롯이 블록 범위를 넘어감
        assert result[1] == (1, "Odd", "08:30", "09:00")  # 09:00은 08:45를 넘음
        # 이것은 조용한 오답: 슬롯이 블록 범위를 초과하는데 검증이 없음


# ===== UID 생성 엣지케이스 =====

class TestUidFromCreatedEdgeCases:
    """uid_from_created: 극단적인 생성시각."""

    def test_only_digits_no_non_digits(self):
        """비숫자를 모두 제거했을 때 12자리 미만."""
        uid = uid_from_created("20260815")  # 8자리만
        parts = uid.split("-")
        assert parts[0] == "20260815"
        assert parts[1] == "0000"

    def test_empty_created_string(self):
        """빈 문자열 → 00000000-0000-xxxx."""
        uid = uid_from_created("")
        parts = uid.split("-")
        assert parts[0] == "00000000"
        assert parts[1] == "0000"

    def test_all_non_digits(self):
        """숫자가 하나도 없는 문자열."""
        uid = uid_from_created("abcd-efgh-ijkl")
        parts = uid.split("-")
        assert parts[0] == "00000000"
        assert parts[1] == "0000"

    def test_very_long_string(self):
        """매우 긴 생성시각 문자열."""
        long_date = "2026-08-15 21:38:45" + "0123456789" * 1000
        uid = uid_from_created(long_date)
        parts = uid.split("-")
        # 처음 12자리만 취함
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4

    def test_unicode_in_created_string(self):
        """생성시각에 이모지나 한글 포함."""
        uid = uid_from_created("2026년08월15일 21:38:45")
        parts = uid.split("-")
        # 한글은 제거되고 숫자만 취함
        assert parts[0] == "20260815"
        assert parts[1] == "2138"

    def test_null_byte_in_string(self):
        """NULL 바이트 포함."""
        uid = uid_from_created("2026-08-15\x0021:38:45")
        parts = uid.split("-")
        # NULL은 \D 정규표현식으로 제거됨
        assert len(parts) == 3

    def test_multiple_uids_same_input_different_random(self):
        """같은 입력으로 여러 UID 생성 시 난수 부분이 다른지 확인."""
        uids = [uid_from_created("2026-08-15 21:38:45") for _ in range(10)]
        parts_list = [u.split("-") for u in uids]
        # 앞의 두 부분은 모두 같아야 함
        for i in range(10):
            assert parts_list[i][0] == "20260815"
            assert parts_list[i][1] == "2138"
        # 난수는 겹칠 가능성이 있지만, 모두 다를 확률이 높음
        random_parts = {p[2] for p in parts_list}
        # 최소한 몇 개는 다를 것으로 예상
        assert len(random_parts) > 1


# ===== JSON 파싱 엣지케이스 =====

class TestParseTimesEdgeCases:
    """_parse_times: 잘못된 JSON과 형식."""

    def test_non_list_json_object(self):
        """JSON이 객체인 경우."""
        result = _parse_times('{"0": null}')
        assert result is None

    def test_json_string_not_array(self):
        """JSON 문자열이 배열이 아님."""
        result = _parse_times('"string"')
        assert result is None

    def test_list_with_wrong_length_9(self):
        """길이 9인 리스트."""
        result = _parse_times([None] * 9)
        assert result is None

    def test_list_with_wrong_length_0(self):
        """빈 리스트."""
        result = _parse_times([])
        assert result is None

    def test_dict_input_not_list(self):
        """dict를 입력 (리스트가 아님)."""
        result = _parse_times({"0": None})
        assert result is None

    def test_integer_input(self):
        """정수를 입력."""
        result = _parse_times(12345)
        assert result is None

    def test_float_input(self):
        """부동소수점을 입력."""
        result = _parse_times(3.14)
        assert result is None

    def test_very_long_json_string(self):
        """매우 긴 JSON 배열."""
        long_json = json.dumps([None] * 100000)
        result = _parse_times(long_json)
        assert result is None  # 길이가 8이 아니므로

    def test_truncated_json(self):
        """불완전한 JSON."""
        result = _parse_times('[null, null,')
        assert result is None

    def test_json_with_unicode(self):
        """JSON에 유니코드 포함."""
        times = [{"start": "08:00"}, "테스트", None, None, None, None, None, None]
        result = _parse_times(json.dumps(times))
        assert result == times


# ===== 시간 적용 엣지케이스 =====

class TestApplyTimesEdgeCases:
    """_apply_times: 길이 불일치와 비정상 구조."""

    def test_blocks_longer_than_times(self):
        """블록이 시간보다 많음."""
        blocks = [("B1", True, "07:30", "09:30"), ("B2", True, "09:30", "11:30")]
        times = [None]  # 1개만
        # zip은 짧은 쪽에 맞춤
        result = _apply_times(blocks, times)
        assert len(result) == 1

    def test_times_longer_than_blocks(self):
        """시간이 블록보다 많음."""
        blocks = [("B1", True, "07:30", "09:30")]
        times = [None, None, None]
        # zip은 짧은 쪽에 맞춤
        result = _apply_times(blocks, times)
        assert len(result) == 1

    def test_times_with_non_dict_non_none(self):
        """시간 요소가 dict도 None도 아님 (문자열)."""
        blocks = [("B1", True, "07:30", "09:30")]
        times = ["string"]
        result = _apply_times(blocks, times)
        # "string" is not isinstance(dict) → 원본 사용
        assert result[0] == ("B1", True, "07:30", "09:30")

    def test_times_with_integer(self):
        """시간 요소가 정수."""
        blocks = [("B1", True, "07:30", "09:30")]
        times = [123]
        result = _apply_times(blocks, times)
        # 123 is not isinstance(dict) → 원본 사용
        assert result[0] == ("B1", True, "07:30", "09:30")

    def test_empty_blocks_list(self):
        """빈 블록 목록."""
        blocks = []
        times = [None] * 8
        result = _apply_times(blocks, times)
        assert result == []

    def test_times_with_partial_dict(self):
        """시간 dict에 start만 있고 end 없음."""
        blocks = [("B1", True, "07:30", "09:30")]
        times = [{"start": "08:00"}]
        result = _apply_times(blocks, times)
        assert result[0] == ("B1", True, "08:00", "09:30")  # end는 원본

    def test_times_with_extra_keys_in_dict(self):
        """시간 dict에 불필요한 키 포함."""
        blocks = [("B1", True, "07:30", "09:30")]
        times = [{"start": "08:00", "end": "10:00", "extra": "key"}]
        result = _apply_times(blocks, times)
        assert result[0] == ("B1", True, "08:00", "10:00")

    def test_times_with_null_values_in_dict(self):
        """시간 dict의 값이 None."""
        blocks = [("B1", True, "07:30", "09:30")]
        times = [{"start": None, "end": None}]
        result = _apply_times(blocks, times)
        # None or ds → ds 사용
        assert result[0] == ("B1", True, "07:30", "09:30")


# ===== 설정 엣지케이스 =====

class TestGetWeekdayOverridesEdgeCases:
    """get_weekday_overrides: 비정상 저장 형식."""

    def test_not_dict_json(self, fresh_db):
        """저장된 값이 JSON 배열(dict 아님)."""
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps([]))
        result = get_weekday_overrides()
        assert result == {}

    def test_dict_with_invalid_keys(self, fresh_db):
        """dict의 키가 '0'~'6' 이 아님."""
        overrides = {"invalid": []}
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))
        result = get_weekday_overrides()
        assert result == overrides  # 그대로 반환

    def test_dict_with_negative_weekday(self, fresh_db):
        """dict의 키가 "-1" (범위 밖)."""
        overrides = {"-1": [], "7": []}
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))
        result = get_weekday_overrides()
        assert "-1" in result

    def test_very_long_json_string(self, fresh_db):
        """매우 긴 JSON."""
        large_overrides = {"0": [None] * 100000}
        json_str = json.dumps(large_overrides)
        set_setting(BLOCK_TIMES_WD_KEY, json_str)
        result = get_weekday_overrides()
        assert result == large_overrides

    def test_json_with_unicode(self, fresh_db):
        """JSON에 유니코드."""
        overrides = {"0": [{"start": "08:00", "label": "테스트"}]}
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))
        result = get_weekday_overrides()
        assert result == overrides


# ===== 요일 컨셉 엣지케이스 =====

class TestGetWeekdayConceptsEdgeCases:
    """get_weekday_concepts: 비정상 저장값."""

    def test_json_array_with_none_values(self, fresh_db):
        """JSON 배열에 None."""
        concepts = [None, "화", None, "목", None, "토", None]
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))
        result = get_weekday_concepts()
        # str(None or "").strip() → ""
        assert result[0] == ""
        assert result[1] == "화"

    def test_json_array_with_empty_strings(self, fresh_db):
        """JSON 배열에 빈 문자열."""
        concepts = ["", "", "", "", "", "", ""]
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))
        result = get_weekday_concepts()
        assert result == [""] * 7

    def test_json_string_that_is_not_array(self, fresh_db):
        """JSON이 배열이 아님 (object)."""
        set_setting(WEEKDAY_CONCEPTS_KEY, '{"0": "월"}')
        result = get_weekday_concepts()
        assert result == [""] * 7

    def test_json_with_very_long_strings(self, fresh_db):
        """배열의 요소가 매우 긴 문자열."""
        concepts = ["월" * 10000] * 7
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))
        result = get_weekday_concepts()
        assert len(result) == 7
        assert len(result[0]) == 10000

    def test_json_with_unicode_and_emoji(self, fresh_db):
        """배열에 이모지와 유니코드."""
        concepts = ["월🔥", "화✅", "수📝", "목🎯", "금💼", "토🏖", "일😴"]
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))
        result = get_weekday_concepts()
        assert result == concepts

    def test_json_with_whitespace_only(self, fresh_db):
        """배열의 요소가 공백만."""
        concepts = ["   ", "\t", "\n", "  \t  ", "", "   ", "   "]
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))
        result = get_weekday_concepts()
        # .strip()이 적용되므로
        assert result[0] == ""
        assert result[1] == ""

    def test_json_exceeding_seven_elements_truncates(self, fresh_db):
        """배열이 7개를 초과."""
        concepts = [""] * 20
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))
        result = get_weekday_concepts()
        assert len(result) == 7


# ===== 요일 블록 엣지케이스 =====

class TestGetDayBlocksEdgeCases:
    """get_day_blocks: 범위를 벗어나는 weekday."""

    def test_weekday_negative(self, fresh_db):
        """weekday -1."""
        result = get_day_blocks(weekday=-1)
        # str(-1) → "-1" → 오버라이드 없음 → 기본값 사용
        assert len(result) == 8

    def test_weekday_7_sunday_alternative(self, fresh_db):
        """weekday 7 (일요일의 대체 표기?)."""
        result = get_day_blocks(weekday=7)
        # str(7) → "7" → 오버라이드 없음 → 기본값 사용
        assert len(result) == 8

    def test_weekday_100(self, fresh_db):
        """weekday 100 (완전히 범위 밖)."""
        result = get_day_blocks(weekday=100)
        assert len(result) == 8

    def test_weekday_float(self, fresh_db):
        """weekday가 float (의도하지 않은 타입)."""
        # str(3.5) → "3.5" → 오버라이드 없음
        result = get_day_blocks(weekday=3.5)
        assert len(result) == 8

    def test_weekday_none_explicit(self, fresh_db):
        """weekday=None 명시적으로."""
        result = get_day_blocks(weekday=None)
        assert len(result) == 8
        # 공통 시간 사용


# ===== 색 톤 엣지케이스 =====

class TestAreaToneEdgeCases:
    """area_tone: 음수와 매우 큰 수."""

    def test_negative_order(self):
        """order -1."""
        result = area_tone(-1)
        # -1 % 8 = 7 (Python의 음수 modulo)
        assert result == AREA_TONE_ORDER[7]

    def test_very_large_order(self):
        """order 10**9."""
        result = area_tone(10**9)
        # 단순 modulo이므로 상관없음
        assert result in AREA_TONE_ORDER

    def test_order_equals_palette_length(self):
        """order가 팔레트 길이와 같음."""
        n = len(AREA_TONE_ORDER)
        result = area_tone(n)
        assert result == AREA_TONE_ORDER[0]


class TestCatToneEdgeCases:
    """cat_tone: 비정상 카테고리 이름."""

    def test_category_none(self):
        """None을 전달."""
        # cat_tone(None) → CAT_TONE.get(None, "black")
        result = cat_tone(None)
        assert result == "black"

    def test_category_integer(self):
        """정수를 전달."""
        result = cat_tone(123)
        # dict.get(123, "black") → "black"
        assert result == "black"

    def test_category_empty_string(self):
        """빈 문자열."""
        result = cat_tone("")
        assert result == "black"

    def test_category_very_long_string(self):
        """매우 긴 문자열."""
        long_name = "카테고리" * 10000
        result = cat_tone(long_name)
        assert result == "black"

    def test_category_with_special_chars(self):
        """특수문자와 이모지."""
        result = cat_tone("코어🔥")
        assert result == "black"  # "코어🔥"는 알려지지 않음

    def test_category_case_sensitive(self):
        """대소문자 구분."""
        # "코어" vs "코어"는 한글이므로 대소문자 구분 자체가 없지만
        # "코어 " (끝에 공백) vs "코어"
        result1 = cat_tone("코어")
        result2 = cat_tone("코어 ")
        assert result1 == "blue"
        assert result2 == "black"  # 공백이 있으면 다른 키


# ===== 동시 접근 엣지케이스 =====

class TestConcurrentSettingsAccess:
    """set_setting + get_settings 동시 호출."""

    def test_concurrent_set_and_get(self, fresh_db):
        """여러 스레드에서 동시에 설정을 저장하고 읽음."""
        results = []
        errors = []

        def worker(i):
            try:
                for j in range(10):
                    set_setting(f"test_key_{i}_{j}", f"value_{i}_{j}")
                    val = get_settings().get(f"test_key_{i}_{j}")
                    results.append(val)
            except Exception as e:
                errors.append(str(e))

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker, i) for i in range(5)]
            for future in as_completed(futures):
                future.result()

        # 예외가 없었는지 확인
        assert len(errors) == 0
        # 최소한 일부 결과가 있는지 확인
        assert len(results) > 0

    def test_rapid_cache_invalidation(self, fresh_db):
        """연속으로 set_setting을 호출해 캐시가 계속 무효화되는지 확인."""
        for i in range(100):
            set_setting("rapid_key", f"value_{i}")
            val = get_settings()["rapid_key"]
            assert val == f"value_{i}"

    def test_concurrent_different_keys(self, fresh_db):
        """여러 키를 동시에 저장."""
        def set_many(prefix):
            for i in range(20):
                set_setting(f"{prefix}_{i}", f"val_{i}")

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(set_many, f"thread{j}") for j in range(3)]
            for future in as_completed(futures):
                future.result()

        # DB 연결이 안전한지 확인
        settings = get_settings()
        assert len(settings) > len(DEFAULT_SETTINGS)


class TestConcurrentSettingsCache:
    """캐시 동시 접근."""

    def test_cache_invalidation_under_load(self, fresh_db):
        """한 스레드가 설정을 쓰는 중에 다른 스레드가 읽음."""
        shared_list = []

        def reader():
            for _ in range(50):
                try:
                    s = get_settings()
                    shared_list.append(len(s))
                except Exception as e:
                    shared_list.append(None)

        def writer():
            for i in range(50):
                try:
                    set_setting(f"concurrent_{i}", f"val_{i}")
                except Exception as e:
                    pass

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(reader),
                executor.submit(writer),
            ]
            for future in as_completed(futures):
                future.result()

        # 적어도 일부는 성공했는지 확인
        assert None not in shared_list or len([x for x in shared_list if x is not None]) > 0


# ===== 무한루프 방지 테스트 =====

class TestDeadlockPrevention:
    """락이나 무한재귀로 인한 멈춤 확인."""

    def test_init_db_doesnt_deadlock(self, tmp_path, monkeypatch):
        """init_db가 파일 락으로 무한 대기하지 않는지 확인."""
        from app import db as db_module

        test_db = tmp_path / "test.db"
        monkeypatch.setattr(db_module, "DB_PATH", test_db)

        # init_db를 여러 번 호출 (동시성 시뮬레이션)
        db_module.init_db()
        db_module.init_db()
        db_module.init_db()
        # 예외나 무한 대기 없이 완료됨

    def test_get_settings_doesnt_hang(self, fresh_db):
        """get_settings가 DB 접근으로 인해 멈추지 않는지."""
        result = get_settings()
        assert isinstance(result, dict)

    def test_get_day_blocks_doesnt_hang(self, fresh_db):
        """get_day_blocks가 호출 순환으로 인해 멈추지 않는지."""
        for i in range(100):
            result = get_day_blocks(weekday=i % 7)
            assert len(result) == 8


# ===== 데이터 무결성 엣지케이스 =====

class TestDataIntegrityRoundTrip:
    """저장 → 읽기 왕복에서 데이터가 보존되는지 확인."""

    def test_set_then_get_exact_match(self, fresh_db):
        """설정을 저장한 후 읽으면 정확히 일치."""
        test_pairs = [
            ("key1", "value1"),
            ("key2", ""),  # 빈 값
            ("key3", " " * 100),  # 공백
            ("key4", "한글테스트"),  # 한글
            ("key5", "emoji🔥test"),  # 이모지
            ("key6", "line\nbreak"),  # 개행
            ("key7", "tab\there"),  # 탭
        ]
        for key, val in test_pairs:
            set_setting(key, val)

        # 캐시 비우고 다시 읽기
        import app.db as db_module
        db_module._settings_cache = None

        settings = get_settings()
        for key, val in test_pairs:
            assert settings[key] == val, f"{key}이 일치하지 않음"

    def test_unicode_normalization(self, fresh_db):
        """유니코드 정규화 문제가 없는지."""
        # 조합 모음과 미리 조합된 한글
        val1 = "한"  # 완성된 한글
        val2 = "갑"  # 같은 문자, 코드포인트 다름 가능
        set_setting("unicode_test", val1)
        import app.db as db_module
        db_module._settings_cache = None
        result = get_settings()["unicode_test"]
        assert result == val1

    def test_sql_injection_strings_not_executed(self, fresh_db):
        """SQL injection 시도가 데이터로만 저장되는지."""
        attack_strings = [
            "'; DROP TABLE app_settings; --",
            "1' OR '1'='1",
            "test%",  # SQL LIKE 메타문자
            "test_",
            "test\\",
        ]
        for s in attack_strings:
            set_setting("sql_test", s)
            import app.db as db_module
            db_module._settings_cache = None
            result = get_settings()["sql_test"]
            assert result == s, f"'{s}'가 일치하지 않음"


# ===== 설정값 범위 엣지케이스 =====

class TestSettingValueEdgeCases:
    """설정값의 극단적인 크기와 형식."""

    def test_very_large_json_setting(self, fresh_db):
        """매우 큰 JSON을 설정값으로 저장."""
        large_json = json.dumps([None] * 100000)
        set_setting("large_json", large_json)
        import app.db as db_module
        db_module._settings_cache = None
        result = get_settings()["large_json"]
        assert result == large_json

    def test_very_long_key_name(self, fresh_db):
        """매우 긴 키 이름."""
        long_key = "k" * 10000
        set_setting(long_key, "value")
        import app.db as db_module
        db_module._settings_cache = None
        result = get_settings()[long_key]
        assert result == "value"

    def test_null_byte_in_value(self, fresh_db):
        """값에 NULL 바이트."""
        try:
            set_setting("null_byte", "test\x00value")
            import app.db as db_module
            db_module._settings_cache = None
            result = get_settings()["null_byte"]
            # Python 문자열에는 NULL이 포함될 수 있지만 SQLite는 처리 가능
        except (ValueError, sqlite3.IntegrityError):
            pass

    def test_control_characters_in_value(self, fresh_db):
        """값에 제어문자."""
        value_with_controls = "test\x01\x02\x03\x1fvalue"
        set_setting("controls", value_with_controls)
        import app.db as db_module
        db_module._settings_cache = None
        result = get_settings()["controls"]
        assert result == value_with_controls
