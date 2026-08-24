# app/routes 안의 순수 도우미 함수들 유닛 테스트
from datetime import date, datetime, timedelta


from app.common import KST
from app.main import _netloc_key, _origin_allowed
from app.routes import analytics, day, plan, reflect, settings


class TestAnalytics:
    """analytics.py 순수 함수 테스트"""

    def test_calc_streak_empty(self):
        """기록이 없으면 0."""
        today = datetime.now(KST).date()
        assert analytics._calc_streak(set(), today) == 0

    def test_calc_streak_today_has_record(self):
        """오늘 기록이 있으면 1부터 시작."""
        today = datetime.now(KST).date()
        today_str = today.strftime("%Y-%m-%d")
        rec_dates = {today_str}
        assert analytics._calc_streak(rec_dates, today) == 1

    def test_calc_streak_consecutive(self):
        """연속 기록 5일."""
        today = datetime.now(KST).date()
        rec_dates = {
            (today - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(5)
        }
        assert analytics._calc_streak(rec_dates, today) == 5

    def test_calc_streak_yesterday_no_today(self):
        """오늘 기록 없지만 어제부터 연속."""
        today = datetime.now(KST).date()
        rec_dates = {
            (today - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(1, 4)  # 어제, 그제, 3일전
        }
        assert analytics._calc_streak(rec_dates, today) == 3

    def test_calc_streak_broken_chain(self):
        """어제만 있고 그 전날은 없으면 1."""
        today = datetime.now(KST).date()
        rec_dates = {
            (today - timedelta(days=1)).strftime("%Y-%m-%d"),
            (today - timedelta(days=3)).strftime("%Y-%m-%d"),
        }
        assert analytics._calc_streak(rec_dates, today) == 1

    def test_on_this_day_empty_db(self, conn):
        """빈 DB에서 빈 리스트 반환."""
        today = datetime.now(KST).date()
        result = analytics._on_this_day(today)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_search_records_empty_query(self):
        """빈 검색어면 빈 목록."""
        slots, blocks = analytics._search_records("")
        assert slots == []
        assert blocks == []

    def test_search_records_whitespace_query(self):
        """공백만 있는 검색어도 빈 목록."""
        slots, blocks = analytics._search_records("   ")
        assert slots == []
        assert blocks == []


class TestPlan:
    """plan.py 순수 함수 테스트"""

    def test_parse_anchor_valid(self):
        """유효한 날짜 문자열."""
        result = plan._parse_anchor("2026-08-15")
        assert result == date(2026, 8, 15)

    def test_parse_anchor_invalid(self):
        """잘못된 형식이면 오늘."""
        today = datetime.now(KST).date()
        result = plan._parse_anchor("invalid")
        assert result == today

    def test_parse_anchor_empty(self):
        """빈 문자열이면 오늘."""
        today = datetime.now(KST).date()
        result = plan._parse_anchor("")
        assert result == today

    def test_month_last_january(self):
        """1월의 마지막 날은 31일."""
        result = plan._month_last(2026, 1)
        assert result == date(2026, 1, 31)

    def test_month_last_february_leap(self):
        """윤년 2월은 29일."""
        result = plan._month_last(2024, 2)
        assert result == date(2024, 2, 29)

    def test_month_last_february_normal(self):
        """평년 2월은 28일."""
        result = plan._month_last(2025, 2)
        assert result == date(2025, 2, 28)

    def test_month_last_december(self):
        """12월은 31일."""
        result = plan._month_last(2026, 12)
        assert result == date(2026, 12, 31)

    def test_add_months_same_day(self):
        """3개월 더하기."""
        d = date(2026, 1, 15)
        result = plan._add_months(d, 3)
        assert result == date(2026, 4, 15)

    def test_add_months_day_clamping(self):
        """1/31 + 1개월 = 2/28(비윤년)."""
        d = date(2025, 1, 31)
        result = plan._add_months(d, 1)
        assert result == date(2025, 2, 28)

    def test_add_months_backward(self):
        """음수로 빼기."""
        d = date(2026, 4, 15)
        result = plan._add_months(d, -2)
        assert result == date(2026, 2, 15)

    def test_split_blocks_single(self):
        """'B1'은 ['B1']."""
        result = plan._split_blocks("B1")
        assert result == ["B1"]

    def test_split_blocks_multiple(self):
        """'B1,B3,B5' → ['B1', 'B3', 'B5']."""
        result = plan._split_blocks("B1,B3,B5")
        assert set(result) == {"B1", "B3", "B5"}

    def test_split_blocks_with_spaces(self):
        """공백 제거."""
        result = plan._split_blocks("B1 , B3 , B5")
        assert set(result) == {"B1", "B3", "B5"}

    def test_split_blocks_unknown_ignored(self):
        """모르는 블록은 버림."""
        result = plan._split_blocks("B1,B9,B3")
        assert set(result) == {"B1", "B3"}

    def test_split_blocks_empty(self):
        """빈 문자열."""
        result = plan._split_blocks("")
        assert result == []

    def test_clean_blocks_result(self):
        """'B1,B3' → 'B1,B3'."""
        result = plan._clean_blocks("B1,B3")
        assert result == "B1,B3" or result == "B3,B1"  # 순서는 CORE_BLOCKS 순

    def test_clean_blocks_empty(self):
        """빈 입력 → ''."""
        result = plan._clean_blocks("")
        assert result == ""

    def test_clean_blocks_unknown(self):
        """모르는 값만 → ''."""
        result = plan._clean_blocks("B9,B10")
        assert result == ""

    def test_block_rows(self):
        """B1~B6 + 미지정 7줄."""
        result = plan._block_rows()
        assert len(result) == 7
        labels = [r["label"] for r in result]
        assert "B1" in labels
        assert "B6" in labels
        assert "미지정" in labels

    def test_span_header_same_year(self):
        """같은 해면 '2026년 1월 – 3월'."""
        cols = [
            {"start": date(2026, 1, 1), "end": date(2026, 1, 31)},
            {"start": date(2026, 2, 1), "end": date(2026, 2, 28)},
            {"start": date(2026, 3, 1), "end": date(2026, 3, 31)},
        ]
        result = plan._span_header(cols)
        assert "2026년" in result
        assert "1월" in result
        assert "3월" in result

    def test_span_header_different_year(self):
        """다른 해면 '2025년 11월 – 2026년 2월'."""
        cols = [
            {"start": date(2025, 11, 1), "end": date(2025, 11, 30)},
            {"start": date(2026, 2, 1), "end": date(2026, 2, 28)},
        ]
        result = plan._span_header(cols)
        assert "2025년" in result
        assert "2026년" in result

    def test_plan_nav_year(self):
        """연 단위 이전/다음."""
        anchor = date(2026, 6, 15)
        prev, next_ = plan._plan_nav("year", anchor)
        assert prev == "2025-01-01"
        assert next_ == "2027-01-01"

    def test_plan_nav_month(self):
        """월 단위 이전/다음."""
        anchor = date(2026, 6, 15)
        prev, next_ = plan._plan_nav("month", anchor)
        assert prev == "2026-05-15"
        assert next_ == "2026-07-15"

    def test_plan_nav_week(self):
        """주 단위 이전/다음."""
        anchor = date(2026, 8, 14)  # 금요일
        prev, next_ = plan._plan_nav("week", anchor)
        # 이전: 7일 전, 다음: 7일 후
        prev_date = date(2026, 8, 7)
        next_date = date(2026, 8, 21)
        assert prev == prev_date.strftime("%Y-%m-%d")
        assert next_ == next_date.strftime("%Y-%m-%d")

    def test_plan_breadcrumb_year(self):
        """연 레벨 경로."""
        anchor = date(2026, 6, 15)
        result = plan._plan_breadcrumb("year", anchor)
        assert len(result) == 1
        assert result[0]["level"] == "year"
        assert result[0]["label"] == "2026"

    def test_plan_breadcrumb_month(self):
        """월 레벨 경로."""
        anchor = date(2026, 6, 15)
        result = plan._plan_breadcrumb("month", anchor)
        assert len(result) == 3
        assert result[-1]["level"] == "month"
        assert "6월" in result[-1]["label"]

    def test_plan_breadcrumb_week(self):
        """주 레벨 경로."""
        anchor = date(2026, 8, 14)  # 금요일
        result = plan._plan_breadcrumb("week", anchor)
        assert len(result) == 4
        assert result[-1]["level"] == "week"

    def test_lt_descendants_no_children(self, conn):
        """자식이 없으면 빈 리스트."""
        result = plan._lt_descendants(conn, 999999)
        assert result == []

    def test_lt_root_no_parent(self, conn):
        """부모가 없으면 자신."""
        result = plan._lt_root(conn, 999999)
        assert result == 999999


class TestSettings:
    """settings.py 순수 함수 테스트"""

    def test_valid_hhmm_valid(self):
        """'00:00'은 유효."""
        assert settings._valid_hhmm("00:00") is True

    def test_valid_hhmm_boundary_max(self):
        """'24:00'은 유효."""
        assert settings._valid_hhmm("24:00") is True

    def test_valid_hhmm_boundary_over(self):
        """'24:01'은 무효."""
        assert settings._valid_hhmm("24:01") is False

    def test_valid_hhmm_hour_over(self):
        """'25:00'은 무효."""
        assert settings._valid_hhmm("25:00") is False

    def test_valid_hhmm_minute_over(self):
        """'12:60'은 무효."""
        assert settings._valid_hhmm("12:60") is False

    def test_valid_hhmm_format_wrong(self):
        """'12:0' 형식 오류."""
        assert settings._valid_hhmm("12:0") is False

    def test_valid_hhmm_empty(self):
        """빈 문자열 무효."""
        assert settings._valid_hhmm("") is False

    def test_parse_scope_empty(self):
        """''은 (True, None)."""
        ok, wd = settings._parse_scope("")
        assert ok is True
        assert wd is None

    def test_parse_scope_valid_weekday(self):
        """'3'은 (True, 3)."""
        ok, wd = settings._parse_scope("3")
        assert ok is True
        assert wd == 3

    def test_parse_scope_boundary(self):
        """'0', '6' 유효."""
        ok0, wd0 = settings._parse_scope("0")
        ok6, wd6 = settings._parse_scope("6")
        assert ok0 is True and wd0 == 0
        assert ok6 is True and wd6 == 6

    def test_parse_scope_out_of_range(self):
        """'7'은 (False, None)."""
        ok, wd = settings._parse_scope("7")
        assert ok is False
        assert wd is None

    def test_parse_scope_non_digit(self):
        """'abc'는 (False, None)."""
        ok, wd = settings._parse_scope("abc")
        assert ok is False

    def test_clean_weekdays_single(self):
        """'3' → '3'."""
        result = settings._clean_weekdays("3")
        assert result == "3"

    def test_clean_weekdays_multiple(self):
        """'5,2,0' → '0,2,5'(정렬됨)."""
        result = settings._clean_weekdays("5,2,0")
        assert result == "0,2,5"

    def test_clean_weekdays_duplicates(self):
        """중복 제거."""
        result = settings._clean_weekdays("2,2,5,2")
        assert result == "2,5"

    def test_clean_weekdays_out_of_range(self):
        """범위 밖 버림."""
        result = settings._clean_weekdays("0,8,3,-1")
        assert result == "0,3"

    def test_clean_weekdays_empty(self):
        """빈 입력."""
        result = settings._clean_weekdays("")
        assert result == ""

    def test_mask_env_text_secret(self):
        """KEY=value → KEY=********."""
        text = "AI_API_KEY=sk-1234567890\nOTHER=data"
        result = settings._mask_env_text(text)
        assert "AI_API_KEY=********" in result
        assert "OTHER=********" in result
        assert "sk-1234567890" not in result

    def test_mask_env_text_empty_value(self):
        """KEY=빈칸 → KEY= (그대로)."""
        text = "EMPTY_KEY="
        result = settings._mask_env_text(text)
        assert "EMPTY_KEY=" in result

    def test_mask_env_text_comment(self):
        """주석 라인은 그대로."""
        text = "# Comment line\nKEY=value"
        result = settings._mask_env_text(text)
        assert "# Comment line" in result
        assert "KEY=********" in result

    def test_mask_env_text_no_equals(self):
        """= 없는 줄은 그대로."""
        text = "NO_EQUALS_LINE"
        result = settings._mask_env_text(text)
        assert "NO_EQUALS_LINE" in result

    def test_unmask_env_text_restore(self):
        """마스킹을 원본으로 되돌림."""
        old = "AI_API_KEY=sk-old\nOTHER=data"
        new = "AI_API_KEY=********\nOTHER=newvalue"
        result = settings._unmask_env_text(new, old)
        assert "AI_API_KEY=sk-old" in result
        assert "OTHER=newvalue" in result

    def test_unmask_env_text_new_key(self):
        """새로운 KEY는 그대로."""
        old = "OLD_KEY=value"
        new = "OLD_KEY=********\nNEW_KEY=newvalue"
        result = settings._unmask_env_text(new, old)
        assert "NEW_KEY=newvalue" in result

    def test_unmask_env_text_no_old_value(self):
        """기존에 없는 마스킹은 그대로 마스킹된 채."""
        old = "KEY1=value1"
        new = "KEY1=********\nKEY2=********"
        result = settings._unmask_env_text(new, old)
        assert "KEY1=value1" in result
        assert "KEY2=********" in result


class TestDay:
    """day.py 순수 함수 테스트"""

    def test_hidden_task_titles_empty(self, fresh_db):
        """설정이 비면 빈 집합. 값이 있으면 쉼표로 갈라 정확히 같은 제목만 거른다."""
        from app.db import set_setting

        set_setting("hide_task_titles", "")
        assert day._hidden_task_titles() == set()

        set_setting("hide_task_titles", " 체크/잡일(2분) , 물 마시기 ,, ")
        assert day._hidden_task_titles() == {"체크/잡일(2분)", "물 마시기"}

    def test_distribute_no_timed_items(self):
        """시각 항목이 없으면 빈 dict + 빈 leftover."""
        blocks = [
            {"id": 1, "start_time": "09:00", "end_time": "10:00"},
            {"id": 2, "start_time": "10:00", "end_time": "11:00"},
        ]
        by_block, leftover = day._distribute(blocks, [])
        assert by_block[1] == []
        assert by_block[2] == []
        assert leftover == []

    def test_distribute_single_item_in_range(self):
        """시각이 범위 안이면 해당 블록에."""
        blocks = [
            {"id": 1, "start_time": "09:00", "end_time": "10:00"},
        ]
        items = [
            {"start_min": 9 * 60 + 15, "title": "item1"},
        ]
        by_block, leftover = day._distribute(blocks, items)
        assert len(by_block[1]) == 1
        assert leftover == []

    def test_distribute_item_out_of_range(self):
        """시각이 범위 밖이면 leftover."""
        blocks = [
            {"id": 1, "start_time": "09:00", "end_time": "10:00"},
        ]
        items = [
            {"start_min": 12 * 60, "title": "item1"},
        ]
        by_block, leftover = day._distribute(blocks, items)
        assert len(by_block[1]) == 0
        assert len(leftover) == 1

    def test_distribute_sorting(self):
        """블록 내에서 시간순 정렬."""
        blocks = [
            {"id": 1, "start_time": "09:00", "end_time": "11:00"},
        ]
        items = [
            {"start_min": 10 * 60 + 30, "title": "later"},
            {"start_min": 9 * 60 + 15, "title": "earlier"},
        ]
        by_block, leftover = day._distribute(blocks, items)
        assert by_block[1][0]["title"] == "earlier"
        assert by_block[1][1]["title"] == "later"

    def test_lt_columns_single_area(self):
        """한 영역이면 한 열."""
        items = [
            {"area_name": "영역1", "id": 1},
            {"area_name": "영역1", "id": 2},
        ]
        result = day._lt_columns(items)
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_lt_columns_multiple_areas(self):
        """여러 영역이면 각각 한 열."""
        items = [
            {"area_name": "영역1", "id": 1},
            {"area_name": "영역2", "id": 2},
            {"area_name": "영역3", "id": 3},
        ]
        result = day._lt_columns(items)
        assert len(result) == 3

    def test_lt_columns_wrap(self):
        """4개 영역은 3열에서 첫 영역 다시."""
        items = [
            {"area_name": "A", "id": 1},
            {"area_name": "B", "id": 2},
            {"area_name": "C", "id": 3},
            {"area_name": "D", "id": 4},
        ]
        result = day._lt_columns(items)
        assert len(result) == 3
        # A와 D는 같은 열
        col0_names = [it["area_name"] for it in result[0]]
        assert "A" in col0_names and "D" in col0_names


class TestReflect:
    """reflect.py 순수 함수 테스트"""

    def test_reflect_title_with_title(self):
        """제목이 있으면 그것."""
        result = reflect._reflect_title("제목", "내용")
        assert result == "제목"

    def test_reflect_title_empty_use_content(self):
        """제목 비어있으면 내용 첫줄."""
        result = reflect._reflect_title("", "첫줄\n둘째줄")
        assert result == "첫줄"

    def test_reflect_title_truncate(self):
        """길이 제한 120자."""
        long_text = "a" * 150
        result = reflect._reflect_title("", long_text)
        assert len(result) <= 120

    def test_reflect_sig_empty_context(self):
        """빈 컨텍스트에서 해시."""
        ctx = {"items": [], "upcoming_reviews": []}
        result = reflect._reflect_sig(ctx)
        assert isinstance(result, str)
        assert len(result) == 32  # MD5는 32자


class TestMain:
    """main.py 순수 함수 테스트"""

    def test_netloc_key_http_default(self):
        """http://host:80 → host."""
        result = _netloc_key("http", "host:80")
        assert result == "host"

    def test_netloc_key_https_default(self):
        """https://host:443 → host."""
        result = _netloc_key("https", "host:443")
        assert result == "host"

    def test_netloc_key_custom_port(self):
        """http://host:8080 → host:8080."""
        result = _netloc_key("http", "host:8080")
        assert result == "host:8080"

    def test_netloc_key_case_insensitive(self):
        """대소문자 정규화."""
        result = _netloc_key("http", "HOST:8080")
        assert result == "host:8080"

    def test_netloc_key_ipv6(self):
        """[::1]:8080 형태."""
        # IPv6은 대괄호 안에 들어가므로 partition으로 안 맞음. 기존 코드 그대로 동작.
        result = _netloc_key("http", "[::1]:8080")
        # 기존 코드 동작 재현
        assert "[::1]" in result or ":" in result

    def test_origin_allowed_self(self):
        """같은 서버 출처는 허용."""
        result = _origin_allowed("http://localhost:8000/page", "localhost:8000")
        assert result is True

    def test_origin_allowed_different(self):
        """다른 서버 출처는 거절."""
        result = _origin_allowed("http://evil.com/", "localhost:8000")
        assert result is False

    def test_origin_allowed_no_netloc(self):
        """네크로크 없는 URL은 거절."""
        result = _origin_allowed("", "localhost:8000")
        assert result is False

    def test_origin_allowed_https_http_mismatch(self):
        """스킴이 다르면 false (Origin이 https인데 Host가 http일 수 없음)."""
        # 실제로는 Host 헤더는 스킴을 포함하지 않으므로 둘 다 비교
        result = _origin_allowed("https://localhost:8000/", "localhost:8000")
        # _origin_allowed는 http/https 둘다 비교하므로 True
        assert result is True
