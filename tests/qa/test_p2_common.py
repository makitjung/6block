# app/common.py 공통 도우미 함수의 2단계 엣지케이스 테스트 (조용한 오답 중심)
import sqlite3
import time
from datetime import date, datetime, timedelta

import pytest

from app.common import (
   int_id, opt_id, _ko_weekday, _pretty_date, _short_date, asset_ver,
    _client_settings, today_str, week_start, _weekday_of, _skeleton_matches_config,
    _day_has_content, ensure_day_skeleton, _name_override, _split3, _join3,
    _parse_date, lt_tree_order, lt_leaves, week_lt_items, week_todos, _like_pattern,
    _rule_distribute, _ai_split, SLOT_HAS_CONTENT, VERSIONED_ASSETS, SQLITE_MAX_INT,
    RowId, KST,
)


# ============================================================================
# int_id / opt_id: 극단값 and 타입 오류
# ============================================================================

class TestIntIdEdges:
    """int_id의 경계값 및 타입 오류"""

    def test_int_id_exactly_max(self):
        """정확히 최댓값"""
        assert int_id(str(SQLITE_MAX_INT)) == SQLITE_MAX_INT

    def test_int_id_one_over_max(self):
        """최댓값 초과"""
        with pytest.raises(ValueError):
            int_id(str(SQLITE_MAX_INT + 1))

    def test_int_id_two_to_power_63(self):
        """2**63 (SQLite 범위 초과)"""
        with pytest.raises(ValueError):
            int_id(str(2**63))

    def test_int_id_two_to_power_64(self):
        """2**64 (훨씬 초과)"""
        with pytest.raises(ValueError):
            int_id(str(2**64))

    def test_int_id_whitespace_padding(self):
        """공백이 있는 입력"""
        assert int_id("  123  ") == 123
        with pytest.raises(ValueError):
            int_id("  0  ")

    def test_int_id_leading_zeros(self):
        """선행 0"""
        assert int_id("00123") == 123
        assert int_id("00001") == 1

    def test_int_id_negative_large(self):
        """매우 작은 음수"""
        with pytest.raises(ValueError):
            int_id(str(-SQLITE_MAX_INT))

    def test_int_id_type_error_dict(self):
        """dict 입력"""
        with pytest.raises((TypeError, ValueError)):
            int_id({})

    def test_int_id_type_error_list(self):
        """list 입력"""
        with pytest.raises((TypeError, ValueError)):
            int_id([])

    def test_int_id_float_string(self):
        """부동소수점 문자열"""
        with pytest.raises(ValueError):
            int_id("123.456")

    def test_int_id_scientific_notation(self):
        """과학 표기법"""
        with pytest.raises(ValueError):
            int_id("1e10")


class TestOptIdEdges:
    """opt_id의 경계값 및 타입 오류"""

    def test_opt_id_whitespace_only(self):
        """공백만 있는 경우"""
        assert opt_id("   ") is None

    def test_opt_id_newline(self):
        """개행"""
        assert opt_id("\n") is None

    def test_opt_id_tab(self):
        """탭"""
        assert opt_id("\t") is None

    def test_opt_id_false_like_string(self):
        """'False' 문자열"""
        assert opt_id("False") is None

    def test_opt_id_list_converted_to_string(self):
        """리스트 (문자열화됨)"""
        # 만약 이게 str로 변환되면 '[1]' 같은 문자열이 되어 int() 실패
        assert opt_id([1]) is None

    def test_opt_id_dict_converted(self):
        """dict 입력"""
        assert opt_id({}) is None


# ============================================================================
# 날짜 함수들: 경계 날짜 및 유효성
# ============================================================================

class TestDateFunctions:
    """날짜 함수의 엣지케이스"""

    def test_ko_weekday_all_days(self):
        """모든 요일 정확성"""
        # 2026-08-03 (월) ~ 2026-08-09 (일)
        expected = ["월", "화", "수", "목", "금", "토", "일"]
        for i, exp in enumerate(expected):
            date_str = f"2026-08-{3+i:02d}"
            assert _ko_weekday(date_str) == exp

    def test_ko_weekday_invalid_format(self):
        """잘못된 형식"""
        with pytest.raises(ValueError):
            _ko_weekday("08-03-2026")
        with pytest.raises(ValueError):
            _ko_weekday("2026/08/03")

    def test_ko_weekday_leap_year_feb29(self):
        """윤년 2월 29일"""
        # 2024년은 윤년, 2/29 존재
        result = _ko_weekday("2024-02-29")
        assert result in ["월", "화", "수", "목", "금", "토", "일"]

    def test_ko_weekday_non_leap_feb29(self):
        """평년 2월 29일 (불가)"""
        with pytest.raises(ValueError):
            _ko_weekday("2025-02-29")

    def test_pretty_date_boundary_months(self):
        """월 경계 날짜"""
        result = _pretty_date("2026-01-01")
        assert "1월" in result and "1일" in result
        result = _pretty_date("2026-12-31")
        assert "12월" in result and "31일" in result

    def test_short_date_leading_zeros(self):
        """단일 숫자 월/일"""
        assert _short_date("2026-01-01") == "1.1"
        assert _short_date("2026-01-09") == "1.9"
        assert _short_date("2026-09-01") == "9.1"

    def test_weekday_of_invalid_format(self):
        """_weekday_of 형식 오류"""
        with pytest.raises(ValueError):
            _weekday_of("2026/08/03")

    def test_weekday_of_zero_is_monday(self):
        """월요일은 0"""
        assert _weekday_of("2026-08-03") == 0

    def test_weekday_of_six_is_sunday(self):
        """일요일은 6"""
        assert _weekday_of("2026-08-09") == 6


# ============================================================================
# week_start: 요일 계산 정확성
# ============================================================================

class TestWeekStart:
    """week_start의 경계값"""

    def test_week_start_monday_returns_itself(self):
        """월요일이면 자기 자신"""
        d = date(2026, 8, 3)  # 월요일
        assert week_start(d) == d

    def test_week_start_sunday_goes_back(self):
        """일요일이면 지난 월요일로"""
        d = date(2026, 8, 9)  # 일요일
        expected = date(2026, 8, 3)  # 그 주 월요일
        assert week_start(d) == expected

    def test_week_start_month_boundary(self):
        """월 경계를 넘을 때"""
        d = date(2026, 8, 3)  # 8월 3일 (월요일)
        # 일주일 전 월요일은 7월 27일
        prev_week = week_start(d - timedelta(days=7))
        assert prev_week.month == 7

    def test_week_start_year_boundary(self):
        """연도 경계를 넘을 때"""
        d = date(2026, 1, 5)  # 1월 5일 (월요일)
        prev_week = week_start(d - timedelta(days=7))
        assert prev_week.year == 2025


# ============================================================================
# today_str: KST 시간대 정확성
# ============================================================================

class TestTodayStr:
    """today_str의 정확성"""

    def test_today_str_format(self):
        """YYYY-MM-DD 형식"""
        result = today_str()
        assert len(result) == 10
        assert result[4] == '-' and result[7] == '-'

    def test_today_str_parseable(self):
        """파싱 가능한 형식"""
        result = today_str()
        parsed = datetime.strptime(result, "%Y-%m-%d")
        assert isinstance(parsed, datetime)


# ============================================================================
# _skeleton_matches_config: DB 비교 정확성
# ============================================================================

class TestSkeletonMatchesConfig:
    """_skeleton_matches_config의 조용한 오답"""

    def test_matches_exact(self, conn):
        """정확히 일치"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        assert _skeleton_matches_config(conn, date_str)

    def test_not_matches_missing_block(self, conn):
        """블록 누락"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        # 첫 번째 블록 삭제
        first_block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? ORDER BY block_order LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("DELETE FROM blocks WHERE id = ?", (first_block_id,))
        assert not _skeleton_matches_config(conn, date_str)

    def test_not_matches_different_time(self, conn):
        """시간 변경"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        # 첫 블록의 시작 시간 변경
        first_block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? ORDER BY block_order LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE blocks SET start_time = '09:30' WHERE id = ?",
            (first_block_id,),
        )
        assert not _skeleton_matches_config(conn, date_str)

    def test_not_matches_different_label(self, conn):
        """블록 라벨 변경"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        first_block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? ORDER BY block_order LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE blocks SET block_label = 'OTHER' WHERE id = ?",
            (first_block_id,),
        )
        assert not _skeleton_matches_config(conn, date_str)

    def test_matches_nonexistent_date(self, conn):
        """골격이 아예 없는 날짜는 '설정과 같다'가 아니다.

        여기서 True 가 나오면 ensure_day_skeleton 이 그날을 안 만들고 지나간다.
        """
        assert _skeleton_matches_config(conn, "2025-01-01") is False


# ============================================================================
# _day_has_content: 조용한 오답 중심 (SQL 복잡도 높음)
# ============================================================================

class TestDayHasContent:
    """_day_has_content의 조용한 오답 (누락된 컬럼 감지)"""

    def test_empty_day_no_content(self, conn):
        """아무 내용 없는 날"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        assert not _day_has_content(conn, date_str)

    def test_slot_do_text(self, conn):
        """슬롯 do_text"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("UPDATE slots SET do_text = '계획' WHERE id = ?", (slot_id,))
        assert _day_has_content(conn, date_str)

    def test_slot_do_text_with_routine(self, conn):
        """루틴 슬롯은 do_text만으로 내용 아님"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        # is_routine=1인 슬롯 생성
        slot_id = conn.execute(
            "INSERT INTO slots (date, block_id, slot_index, is_routine, do_text, start_time, end_time, updated_at) "
            "VALUES (?, (SELECT id FROM blocks WHERE date = ? LIMIT 1), 99, 1, '루틴', '09:00', '09:30', datetime('now')) "
            "RETURNING id",
            (date_str, date_str),
        ).fetchone()["id"]
        # 고정 할일이 채운 칸은 사람이 적은 것이 아니라 '내용 있음'이 아니다.
        # 여기서 True 가 되면 그 날은 세션 시간을 바꿔도 새 시간표로 안 바뀐다.
        assert slot_id, "루틴 슬롯이 안 들어갔다"
        assert _day_has_content(conn, date_str) is False
        # 정확히 테스트하려면 다른 내용도 없어야 함

    def test_slot_did_text(self, conn):
        """슬롯 did_text (한 일)"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("UPDATE slots SET did_text = '했음' WHERE id = ?", (slot_id,))
        assert _day_has_content(conn, date_str)

    def test_slot_done_flag(self, conn):
        """슬롯 done 플래그"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("UPDATE slots SET done = 1 WHERE id = ?", (slot_id,))
        assert _day_has_content(conn, date_str)

    def test_slot_category_id(self, conn):
        """슬롯 category_id (구분)"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        # 구분 1 할당 (구분이 있으면 is_routine은 0이어야 함)
        conn.execute(
            "UPDATE slots SET is_routine = 0, category_id = 1 WHERE id = ?",
            (slot_id,),
        )
        assert _day_has_content(conn, date_str)

    def test_slot_wk_todo(self, conn):
        """슬롯 wk_todo"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("UPDATE slots SET wk_todo = '주할' WHERE id = ?", (slot_id,))
        assert _day_has_content(conn, date_str)

    def test_block_plan_text(self, conn):
        """블록 plan_text"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("UPDATE blocks SET plan_text = '계획' WHERE id = ?", (block_id,))
        assert _day_has_content(conn, date_str)

    def test_block_see_text(self, conn):
        """블록 see_text"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("UPDATE blocks SET see_text = '봄' WHERE id = ?", (block_id,))
        assert _day_has_content(conn, date_str)

    def test_block_name(self, conn):
        """블록 name"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1", (date_str,)
        ).fetchone()
        if block_id:
            conn.execute(
                "UPDATE blocks SET name = '이름' WHERE id = ?", (block_id["id"],)
            )
            assert _day_has_content(conn, date_str)

    def test_block_location(self, conn):
        """블록 location"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE blocks SET location = '장소' WHERE id = ?", (block_id,)
        )
        assert _day_has_content(conn, date_str)

    def test_block_category_only_core(self, conn):
        """블록 category_id (코어 블록만)"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        # 비코어 블록의 구분 변경 (내용 아님)
        non_core = conn.execute(
            "SELECT id FROM blocks WHERE date = ? AND is_core = 0 LIMIT 1",
            (date_str,),
        ).fetchone()
        assert non_core, "버퍼 블록(점심·저녁)이 없다"
        conn.execute(
            "UPDATE blocks SET category_id = 1 WHERE id = ?", (non_core["id"],)
        )
        # 버퍼 블록의 구분은 시드로도 채워지므로 사용자 내용으로 세지 않는다.
        assert _day_has_content(conn, date_str) is False

    def test_block_category_core(self, conn):
        """블록 category_id (코어 블록만)"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        # 코어 블록의 구분 변경
        core = conn.execute(
            "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
            (date_str,),
        ).fetchone()
        if core:
            conn.execute(
                "UPDATE blocks SET category_id = 1 WHERE id = ?", (core["id"],)
            )
            assert _day_has_content(conn, date_str)

    def test_whitespace_trimming(self, conn):
        """공백만 있으면 내용 없음"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("UPDATE slots SET do_text = '   ' WHERE id = ?", (slot_id,))
        assert not _day_has_content(conn, date_str)


# ============================================================================
# SLOT_HAS_CONTENT: SQL 조각의 정확성
# ============================================================================

class TestSlotHasContent:
    """SLOT_HAS_CONTENT SQL의 조용한 오답"""

    def test_slot_has_content_is_valid_sql(self, conn):
        """SLOT_HAS_CONTENT가 유효한 SQL 조각"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        # SQL 조각을 쿼리에 끼워 넣을 수 있는지 확인 (별칭 s 필요)
        try:
            conn.execute(
                f"SELECT COUNT(*) FROM slots s WHERE s.date = ? AND {SLOT_HAS_CONTENT}",
                (date_str,),
            ).fetchone()
        except sqlite3.OperationalError:
            pytest.fail("SLOT_HAS_CONTENT is not valid SQL fragment")

    def test_slot_has_content_matches_day_has_content(self, conn):
        """SLOT_HAS_CONTENT가 _day_has_content와 일관성"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]

        # 비루틴 슬롯에 do_text 설정
        conn.execute(
            "UPDATE slots SET is_routine = 0, do_text = '계획' WHERE id = ?",
            (slot_id,),
        )

        # _day_has_content는 True
        assert _day_has_content(conn, date_str)

        # SLOT_HAS_CONTENT도 이 슬롯을 카운트해야 함 (별칭 s 필요)
        count = conn.execute(
            f"SELECT COUNT(*) as cnt FROM slots s WHERE s.date = ? AND {SLOT_HAS_CONTENT}",
            (date_str,),
        ).fetchone()["cnt"]
        assert count > 0


# ============================================================================
# ensure_day_skeleton: DB 무결성
# ============================================================================

class TestEnsureDaySkeleton:
    """ensure_day_skeleton의 부작용 및 일관성"""

    def test_creates_blocks_and_slots(self, conn):
        """블록과 슬롯 생성"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)

        blocks = conn.execute(
            "SELECT COUNT(*) as cnt FROM blocks WHERE date = ?", (date_str,)
        ).fetchone()["cnt"]
        slots = conn.execute(
            "SELECT COUNT(*) as cnt FROM slots WHERE date = ?", (date_str,)
        ).fetchone()["cnt"]

        assert blocks > 0
        assert slots > 0

    def test_idempotent_when_matches_config(self, conn):
        """골격이 설정과 일치하면 재생성 안 함"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)

        # 첫 호출의 블록 id들 기억
        block_ids_1 = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM blocks WHERE date = ? ORDER BY block_order",
                (date_str,),
            )
        ]

        # 다시 호출
        ensure_day_skeleton(conn, date_str)

        # 블록 id가 그대로여야 함 (DELETE + INSERT 아닌 KEEP)
        block_ids_2 = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM blocks WHERE date = ? ORDER BY block_order",
                (date_str,),
            )
        ]

        assert block_ids_1 == block_ids_2

    def test_recreates_when_config_mismatch_and_no_content(self, conn):
        """설정과 다른데 적어 둔 내용이 없으면 새 시간표로 다시 만든다."""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)

        # 첫 블록의 시간만 손으로 어긋나게 한다(설정 불일치)
        conn.execute(
            "UPDATE blocks SET start_time = '09:00' WHERE date = ? AND block_order = 0",
            (date_str,),
        )
        assert _skeleton_matches_config(conn, date_str) is False

        ensure_day_skeleton(conn, date_str)

        # 지우고 다시 만들었으므로 설정 시간으로 돌아와 있어야 한다.
        # (행 id 로는 확인할 수 없다. SQLite 가 지운 rowid 를 그대로 다시 준다.)
        row = conn.execute(
            "SELECT start_time FROM blocks WHERE date = ? AND block_order = 0",
            (date_str,),
        ).fetchone()
        assert row["start_time"] == "07:30", row["start_time"]
        assert _skeleton_matches_config(conn, date_str) is True
        assert conn.execute(
            "SELECT COUNT(*) FROM slots WHERE date = ?", (date_str,)
        ).fetchone()[0] == 31

    def test_preserves_content_on_config_mismatch(self, conn):
        """설정 불일치지만 내용이 있으면 유지"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)

        # 슬롯에 내용 추가
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()["id"]
        conn.execute("UPDATE slots SET do_text = '중요한 계획' WHERE id = ?", (slot_id,))

        # 블록 시간 변경
        conn.execute(
            "UPDATE blocks SET start_time = '09:00' WHERE date = ? AND block_order = 0",
            (date_str,),
        )

        # 다시 호출
        ensure_day_skeleton(conn, date_str)

        # 슬롯 내용이 유지되어야 함
        do_text = conn.execute(
            "SELECT do_text FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()["do_text"]
        assert do_text == "중요한 계획"

    def test_etc_category_on_lunch_dinner(self, conn):
        """점심/저녁 블록은 기본 구분이 '기타'"""
        date_str = "2026-08-15"
        ensure_day_skeleton(conn, date_str)

        lunch = conn.execute(
            "SELECT category_id FROM blocks WHERE date = ? AND block_label = '점심'",
            (date_str,),
        ).fetchone()
        dinner = conn.execute(
            "SELECT category_id FROM blocks WHERE date = ? AND block_label = '저녁'",
            (date_str,),
        ).fetchone()

        etc = conn.execute(
            "SELECT id FROM categories WHERE name = '기타'"
        ).fetchone()

        if etc and lunch and dinner:
            assert lunch["category_id"] == etc["id"]
            assert dinner["category_id"] == etc["id"]


# ============================================================================
# _name_override: 주간 상속 로직
# ============================================================================

class TestNameOverride:
    """_name_override의 주간 상속 판정"""

    def test_empty_value_returns_none(self):
        """빈 값은 None (상속)"""
        assert _name_override("", "inherited") is None
        assert _name_override("   ", "inherited") is None

    def test_same_as_inherited_returns_none(self):
        """상속된 이름과 같으면 None"""
        assert _name_override("name", "name") is None

    def test_different_returns_value(self):
        """다르면 그 값 반환"""
        assert _name_override("new", "inherited") == "new"

    def test_whitespace_normalization(self):
        """공백 정규화"""
        assert _name_override("  new  ", "inherited") == "new"

    def test_none_input(self):
        """None 입력"""
        assert _name_override(None, "inherited") is None

    def test_unicode_name(self):
        """유니코드 이름"""
        assert _name_override("한글", "한글") is None
        assert _name_override("한글", "영문") == "한글"


# ============================================================================
# _split3 / _join3: 3칸 처리 및 특수문자
# ============================================================================

class TestSplit3Join3:
    """_split3/_join3의 왕복 불변식과 특수문자"""

    def test_split3_empty_string(self):
        """빈 문자열"""
        result = _split3("")
        assert result == ["", "", ""]

    def test_split3_one_line(self):
        """한 줄"""
        result = _split3("line1")
        assert result == ["line1", "", ""]

    def test_split3_three_lines(self):
        """3줄"""
        result = _split3("line1\nline2\nline3")
        assert result == ["line1", "line2", "line3"]

    def test_split3_more_than_three(self):
        """3줄 이상 (잘려야 함)"""
        result = _split3("line1\nline2\nline3\nline4\nline5")
        assert result == ["line1", "line2", "line3"]
        assert len(result) == 3

    def test_split3_empty_lines(self):
        """빈 줄 유지"""
        result = _split3("\n\n")
        assert result == ["", "", ""]

    def test_split3_middle_empty(self):
        """중간 줄이 빈 경우"""
        result = _split3("line1\n\nline3")
        assert result == ["line1", "", "line3"]

    def test_join3_roundtrip(self):
        """_split3 -> _join3 왕복"""
        original = "line1\nline2\nline3"
        split = _split3(original)
        form = {f"prefix{i}": split[i - 1] for i in (1, 2, 3)}
        joined = _join3(form, "prefix")
        assert joined == original

    def test_join3_internal_newlines_replaced(self):
        """내부 개행이 공백으로 변환됨"""
        form = {"prefix1": "line1\ninner", "prefix2": "line2", "prefix3": ""}
        result = _join3(form, "prefix")
        # 내부 개행이 공백으로 바뀌어야 함
        assert "\n" not in result.split("\n")[0]  # 첫 번째 라인에 개행 없음
        assert "line1 inner" in result

    def test_join3_carriage_return(self):
        """캐리지 리턴 처리"""
        form = {"prefix1": "line1\rline1b", "prefix2": "", "prefix3": ""}
        result = _join3(form, "prefix")
        assert "\r" not in result

    def test_join3_unicode(self):
        """유니코드"""
        form = {"prefix1": "한글1", "prefix2": "한글2", "prefix3": "한글3"}
        result = _join3(form, "prefix")
        assert "한글1\n한글2\n한글3" == result

    def test_join3_all_empty_returns_empty(self):
        """모두 비면 빈 문자열"""
        form = {"prefix1": "", "prefix2": "   ", "prefix3": "\t"}
        result = _join3(form, "prefix")
        assert result == ""

    def test_join3_mixed_spaces(self):
        """공백과 문자 혼합"""
        form = {"prefix1": "  ", "prefix2": "text", "prefix3": "  "}
        result = _join3(form, "prefix")
        lines = result.split("\n")
        assert "" in lines  # 공백만 있으면 strip됨
        assert "text" in lines

    def test_split3_unicode_newlines(self):
        """유니코드 개행 문자"""
        # U+2028 (LINE SEPARATOR)는 파이썬 \n과 다름
        result = _split3("line1 line2")
        # splitlines()는 이를 개행으로 인식하지만, split("\n")은 아님
        assert len(result) == 3  # ["line1", "line2", ""] 또는 다를 수도


# ============================================================================
# _parse_date: 날짜 파싱
# ============================================================================

class TestParseDate:
    """_parse_date의 경계값과 안전성"""

    def test_valid_date(self):
        """유효한 날짜"""
        result = _parse_date("2026-08-15")
        assert result == date(2026, 8, 15)

    def test_empty_string(self):
        """빈 문자열"""
        assert _parse_date("") is None

    def test_none_input(self):
        """None"""
        assert _parse_date(None) is None

    def test_whitespace_only(self):
        """공백만"""
        assert _parse_date("   ") is None

    def test_wrong_format(self):
        """형식 오류"""
        assert _parse_date("08-15-2026") is None
        assert _parse_date("2026/08/15") is None

    def test_invalid_month(self):
        """잘못된 월"""
        assert _parse_date("2026-13-01") is None

    def test_invalid_day(self):
        """잘못된 일"""
        assert _parse_date("2026-08-32") is None

    def test_leap_year_feb29(self):
        """윤년 2월 29"""
        result = _parse_date("2024-02-29")
        assert result == date(2024, 2, 29)

    def test_non_leap_feb29(self):
        """평년 2월 29"""
        assert _parse_date("2025-02-29") is None

    def test_five_digit_year(self):
        """5자리 연도"""
        assert _parse_date("10000-01-01") is None  # 파이썬 date는 1~9999만 지원

    def test_leading_zeros(self):
        """선행 0"""
        result = _parse_date("2026-08-01")
        assert result == date(2026, 8, 1)

    def test_unicode_digits(self):
        """유니코드 숫자"""
        # 한글/중국 숫자 등
        assert _parse_date("２０２６-０８-１５") is None


# ============================================================================
# lt_tree_order: 계층 구조와 순환 참조
# ============================================================================

class TestLtTreeOrder:
    """lt_tree_order의 깊이 계산과 순환 참조"""

    def test_empty_list(self):
        """빈 리스트"""
        result = lt_tree_order([])
        assert result == []

    def test_single_item_no_parent(self):
        """부모 없는 한 항목"""
        rows = [{"id": 1, "parent_id": None, "depth": 0}]
        result = lt_tree_order(rows)
        assert len(result) == 1
        assert result[0]["depth"] == 0

    def test_parent_child_hierarchy(self, conn):
        """부모-자식 관계"""
        # lt_item 테이블에 항목 삽입 (updated_at 필수)
        now = datetime.now(KST).isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO lt_area (name, is_active, display_order) VALUES ('영역', 1, 1)"
        )
        area_id = cur.lastrowid

        cur = conn.execute(
            "INSERT INTO lt_item (title, area_id, block_label, start_date, end_date, parent_id, updated_at) "
            "VALUES ('Parent', ?, 'B1', '2026-08-15', '2026-09-15', NULL, ?)",
            (area_id, now),
        )
        parent_id = cur.lastrowid

        conn.execute(
            "INSERT INTO lt_item (title, area_id, block_label, start_date, end_date, parent_id, updated_at) "
            "VALUES ('Child', ?, 'B1', '2026-08-15', '2026-08-31', ?, ?)",
            (area_id, parent_id, now),
        )

        rows = conn.execute(
            "SELECT id, parent_id FROM lt_item WHERE id IN (?, ?) ORDER BY id",
            (parent_id, parent_id + 1),
        ).fetchall()

        result = lt_tree_order(rows)
        assert len(result) == 2
        assert result[0]["depth"] == 0  # parent
        assert result[1]["depth"] == 1  # child
        assert result[1]["parent_id"] == parent_id

    def test_self_referencing_parent(self):
        """자기 자신을 부모로 두면 그 항목이 목록에서 사라진다(무한루프는 아니다).

        지금은 /plan/item/reparent 가 자기·하위를 막고 있어 화면에서는 만들 수 없다.
        그 막는 곳을 손대면 tests/test_known_defects.py 가 먼저 알려 준다.
        """
        assert lt_tree_order([{"id": 1, "parent_id": 1}]) == []

    def test_circular_parent_child(self):
        """1→2→1 순환도 마찬가지로 둘 다 사라진다(무한루프는 아니다)."""
        rows = [
            {"id": 1, "parent_id": 2},
            {"id": 2, "parent_id": 1},
        ]
        assert lt_tree_order(rows) == []

    def test_deep_hierarchy(self):
        """깊은 계층"""
        # 10단계 깊이
        rows = [{"id": i, "parent_id": i - 1 if i > 0 else None} for i in range(10)]
        result = lt_tree_order(rows)
        assert len(result) == 10
        assert result[-1]["depth"] == 9

    def test_missing_parent_lifted(self):
        """부모 없는 자식이 최상위로 올라옴"""
        rows = [
            {"id": 2, "parent_id": 1},  # parent 1이 없음
            {"id": 3, "parent_id": None},
        ]
        result = lt_tree_order(rows)
        # 1이 없으므로 2도 최상위(depth=0)로 올라와야 함
        depths = [r["depth"] for r in result]
        assert depths == [0, 0]


# ============================================================================
# lt_leaves: 최하위 항목 필터링
# ============================================================================

class TestLtLeaves:
    """lt_leaves의 has_children 판정"""

    def test_all_leaves_if_no_children(self):
        """자식이 없으면 모두 반환"""
        rows = [
            {"id": 1, "parent_id": None, "parent_title": "", "has_children": False, "title": "Item1"},
            {"id": 2, "parent_id": None, "parent_title": "", "has_children": False, "title": "Item2"},
        ]
        result = lt_leaves(rows)
        assert len(result) == 2

    def test_filters_parents_with_children(self):
        """자식 있는 부모는 필터링"""
        rows = [
            {"id": 1, "parent_id": None, "has_children": True, "title": "Parent"},
            {"id": 2, "parent_id": 1, "has_children": False, "title": "Child"},
        ]
        result = lt_leaves(rows)
        assert len(result) == 1
        assert result[0]["id"] == 2

    def test_parent_title_attached(self):
        """부모 제목 첨부"""
        rows = [
            {"id": 1, "parent_id": None, "has_children": True, "title": "Parent"},
            {"id": 2, "parent_id": 1, "has_children": False, "title": "Child"},
        ]
        result = lt_leaves(rows)
        assert result[0].get("parent_title") == "Parent"

    def test_parent_title_empty_if_no_parent(self):
        """부모 없으면 parent_title 빈 문자열"""
        rows = [
            {"id": 1, "parent_id": None, "has_children": False, "title": "Item"},
        ]
        result = lt_leaves(rows)
        assert result[0].get("parent_title") == ""


# ============================================================================
# week_lt_items: 주간 항목 필터링
# ============================================================================

class TestWeekLtItems:
    """week_lt_items의 기간 교집합과 masked 필터링"""

    def test_items_within_week(self, conn):
        """주에 걸친 항목"""
        week_start_str = "2026-08-03"  # 월요일
        sunday = "2026-08-09"
        now = datetime.now(KST).isoformat(timespec="seconds")

        # 그 주에 걸친 항목
        cur = conn.execute(
            "INSERT INTO lt_area (name, is_active, display_order) VALUES ('영역', 1, 1)"
        )
        area_id = cur.lastrowid

        conn.execute(
            "INSERT INTO lt_item (title, area_id, block_label, start_date, end_date, updated_at) "
            "VALUES ('Item', ?, 'B1', ?, ?, ?)",
            (area_id, week_start_str, sunday, now),
        )

        result = week_lt_items(conn, week_start_str)
        # 최하위만 반환되므로 has_children=False인 항목만
        assert len(result) >= 1

    def test_items_before_week_excluded(self, conn):
        """주 이전 항목 제외"""
        week_start_str = "2026-08-03"
        now = datetime.now(KST).isoformat(timespec="seconds")

        cur = conn.execute(
            "INSERT INTO lt_area (name, is_active, display_order) VALUES ('영역', 1, 1)"
        )
        area_id = cur.lastrowid

        conn.execute(
            "INSERT INTO lt_item (title, area_id, block_label, start_date, end_date, updated_at) "
            "VALUES ('Before', ?, 'B1', '2026-07-01', '2026-07-31', ?)",
            (area_id, now),
        )

        result = week_lt_items(conn, week_start_str)
        assert not any(item["title"] == "Before" for item in result)

    def test_masked_items_excluded(self, conn):
        """masked 항목 제외"""
        week_start_str = "2026-08-03"
        sunday = "2026-08-09"
        now = datetime.now(KST).isoformat(timespec="seconds")

        cur = conn.execute(
            "INSERT INTO lt_area (name, is_active, display_order) VALUES ('영역', 1, 1)"
        )
        area_id = cur.lastrowid

        conn.execute(
            "INSERT INTO lt_item (title, area_id, block_label, start_date, end_date, masked, updated_at) "
            "VALUES ('Masked', ?, 'B1', ?, ?, 1, ?)",
            (area_id, week_start_str, sunday, now),
        )

        result = week_lt_items(conn, week_start_str)
        assert not any(item["title"] == "Masked" for item in result)

    def test_inactive_area_excluded(self, conn):
        """비활성 영역 제외"""
        week_start_str = "2026-08-03"
        sunday = "2026-08-09"
        now = datetime.now(KST).isoformat(timespec="seconds")

        cur = conn.execute(
            "INSERT INTO lt_area (name, is_active, display_order) VALUES ('비활성', 0, 1)"
        )
        area_id = cur.lastrowid

        conn.execute(
            "INSERT INTO lt_item (title, area_id, block_label, start_date, end_date, updated_at) "
            "VALUES ('Item', ?, 'B1', ?, ?, ?)",
            (area_id, week_start_str, sunday, now),
        )

        result = week_lt_items(conn, week_start_str)
        assert not any(item["title"] == "Item" for item in result)


# ============================================================================
# _like_pattern: SQL LIKE 이스케이프
# ============================================================================

class TestLikePattern:
    """_like_pattern의 특수문자 이스케이프"""

    def test_normal_text(self):
        """일반 텍스트"""
        result = _like_pattern("hello")
        assert result == "%hello%"

    def test_percent_escaped(self):
        """% 이스케이프"""
        result = _like_pattern("50%")
        assert "\\%" in result

    def test_underscore_escaped(self):
        """_ 이스케이프"""
        result = _like_pattern("hello_world")
        assert "\\_" in result

    def test_backslash_escaped(self):
        """백슬래시 이스케이프"""
        result = _like_pattern("path\\file")
        assert "\\\\" in result

    def test_multiple_special_chars(self):
        """여러 특수문자"""
        result = _like_pattern("50% off_sale\\promo")
        assert all(c in result for c in ("\\%", "\\_", "\\\\"))

    def test_empty_string(self):
        """빈 문자열"""
        result = _like_pattern("")
        assert result == "%%"

    def test_unicode(self):
        """유니코드"""
        result = _like_pattern("한글%검색_경로\\찾기")
        assert "한글" in result
        assert "\\%" in result


# ============================================================================
# _rule_distribute: 규칙 기반 세분화
# ============================================================================

class TestRuleDistribute:
    """_rule_distribute의 분배 로직"""

    def test_empty_text(self):
        """빈 텍스트"""
        result = _rule_distribute("", 3)
        assert result == ["", "", ""]

    def test_single_line_replicated(self):
        """한 줄이면 복제"""
        result = _rule_distribute("single line", 3)
        assert result == ["single line", "single line", "single line"]

    def test_distribute_multiple_lines(self):
        """여러 줄 분배"""
        result = _rule_distribute("line1\nline2\nline3\nline4\nline5", 3)
        assert len(result) == 3
        # 라운드로빈: 0%3=0, 1%3=1, 2%3=2, 3%3=0, 4%3=1
        # buckets[0]: line1, line4
        # buckets[1]: line2, line5
        # buckets[2]: line3
        assert "line1" in result[0]
        assert "line4" in result[0]

    def test_distribute_exact_multiple(self):
        """정확히 n배"""
        result = _rule_distribute("a\nb\nc", 3)
        assert result == ["a", "b", "c"]

    def test_whitespace_only_lines_ignored(self):
        """공백만 있는 줄 무시"""
        result = _rule_distribute("line1\n   \nline2", 2)
        # 공백만 있는 줄은 무시되므로 2줄로 처리
        assert len(result) == 2

    def test_distribute_to_one(self):
        """1개로 분배"""
        result = _rule_distribute("line1\nline2\nline3", 1)
        assert len(result) == 1
        assert "line1\nline2\nline3" in result[0] or "\n" in result[0]

    def test_distribute_to_many(self):
        """n개보다 적은 줄을 n개로 분배"""
        result = _rule_distribute("line1", 5)
        assert result == ["line1"] * 5


# ============================================================================
# _ai_split: AI 분할 (외부 연동, 기본 스텁)
# ============================================================================

class TestAiSplit:
    """_ai_split의 실패 케이스 (기본적으로 스텁되어 None)"""

    def test_ai_split_disabled_returns_none(self):
        """AI가 비활성화되면 None"""
        # 기본적으로 conftest에서 ai.complete는 스텁되어 None 반환
        result = _ai_split("parent text", ["period1", "period2"], "area", "parent")
        assert result is None

    def test_ai_split_empty_labels(self):
        """라벨이 0개면 None. 부르는 쪽이 규칙기반 분배로 넘어간다."""
        assert _ai_split("parent", [], "area", "parent") is None

    def test_ai_split_malformed_json(self, monkeypatch):
        """AI 가 JSON 이 아닌 것을 돌려줘도 터지지 않고 None 이어야 한다.

        None 이면 부르는 쪽이 규칙기반 분배로 넘어간다. 여기서 예외가 새면
        자동 세분화 버튼 전체가 500 이 된다.
        """
        import app.integrations.ai as ai_mod

        for reply in ("그냥 문장입니다", "[깨진 JSON", '{"a": 1}', "", "[]"):
            monkeypatch.setattr(ai_mod, "complete",
                                lambda *a, _r=reply, **k: _r)
            assert _ai_split("parent", ["1월", "2월"], "area", "parent") is None, reply


# ============================================================================
# asset_ver: 파일 mtime 캐싱
# ============================================================================

class TestAssetVer:
    """asset_ver의 캐싱과 파일 변경 감지"""

    def test_asset_ver_returns_string(self):
        """문자열 반환"""
        result = asset_ver()
        assert isinstance(result, str)
        assert result.isdigit()

    def test_asset_ver_consistent_within_ttl(self):
        """TTL 내에서 일관성"""
        ver1 = asset_ver()
        time.sleep(0.1)
        ver2 = asset_ver()
        assert ver1 == ver2

    def test_asset_ver_missing_files_handled(self):
        """파일 없어도 처리"""
        # VERSIONED_ASSETS 중 일부가 없어도 버전 생성
        result = asset_ver()
        assert result is not None


# ============================================================================
# _client_settings: 설정 추출
# ============================================================================

class TestClientSettings:
    """_client_settings의 필터링"""

    def test_client_settings_only_whitelisted_keys(self):
        """화이트리스트 키만 포함"""
        result = _client_settings()
        assert isinstance(result, dict)
        # CLIENT_SETTING_KEYS에 있는 것만
        for key in result.keys():
            assert key in (
                "pomo_auto", "pomo_end_alarm", "collapse_blocks",
                "pomo_start_sound", "pomo_start_sec", "pomo_end_sound", "pomo_end_sec",
            )

    def test_client_settings_excludes_sensitive(self):
        """민감한 키 제외 (GCAL_WRITE_CALENDAR_ID, AI_API_KEY 등)"""
        result = _client_settings()
        assert "GCAL_WRITE_CALENDAR_ID" not in result
        assert "AI_API_KEY" not in result
        assert "AI_BASE_URL" not in result


# ============================================================================
# VERSIONED_ASSETS: 파일 목록
# ============================================================================

class TestVersionedAssets:
    """VERSIONED_ASSETS의 일관성"""

    def test_versioned_assets_is_tuple(self):
        """튜플 타입"""
        assert isinstance(VERSIONED_ASSETS, tuple)

    def test_versioned_assets_all_strings(self):
        """모두 문자열"""
        assert all(isinstance(f, str) for f in VERSIONED_ASSETS)

    def test_versioned_assets_includes_sw(self):
        """sw.js 포함 (서비스워커 버전 관리)"""
        assert "sw.js" in VERSIONED_ASSETS

    def test_versioned_assets_no_duplicates(self):
        """중복 없음"""
        assert len(VERSIONED_ASSETS) == len(set(VERSIONED_ASSETS))


# ============================================================================
# RowId: 경로 매개변수 범위 검사
# ============================================================================

class TestRowId:
    """RowId의 범위 검사 (FastAPI PathParam)"""

    def test_rowid_annotation_exists(self):
        """RowId가 annotation으로 정의됨"""
        # RowId는 Annotated[int, PathParam(ge=1, le=SQLITE_MAX_INT)]
        # FastAPI가 이를 자동으로 검사함
        assert RowId is not None

    def test_sqlite_max_int_value(self):
        """SQLITE_MAX_INT 값 정확"""
        assert SQLITE_MAX_INT == 9223372036854775807
        assert SQLITE_MAX_INT == 2**63 - 1


# ============================================================================
# week_todos: 주간 할 일 목록
# ============================================================================

class TestWeekTodos:
    """week_todos의 장기 항목과 자유 란 혼합"""

    def test_week_todos_lt_items_included(self, conn):
        """장기 항목이 포함됨"""
        week_start_str = "2026-08-03"
        sunday = "2026-08-09"
        now = datetime.now(KST).isoformat(timespec="seconds")

        cur = conn.execute(
            "INSERT INTO lt_area (name, is_active, display_order) VALUES ('영역', 1, 1)"
        )
        area_id = cur.lastrowid

        cur = conn.execute(
            "INSERT INTO lt_item (title, area_id, block_label, start_date, end_date, updated_at) "
            "VALUES ('LT Item', ?, 'B1', ?, ?, ?)",
            (area_id, week_start_str, sunday, now),
        )
        item_id = cur.lastrowid

        result = week_todos(conn, week_start_str)
        assert any(f"lt:{item_id}" in (r.get("key") or "") for r in result)

    def test_week_todos_free_goals_included(self, conn):
        """자유 할 일(wk:1~3)이 포함됨"""
        week_start_str = "2026-08-03"

        conn.execute(
            "INSERT INTO weekly_meta (week_start, weekly_goal) "
            "VALUES (?, 'Goal1\nGoal2\nGoal3')",
            (week_start_str,),
        )

        result = week_todos(conn, week_start_str)
        keys = [r.get("key") for r in result]
        assert "wk:1" in keys
        assert "wk:2" in keys
        assert "wk:3" in keys

    def test_week_todos_empty_free_goals_skipped(self, conn):
        """빈 자유 할 일은 제외"""
        week_start_str = "2026-08-03"

        conn.execute(
            "INSERT INTO weekly_meta (week_start, weekly_goal) "
            "VALUES (?, 'Goal1\n\nGoal3')",
            (week_start_str,),
        )

        result = week_todos(conn, week_start_str)
        # wk:2는 빈 줄이므로 제외되어야 함
        keys = [r.get("key") for r in result if r.get("key", "").startswith("wk:")]
        assert "wk:1" in keys
        assert "wk:2" not in keys  # 빈 줄
        assert "wk:3" in keys
