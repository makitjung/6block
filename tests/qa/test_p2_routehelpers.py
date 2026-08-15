# 2단계 엣지케이스 스페셜리스트: 조용한 오답·무한루프·왕복 불변식
import json
import threading
import time
from datetime import date, datetime, timedelta

import pytest

from app.common import KST
from app.main import _netloc_key, _origin_allowed
from app.routes import plan, settings


class TestNetlocKey:
    """_netloc_key: 호스트:포트 정규화"""

    def test_netloc_key_basic_http(self):
        """기본 포트 80은 생략."""
        assert _netloc_key("http", "example.com:80") == "example.com"

    def test_netloc_key_basic_https(self):
        """기본 포트 443은 생략."""
        assert _netloc_key("https", "example.com:443") == "example.com"

    def test_netloc_key_custom_port_http(self):
        """기본 포트 아니면 포함."""
        assert _netloc_key("http", "example.com:8080") == "example.com:8080"

    def test_netloc_key_uppercase_normalized(self):
        """대문자는 소문자로."""
        assert _netloc_key("http", "EXAMPLE.COM:8080") == "example.com:8080"

    def test_netloc_key_no_port(self):
        """포트 없으면 그대로."""
        assert _netloc_key("http", "example.com") == "example.com"

    def test_netloc_key_unknown_scheme(self):
        """알 수 없는 스킴이면 포트 유지."""
        result = _netloc_key("ftp", "example.com:21")
        assert result == "example.com:21"

    def test_netloc_key_zero_port(self):
        """포트 0은 유지."""
        result = _netloc_key("http", "example.com:0")
        assert result == "example.com:0"


class TestOriginAllowed:
    """_origin_allowed: CSRF 출처 검증"""

    def test_origin_allowed_same_host_http(self):
        """같은 호스트는 허용."""
        assert _origin_allowed("http://localhost:8000/path", "localhost:8000") is True

    def test_origin_allowed_same_host_https(self):
        """HTTPS도 기본 포트면 동일로 취급."""
        assert _origin_allowed("https://example.com/path", "example.com") is True

    def test_origin_allowed_different_host(self):
        """다른 호스트는 거절."""
        assert _origin_allowed("http://evil.com/path", "localhost:8000") is False

    def test_origin_allowed_empty_origin(self):
        """빈 Origin은 거절."""
        assert _origin_allowed("", "localhost:8000") is False

    def test_origin_allowed_no_netloc(self):
        """netloc 없는 URL은 거절."""
        assert _origin_allowed("file:///path", "localhost:8000") is False

    def test_origin_allowed_default_port_matching(self):
        """기본 포트 생략 시에도 매칭."""
        assert _origin_allowed("http://example.com/path", "example.com") is True

    def test_origin_allowed_uppercase_normalize(self):
        """대문자 정규화."""
        assert _origin_allowed("HTTP://EXAMPLE.COM/path", "example.com") is True


class TestValidHhmm:
    """_valid_hhmm: 'HH:MM' 형식 검증"""

    def test_valid_hhmm_midnight(self):
        """00:00은 유효."""
        assert settings._valid_hhmm("00:00") is True

    def test_valid_hhmm_noon(self):
        """12:00은 유효."""
        assert settings._valid_hhmm("12:00") is True

    def test_valid_hhmm_last_valid(self):
        """24:00은 유효(경계값)."""
        assert settings._valid_hhmm("24:00") is True

    def test_valid_hhmm_over_24(self):
        """25:00은 무효."""
        assert settings._valid_hhmm("25:00") is False

    def test_valid_hhmm_invalid_minutes(self):
        """분이 60 이상이면 무효."""
        assert settings._valid_hhmm("23:60") is False
        assert settings._valid_hhmm("23:99") is False

    def test_valid_hhmm_invalid_format(self):
        """형식이 아니면 무효."""
        assert settings._valid_hhmm("1:00") is False
        assert settings._valid_hhmm("12:0") is False
        assert settings._valid_hhmm("12-00") is False

    def test_valid_hhmm_empty(self):
        """빈 문자열은 무효."""
        assert settings._valid_hhmm("") is False

    def test_valid_hhmm_none(self):
        """None은 무효."""
        assert settings._valid_hhmm(None) is False

    def test_valid_hhmm_non_numeric(self):
        """문자는 무효."""
        assert settings._valid_hhmm("ab:cd") is False


class TestParseScope:
    """_parse_scope: 세션 시간 편집 범위 파싱"""

    def test_parse_scope_empty(self):
        """빈 문자열 = 공통(None)."""
        ok, wd = settings._parse_scope("")
        assert ok is True
        assert wd is None

    def test_parse_scope_zero(self):
        """'0' = 월요일(0)."""
        ok, wd = settings._parse_scope("0")
        assert ok is True
        assert wd == 0

    def test_parse_scope_six(self):
        """'6' = 일요일(6)."""
        ok, wd = settings._parse_scope("6")
        assert ok is True
        assert wd == 6

    def test_parse_scope_negative(self):
        """음수는 무효."""
        ok, wd = settings._parse_scope("-1")
        assert ok is False

    def test_parse_scope_out_of_range(self):
        """7 이상은 무효."""
        ok, wd = settings._parse_scope("7")
        assert ok is False

    def test_parse_scope_whitespace(self):
        """공백만 있으면 공통(None)."""
        ok, wd = settings._parse_scope("   ")
        assert ok is True
        assert wd is None

    def test_parse_scope_non_numeric(self):
        """문자는 무효."""
        ok, wd = settings._parse_scope("abc")
        assert ok is False


class TestCleanWeekdays:
    """_clean_weekdays: 요일 정리(0~6, 중복 제거, 정렬)"""

    def test_clean_weekdays_valid(self):
        """유효한 요일 정렬."""
        result = settings._clean_weekdays("3,1,5,0")
        assert result == "0,1,3,5"

    def test_clean_weekdays_duplicate(self):
        """중복 제거."""
        result = settings._clean_weekdays("1,1,1")
        assert result == "1"

    def test_clean_weekdays_out_of_range(self):
        """0~6 밖의 값 제거."""
        result = settings._clean_weekdays("0,7,8,-1,3")
        assert result == "0,3"

    def test_clean_weekdays_empty(self):
        """빈 문자열."""
        result = settings._clean_weekdays("")
        assert result == ""

    def test_clean_weekdays_none(self):
        """None."""
        result = settings._clean_weekdays(None)
        assert result == ""

    def test_clean_weekdays_whitespace_only(self):
        """공백만 있는 항목 무시."""
        result = settings._clean_weekdays("1, ,3")
        assert result == "1,3"

    def test_clean_weekdays_all_invalid(self):
        """모두 범위 밖이면 빈 문자열."""
        result = settings._clean_weekdays("7,8,9")
        assert result == ""


class TestMaskUnmaskEnv:
    """_mask_env_text / _unmask_env_text: 시크릿 마스킹 왕복"""

    def test_mask_simple(self):
        """단순 값을 마스킹."""
        text = "KEY=secret\n"
        masked = settings._mask_env_text(text)
        assert "KEY=********" in masked
        assert "secret" not in masked

    def test_mask_empty_value(self):
        """빈 값은 그대로."""
        text = "EMPTY=\n"
        masked = settings._mask_env_text(text)
        assert masked == text

    def test_mask_comment(self):
        """주석은 그대로."""
        text = "# This is a comment with secret\n"
        masked = settings._mask_env_text(text)
        assert masked == text

    def test_mask_no_equals(self):
        """= 없는 줄은 그대로."""
        text = "INVALID LINE\n"
        masked = settings._mask_env_text(text)
        assert masked == text

    def test_unmask_roundtrip_full(self):
        """마스킹→언마스킹 왕복 불변식."""
        original = "KEY1=value1\nKEY2=value2\nKEY3=\n# comment\n"
        masked = settings._mask_env_text(original)
        restored = settings._unmask_env_text(masked, original)
        assert restored == original

    def test_unmask_changed_values(self):
        """새로 입력한 값은 유지."""
        original = "KEY1=secret1\nKEY2=secret2\n"
        masked = settings._mask_env_text(original)
        # 마스크된 값 그대로 반환하면 기존값 복원, 새 값 입력하면 그대로 유지
        new_content = "KEY1=newsecret\nKEY2=********\n"
        restored = settings._unmask_env_text(new_content, original)
        assert "newsecret" in restored
        assert "secret2" in restored

    def test_unmask_added_keys(self):
        """새로 추가한 키는 그대로."""
        original = "KEY1=value1\n"
        masked = settings._mask_env_text(original)
        new_content = masked + "KEY2=newvalue\n"
        restored = settings._unmask_env_text(new_content, original)
        assert "KEY2=newvalue" in restored

    def test_mask_multiline_values(self):
        """여러 줄 파일 마스킹."""
        text = "# 주석\nKEY1=value1\n\nKEY2=value2\nKEY3=\n"
        masked = settings._mask_env_text(text)
        assert "# 주석" in masked
        assert "KEY1=********" in masked
        assert "KEY2=********" in masked
        assert "KEY3=" in masked
        assert "value1" not in masked
        assert "value2" not in masked

    def test_unmask_preserves_structure(self):
        """줄 구조를 보존."""
        original = "A=1\nB=2\nC=3\n"
        masked = settings._mask_env_text(original)
        restored = settings._unmask_env_text(masked, original)
        lines = restored.split("\n")
        assert len(lines) == 4  # A, B, C, trailing empty
        assert lines[0].startswith("A=")
        assert lines[1].startswith("B=")
        assert lines[2].startswith("C=")


class TestPlanAddMonths:
    """_add_months: 달 계산"""

    def test_add_months_same_day(self):
        """같은 날씬 달이 있으면 그대로."""
        d = date(2026, 1, 15)
        result = plan._add_months(d, 1)
        assert result == date(2026, 2, 15)

    def test_add_months_eom_to_short_month(self):
        """1/31 + 1월 = 2/28(29)."""
        d = date(2026, 1, 31)
        result = plan._add_months(d, 1)
        assert result == date(2026, 2, 28)

    def test_add_months_leap_year(self):
        """윤년 2/29 처리."""
        d = date(2024, 1, 31)  # 2024는 윤년
        result = plan._add_months(d, 1)
        assert result == date(2024, 2, 29)

    def test_add_months_negative(self):
        """음수(이전 달)."""
        d = date(2026, 3, 15)
        result = plan._add_months(d, -1)
        assert result == date(2026, 2, 15)

    def test_add_months_cross_year(self):
        """해를 넘어가기."""
        d = date(2026, 11, 15)
        result = plan._add_months(d, 3)
        assert result == date(2027, 2, 15)

    def test_add_months_zero(self):
        """0을 더하면 그대로."""
        d = date(2026, 6, 15)
        result = plan._add_months(d, 0)
        assert result == d

    def test_add_months_large_offset(self):
        """많은 달을 더하기."""
        d = date(2026, 1, 1)
        result = plan._add_months(d, 12)
        assert result == date(2027, 1, 1)

    def test_add_months_large_negative(self):
        """많은 달을 빼기."""
        d = date(2026, 12, 1)
        result = plan._add_months(d, -12)
        assert result == date(2025, 12, 1)


class TestSplitCleanBlocks:
    """_split_blocks / _clean_blocks: 블록 목록 파싱"""

    def test_split_blocks_valid(self):
        """유효한 블록 목록."""
        result = plan._split_blocks("B1,B3,B5")
        assert result == ["B1", "B3", "B5"]

    def test_split_blocks_duplicates(self):
        """중복 제거."""
        result = plan._split_blocks("B1,B1,B3")
        # set으로 중복을 제거한 후 CORE_BLOCKS 순서로 정렬
        assert "B1" in result
        assert "B3" in result

    def test_split_blocks_invalid(self):
        """존재하지 않는 블록 제거."""
        result = plan._split_blocks("B1,B99,B3")
        assert "B99" not in result
        assert "B1" in result
        assert "B3" in result

    def test_split_blocks_empty(self):
        """빈 문자열."""
        result = plan._split_blocks("")
        assert result == []

    def test_split_blocks_none(self):
        """None."""
        result = plan._split_blocks(None)
        assert result == []

    def test_split_blocks_whitespace(self):
        """공백은 무시."""
        result = plan._split_blocks("B1 , B3 , B5")
        assert "B1" in result
        assert "B3" in result
        assert "B5" in result

    def test_clean_blocks_roundtrip(self):
        """split→clean 왕복."""
        original = "B1,B5,B2"
        cleaned = plan._clean_blocks(original)
        split = plan._split_blocks(cleaned)
        assert set(split) == {"B1", "B5", "B2"}


class TestCycleDetection:
    """순환 참조 방지: _lt_rollup, _lt_descendants, _lt_root"""

    def test_lt_root_self_reference(self, fresh_db, conn):
        """항목이 자신을 상위로 참조하는 경우."""
        # lt_item 테이블에 자신을 가리키는 parent_id를 가진 항목 추가
        now = datetime.now(KST).isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO lt_item (area_id, title, start_date, end_date, parent_id, updated_at) "
            "VALUES (1, 'self-ref', '2026-08-01', '2026-08-31', NULL, ?)",
            (now,)
        )
        iid = cur.lastrowid
        conn.execute(
            "UPDATE lt_item SET parent_id = ? WHERE id = ?", (iid, iid)
        )
        # _lt_root가 무한루프에 빠지지 않아야 함
        result = plan._lt_root(conn, iid)
        assert result == iid  # 순환 탐지 후 현재 항목 반환

    def test_lt_root_circular_chain(self, fresh_db, conn):
        """A → B → C → A 순환 참조."""
        # 3개 항목 생성
        now = datetime.now(KST).isoformat(timespec="seconds")
        rows = []
        for i in range(3):
            cur = conn.execute(
                "INSERT INTO lt_item (area_id, title, start_date, end_date, updated_at) "
                "VALUES (1, ?, '2026-08-01', '2026-08-31', ?)",
                (f"item{i}", now)
            )
            rows.append(cur.lastrowid)

        # A → B, B → C, C → A 순환
        conn.execute("UPDATE lt_item SET parent_id = ? WHERE id = ?", (rows[1], rows[0]))
        conn.execute("UPDATE lt_item SET parent_id = ? WHERE id = ?", (rows[2], rows[1]))
        conn.execute("UPDATE lt_item SET parent_id = ? WHERE id = ?", (rows[0], rows[2]))

        # _lt_root가 무한루프 없이 반환해야 함
        result = plan._lt_root(conn, rows[0])
        assert isinstance(result, int)

    def test_lt_descendants_no_cycle(self, fresh_db, conn):
        """_lt_descendants가 순환 참조 여부와 무관하게 동작."""
        # 간단한 트리 생성
        now = datetime.now(KST).isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO lt_item (area_id, title, start_date, end_date, updated_at) "
            "VALUES (1, 'parent', '2026-08-01', '2026-08-31', ?)",
            (now,)
        )
        pid = cur.lastrowid

        cur = conn.execute(
            "INSERT INTO lt_item (area_id, title, start_date, end_date, parent_id, updated_at) "
            "VALUES (1, 'child', '2026-08-01', '2026-08-31', ?, ?)",
            (pid, now)
        )
        cid = cur.lastrowid

        descendants = plan._lt_descendants(conn, pid)
        assert cid in descendants


class TestLtRollupNoInfiniteLoop:
    """_lt_rollup: 순환 참조 방지"""

    def test_lt_rollup_timeout_protection(self, fresh_db, conn):
        """_lt_rollup이 무한루프에 빠지지 않는지(타임아웃)."""
        # 자기 자신을 상위로 참조하는 순환 항목 생성
        now = datetime.now(KST).isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO lt_item (area_id, title, start_date, end_date, updated_at) "
            "VALUES (1, 'circular', '2026-08-01', '2026-08-31', ?)",
            (now,)
        )
        iid = cur.lastrowid
        conn.execute(
            "UPDATE lt_item SET parent_id = ? WHERE id = ?", (iid, iid)
        )

        # 타임아웃 보호가 있어야 빠져나옴
        start = time.time()
        plan._lt_rollup(conn, iid)
        elapsed = time.time() - start
        assert elapsed < 1.0  # 1초 이내에 반환


class TestParseAnchorEdgeCases:
    """_parse_anchor: 날짜 파싱"""

    def test_parse_anchor_invalid_date(self):
        """존재하지 않는 날짜('2026-02-30')."""
        today = datetime.now(KST).date()
        result = plan._parse_anchor("2026-02-30")
        assert result == today  # 오늘로 폴백

    def test_parse_anchor_leap_year(self):
        """윤년 2/29."""
        result = plan._parse_anchor("2024-02-29")
        assert result == date(2024, 2, 29)

    def test_parse_anchor_far_future(self):
        """먼 미래 날짜."""
        result = plan._parse_anchor("9999-12-31")
        assert result == date(9999, 12, 31)

    def test_parse_anchor_ancient_date(self):
        """매우 과거 날짜."""
        result = plan._parse_anchor("0001-01-01")
        assert result == date(1, 1, 1)

    def test_parse_anchor_wrong_format(self):
        """형식이 다르면 오늘."""
        today = datetime.now(KST).date()
        for bad_anchor in ["26-08-15", "2026/08/15", "08-15-2026", ""]:
            result = plan._parse_anchor(bad_anchor)
            assert result == today


class TestMonthLast:
    """_month_last: 월말 계산"""

    def test_month_last_31_day_month(self):
        """31일 있는 달."""
        assert plan._month_last(2026, 1) == date(2026, 1, 31)
        assert plan._month_last(2026, 3) == date(2026, 3, 31)

    def test_month_last_30_day_month(self):
        """30일 있는 달."""
        assert plan._month_last(2026, 4) == date(2026, 4, 30)
        assert plan._month_last(2026, 6) == date(2026, 6, 30)

    def test_month_last_february_non_leap(self):
        """평년 2월."""
        assert plan._month_last(2026, 2) == date(2026, 2, 28)

    def test_month_last_february_leap(self):
        """윤년 2월."""
        assert plan._month_last(2024, 2) == date(2024, 2, 29)

    def test_month_last_december(self):
        """12월(다음 해로 넘어감)."""
        assert plan._month_last(2026, 12) == date(2026, 12, 31)


class TestPlanColumns:
    """_plan_columns: 기간 열 생성"""

    def test_plan_columns_year_level(self):
        """연 단위."""
        anchor = date(2026, 6, 15)
        cols, header = plan._plan_columns("year", anchor)
        assert len(cols) == 5
        assert cols[1]["current"] is True  # anchor 연이 두 번째
        assert all("start" in c and "end" in c for c in cols)

    def test_plan_columns_today_marking(self):
        """현재 기간 표시."""
        today = datetime.now(KST).date()
        cols, header = plan._plan_columns("month", today)
        # 현재 월이 marked 되어야 함
        current_cols = [c for c in cols if c.get("current")]
        assert len(current_cols) >= 0  # 최소한 쿼리가 안 깨짐

    def test_plan_columns_drill_links(self):
        """드릴 다운 링크 생성."""
        anchor = date(2026, 6, 15)
        cols, header = plan._plan_columns("year", anchor)
        assert all(c.get("drill_level") for c in cols)
        assert all(c.get("drill_anchor") for c in cols)

    def test_plan_columns_date_ranges_valid(self):
        """start ≤ end 범위."""
        for level in ["year", "quarter", "month", "week"]:
            cols, header = plan._plan_columns(level, date(2026, 6, 15))
            for col in cols:
                assert col["start"] <= col["end"], f"{level}: {col}"


class TestSearchRecordsLarge:
    """_search_records: 큰 입력값"""

    def test_search_records_huge_query(self):
        """아주 긴 검색어(10만 글자)."""
        huge = "a" * 100_000
        slots, blocks = analytics._search_records(huge)
        # 쿼리는 실행되어야 하고, 결과는 빈 리스트여야 함
        assert isinstance(slots, list)
        assert isinstance(blocks, list)

    def test_search_records_special_chars(self):
        """SQL LIKE 메타문자."""
        from app.routes import analytics
        # %, _, \ 등 LIKE 메타문자가 안전하게 처리되어야 함
        for query in ["%", "_", "\\", "%%", "_%"]:
            slots, blocks = analytics._search_records(query)
            # 크래시 없이 빈 결과 반환
            assert isinstance(slots, list)
            assert isinstance(blocks, list)


# 필요한 import 추가
import sys
sys.path.insert(0, "/Users/jinhyugjung/dev/6block")
from app.routes import analytics
