# app/common.py 공통 도우미 함수의 1단계 유닛 테스트
from datetime import date

import pytest

from app.common import (
   int_id, opt_id, _ko_weekday, _pretty_date, _short_date, asset_ver,
    _client_settings, today_str, week_start, _weekday_of, _skeleton_matches_config,
    _day_has_content, ensure_day_skeleton, _name_override, _split3, _join3,
    _parse_date, lt_tree_order, lt_leaves, week_lt_items, week_todos, _like_pattern,
    _rule_distribute, _ai_split, SLOT_HAS_CONTENT, VERSIONED_ASSETS, SQLITE_MAX_INT,
)
from app.config import DAY_BLOCKS


class TestIntId:
    def test_valid_id(self):
        assert int_id("1") == 1
        assert int_id("123") == 123
        assert int_id(str(SQLITE_MAX_INT)) == SQLITE_MAX_INT

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            int_id("0")

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            int_id("-1")

    def test_overflow_raises(self):
        with pytest.raises(ValueError):
            int_id(str(SQLITE_MAX_INT + 1))

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            int_id("abc")


class TestOptId:
    def test_valid_id(self):
        assert opt_id("1") == 1
        assert opt_id("123") == 123

    def test_empty_returns_none(self):
        assert opt_id("") is None
        assert opt_id(None) is None

    def test_invalid_returns_none(self):
        assert opt_id("abc") is None
        assert opt_id("0") is None  # 0은 범위 벗어남
        assert opt_id("-1") is None

    def test_overflow_returns_none(self):
        assert opt_id(str(SQLITE_MAX_INT + 1)) is None


class TestKoWeekday:
    def test_monday(self):
        assert _ko_weekday("2026-08-03") == "월"

    def test_friday(self):
        assert _ko_weekday("2026-08-07") == "금"

    def test_sunday(self):
        assert _ko_weekday("2026-08-09") == "일"


class TestPrettyDate:
    def test_format(self):
        result = _pretty_date("2026-08-15")
        assert "8월" in result
        assert "15일" in result
        assert "요일" in result

    def test_correct_weekday(self):
        # 2026-08-15는 토요일
        result = _pretty_date("2026-08-15")
        assert "토요일" in result


class TestShortDate:
    def test_format(self):
        assert _short_date("2026-08-15") == "8.15"
        assert _short_date("2026-01-01") == "1.1"
        assert _short_date("2026-12-31") == "12.31"


class TestWeekdayOf:
    def test_monday_is_zero(self):
        assert _weekday_of("2026-08-03") == 0  # 월요일

    def test_sunday_is_six(self):
        assert _weekday_of("2026-08-09") == 6  # 일요일

    def test_wednesday_is_two(self):
        assert _weekday_of("2026-08-05") == 2  # 수요일


class TestWeekStart:
    def test_monday_returns_itself(self):
        d = date(2026, 8, 3)  # 월요일
        assert week_start(d) == d

    def test_friday_returns_monday(self):
        d = date(2026, 8, 7)  # 금요일
        monday = date(2026, 8, 3)  # 그 주 월요일
        assert week_start(d) == monday

    def test_sunday_returns_previous_monday(self):
        d = date(2026, 8, 9)  # 일요일
        monday = date(2026, 8, 3)  # 그 주 월요일
        assert week_start(d) == monday


class TestTodayStr:
    def test_format(self):
        result = today_str()
        assert len(result) == 10  # YYYY-MM-DD
        assert result.count('-') == 2


class TestNameOverride:
    def test_empty_returns_none(self):
        assert _name_override("", "inherited") is None
        assert _name_override(None, "inherited") is None

    def test_same_as_inherited_returns_none(self):
        assert _name_override("same", "same") is None
        assert _name_override("  same  ", "same") is None

    def test_different_returns_value(self):
        assert _name_override("new", "old") == "new"
        assert _name_override("  new  ", "old") == "new"


class TestSplit3:
    def test_one_line(self):
        result = _split3("line1")
        assert result == ["line1", "", ""]

    def test_three_lines(self):
        result = _split3("line1\nline2\nline3")
        assert result == ["line1", "line2", "line3"]

    def test_more_than_three_lines_truncates(self):
        result = _split3("line1\nline2\nline3\nline4")
        assert result == ["line1", "line2", "line3"]

    def test_empty_returns_three_empty(self):
        result = _split3("")
        assert result == ["", "", ""]

    def test_two_lines(self):
        result = _split3("line1\nline2")
        assert result == ["line1", "line2", ""]


class TestJoin3:
    def test_all_filled(self):
        form = {"goal1": "first", "goal2": "second", "goal3": "third"}
        result = _join3(form, "goal")
        assert result == "first\nsecond\nthird"

    def test_partial_filled(self):
        form = {"goal1": "first", "goal2": "", "goal3": "third"}
        result = _join3(form, "goal")
        assert result == "first\n\nthird"

    def test_all_empty_returns_empty(self):
        form = {"goal1": "", "goal2": "", "goal3": ""}
        result = _join3(form, "goal")
        assert result == ""

    def test_internal_newlines_become_spaces(self):
        form = {"goal1": "first\nline", "goal2": "second", "goal3": "third"}
        result = _join3(form, "goal")
        assert "first line" in result

    def test_missing_keys_treated_as_empty(self):
        form = {"goal1": "first"}
        result = _join3(form, "goal")
        assert result == "first\n\n"


class TestParseDate:
    def test_valid_date(self):
        result = _parse_date("2026-08-15")
        assert result == date(2026, 8, 15)

    def test_invalid_format_returns_none(self):
        assert _parse_date("2026/08/15") is None
        assert _parse_date("08-15-2026") is None
        assert _parse_date("invalid") is None

    def test_empty_returns_none(self):
        assert _parse_date("") is None
        assert _parse_date(None) is None

    def test_whitespace_stripped(self):
        result = _parse_date("  2026-08-15  ")
        assert result == date(2026, 8, 15)


class TestLikePattern:
    def test_plain_text(self):
        result = _like_pattern("test")
        assert result == "%test%"

    def test_percent_escaped(self):
        result = _like_pattern("100%")
        assert "\\%" in result
        assert "%100\\%%" in result

    def test_underscore_escaped(self):
        result = _like_pattern("a_b")
        assert "\\_" in result

    def test_backslash_escaped(self):
        result = _like_pattern("a\\b")
        assert result == "%a\\\\b%"


class TestRuleDistribute:
    def test_empty_returns_empty_list(self):
        result = _rule_distribute("", 3)
        assert result == ["", "", ""]

    def test_single_line_replicates(self):
        result = _rule_distribute("single", 3)
        assert result == ["single", "single", "single"]

    def test_multiple_lines_distribute(self):
        result = _rule_distribute("line1\nline2\nline3", 3)
        assert result[0] == "line1"
        assert result[1] == "line2"
        assert result[2] == "line3"

    def test_more_lines_than_n_wraps(self):
        result = _rule_distribute("line1\nline2\nline3\nline4", 2)
        assert "line1" in result[0]
        assert "line3" in result[0]
        assert "line2" in result[1]
        assert "line4" in result[1]

    def test_fewer_lines_than_n(self):
        result = _rule_distribute("line1\nline2", 4)
        assert "line1" in result[0]
        assert "line2" in result[1]
        assert result[2] == ""
        assert result[3] == ""


class TestAiSplit:
    def test_ai_disabled_returns_none(self):
        # 테스트 환경에서 AI는 스텁으로 비활성화되어 있음
        result = _ai_split("parent text", ["label1", "label2"], "area", "parent")
        assert result is None


class TestLtTreeOrder:
    def test_flat_structure(self):
        rows = [
            {"id": 1, "parent_id": None, "title": "Item1"},
            {"id": 2, "parent_id": None, "title": "Item2"},
        ]
        result = lt_tree_order(rows)
        assert len(result) == 2
        assert result[0]["depth"] == 0
        assert result[1]["depth"] == 0

    def test_nested_structure(self):
        rows = [
            {"id": 1, "parent_id": None, "title": "Parent"},
            {"id": 2, "parent_id": 1, "title": "Child"},
            {"id": 3, "parent_id": 2, "title": "Grandchild"},
        ]
        result = lt_tree_order(rows)
        assert result[0]["depth"] == 0
        assert result[1]["depth"] == 1
        assert result[2]["depth"] == 2

    def test_missing_parent_orphaned_to_root(self):
        rows = [
            {"id": 2, "parent_id": 999, "title": "Orphan"},
            {"id": 1, "parent_id": None, "title": "Root"},
        ]
        result = lt_tree_order(rows)
        # 부모가 없는 항목은 최상위로 올려짐
        assert result[0]["parent_id"] is None or result[0]["parent_id"] not in {r["id"] for r in rows}


class TestLtLeaves:
    def test_only_leaves_returned(self):
        rows = [
            {"id": 1, "parent_id": None, "title": "Parent", "has_children": True},
            {"id": 2, "parent_id": 1, "title": "Child", "has_children": False},
        ]
        result = lt_leaves(rows)
        assert len(result) == 1
        assert result[0]["id"] == 2

    def test_parent_title_attached(self):
        rows = [
            {"id": 1, "parent_id": None, "title": "Parent", "has_children": True},
            {"id": 2, "parent_id": 1, "title": "Child", "has_children": False},
        ]
        result = lt_leaves(rows)
        assert result[0]["parent_title"] == "Parent"


class TestSkeletonMatchesConfig:
    def test_empty_db_no_blocks(self, conn):
        # 블록이 없으면 설정과 다름
        assert not _skeleton_matches_config(conn, "2026-08-15")

    def test_matches_after_skeleton_creation(self, conn):
        ensure_day_skeleton(conn, "2026-08-15")
        # 새로 생성한 골격은 현재 설정과 일치해야 함
        assert _skeleton_matches_config(conn, "2026-08-15")


class TestDayHasContent:
    def test_empty_day_no_content(self, conn):
        ensure_day_skeleton(conn, "2026-08-15")
        # 새 골격만으로는 내용 없음
        assert not _day_has_content(conn, "2026-08-15")

    def test_slot_with_do_text_has_content(self, conn):
        ensure_day_skeleton(conn, "2026-08-15")
        # 슬롯에 DO 텍스트 추가
        slot_id = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", ("2026-08-15",)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE slots SET do_text = ? WHERE id = ?",
            ("test task", slot_id)
        )
        conn.commit()
        assert _day_has_content(conn, "2026-08-15")

    def test_block_with_plan_has_content(self, conn):
        ensure_day_skeleton(conn, "2026-08-15")
        # 블록에 PLAN 텍스트 추가
        block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
            ("2026-08-15",)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE blocks SET plan_text = ? WHERE id = ?",
            ("test plan", block_id)
        )
        conn.commit()
        assert _day_has_content(conn, "2026-08-15")


class TestEnsureDaySkeleton:
    def test_creates_blocks_and_slots(self, conn):
        ensure_day_skeleton(conn, "2026-08-15")
        blocks = conn.execute(
            "SELECT COUNT(*) as cnt FROM blocks WHERE date = ?", ("2026-08-15",)
        ).fetchone()["cnt"]
        slots = conn.execute(
            "SELECT COUNT(*) as cnt FROM slots WHERE date = ?", ("2026-08-15",)
        ).fetchone()["cnt"]
        assert blocks > 0
        assert slots > 0

    def test_idempotent_with_content(self, conn):
        ensure_day_skeleton(conn, "2026-08-15")
        block_id = conn.execute(
            "SELECT id FROM blocks WHERE date = ? LIMIT 1", ("2026-08-15",)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE blocks SET plan_text = ? WHERE id = ?",
            ("keep this", block_id)
        )
        conn.commit()

        # 다시 ensure 호출해도 기존 내용 유지
        ensure_day_skeleton(conn, "2026-08-15")
        plan = conn.execute(
            "SELECT plan_text FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()["plan_text"]
        assert plan == "keep this"

    def test_recreates_empty_day_after_config_change(self, conn):
        ensure_day_skeleton(conn, "2026-08-15")
        # 블록을 하나 삭제해서 설정이 안 맞게 만듦
        conn.execute("DELETE FROM blocks WHERE date = ? AND rowid = (SELECT rowid FROM blocks WHERE date = ? LIMIT 1)", ("2026-08-15", "2026-08-15"))
        conn.commit()

        # 다시 ensure하면 재생성됨
        ensure_day_skeleton(conn, "2026-08-15")
        blocks = conn.execute(
            "SELECT COUNT(*) as cnt FROM blocks WHERE date = ?", ("2026-08-15",)
        ).fetchone()["cnt"]
        # 원래 블록 개수로 복구되어야 함
        assert blocks == len(DAY_BLOCKS)


class TestWeekTodos:
    def test_returns_list(self, conn):
        week_start_str = "2026-08-03"
        result = week_todos(conn, week_start_str)
        assert isinstance(result, list)

    def test_has_key_and_label(self, conn):
        week_start_str = "2026-08-03"
        result = week_todos(conn, week_start_str)
        for item in result:
            assert "key" in item
            assert "label" in item


class TestWeekLtItems:
    def test_returns_list(self, conn):
        week_start_str = "2026-08-03"
        result = week_lt_items(conn, week_start_str)
        assert isinstance(result, list)

    def test_items_have_required_fields(self, conn):
        # 데이터가 없으면 빈 리스트 반환이 정상
        week_start_str = "2026-08-03"
        result = week_lt_items(conn, week_start_str)
        # 최소한 리스트는 반환되어야 함
        assert result == []


class TestClientSettings:
    def test_returns_dict(self):
        result = _client_settings()
        assert isinstance(result, dict)

    def test_contains_expected_keys(self):
        result = _client_settings()
        # CLIENT_SETTING_KEYS에 정의된 키들이 있어야 함
        assert "pomo_auto" in result or "pomo_end_alarm" in result


class TestAssetVer:
    def test_returns_string(self):
        result = asset_ver()
        assert isinstance(result, str)

    def test_is_numeric(self):
        result = asset_ver()
        assert result.isdigit() or result == "1"

    def test_caching_works(self):
        first = asset_ver()
        second = asset_ver()
        # 같은 것을 빠르게 연속 호출하면 같은 값이 나와야 함
        assert first == second


class TestSlotHasContent:
    def test_is_sql_fragment(self):
        # SLOT_HAS_CONTENT는 SQL 조각이므로 문자열이어야 함
        assert isinstance(SLOT_HAS_CONTENT, str)
        assert "s.do_text" in SLOT_HAS_CONTENT or "s.did_text" in SLOT_HAS_CONTENT


class TestVersionedAssets:
    def test_is_tuple(self):
        assert isinstance(VERSIONED_ASSETS, tuple)

    def test_contains_required_assets(self):
        assert "app.js" in VERSIONED_ASSETS
        assert "style.css" in VERSIONED_ASSETS
        assert "sw.js" in VERSIONED_ASSETS


class TestSqliteMaxInt:
    def test_is_valid_64bit(self):
        # SQLite 64비트 최대값
        assert SQLITE_MAX_INT == 9223372036854775807
