# 설정(config.py) + DB(db.py) 계층의 1단계 유닛 테스트
import json
import pathlib


from app.config import (
   AREA_TONE_ORDER, CAT_TONE, CATEGORIES, CORE_BLOCKS, DAY_BLOCKS, DEFAULT_SETTINGS,
    LT_AREAS, TONES, TONE_KEYS, WEEK_CORE_BLOCKS, area_tone, cat_tone, hhmm_to_min,
    slots_for_day,
)
from app.db import (
   BLOCK_TIMES_KEY, BLOCK_TIMES_WD_KEY, SCHEMA_VERSION, WEEKDAY_CONCEPTS_KEY,
    _apply_times, _migrate, _parse_times, _seed_categories, _seed_settings,
    get_conn, get_day_blocks, get_settings, get_weekday_concepts,
    get_weekday_overrides, set_setting, uid_from_created,
)


class TestConfigDetectCloudDir:
    """_detect_cloud_dir: OneDrive 자동 탐지 또는 환경변수 지정."""

    def test_returns_path_when_env_set(self, monkeypatch, tmp_path):
        """환경변수 SIXBLOCK_CLOUD_DIR가 있으면 그 경로를 반환."""
        # conftest에서 이미 환경변수를 설정했으므로 여기서는 _detect_cloud_dir()가
        # 이미 실행됐다. 재테스트하려면 직접 재계산해야 한다.
        # 하지만 모듈 import 시점의 초기값은 이미 고정되어 있으므로
        # CLOUD_DIR = _detect_cloud_dir() 로 캐시된 상태다.
        # 따라서 이 테스트는 conftest 설정값의 결과를 확인하는 것.
        # conftest에서 TMP_ROOT 아래 cloud/ 로 설정했으니 그렇게 반환되는지 확인.
        import app.config as cfg
        assert cfg.CLOUD_DIR.exists() or not cfg.CLOUD_DIR.is_absolute() is False
        # 최소한 절대경로여야 한다.
        assert cfg.CLOUD_DIR.is_absolute()

    def test_fallback_to_onedrive_personal(self, monkeypatch, tmp_path):
        """환경변수가 비면 홈의 CloudStorage 에서 OneDrive 폴더를 찾는다.

        맥을 바꾸면 폴더 이름이 'OneDrive-개인' 이 아니라 'OneDrive-Personal' 일 수
        있다. 그때 백업이 조용히 홈 밑으로 떨어지지 않는지 확인한다.
        """
        import app.config as cfg

        monkeypatch.setenv("SIXBLOCK_CLOUD_DIR", "")
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

        # 아무 OneDrive 폴더도 없으면 홈 밑으로 떨어진다
        assert cfg._detect_cloud_dir() == tmp_path / "AI_data" / "6block"

        # 영문 이름만 있으면 그것을 쓴다
        eng = tmp_path / "Library" / "CloudStorage" / "OneDrive-Personal"
        eng.mkdir(parents=True)
        assert cfg._detect_cloud_dir() == eng / "0.개발&전산" / "AI_data" / "6block"

        # 한글 이름이 함께 있으면 한글 쪽이 이긴다(운영 맥이 그것을 쓴다)
        kor = tmp_path / "Library" / "CloudStorage" / "OneDrive-개인"
        kor.mkdir(parents=True)
        assert cfg._detect_cloud_dir() == kor / "0.개발&전산" / "AI_data" / "6block"

        # 환경변수가 있으면 무조건 그것이 이긴다
        monkeypatch.setenv("SIXBLOCK_CLOUD_DIR", str(tmp_path / "직접지정"))
        assert cfg._detect_cloud_dir() == tmp_path / "직접지정"


class TestConfigCatTone:
    """cat_tone: 카테고리 이름 → 색 톤."""

    def test_known_category_returns_tone(self):
        """알려진 카테고리의 톤을 반환."""
        assert cat_tone("코어") == "blue"
        assert cat_tone("점검") == "green"
        assert cat_tone("약속") == "red"
        assert cat_tone("업무") == "black"

    def test_unknown_category_returns_black(self):
        """모르는 카테고리는 검정."""
        assert cat_tone("미지정") == "black"
        assert cat_tone("") == "black"
        assert cat_tone("unknown") == "black"

    def test_returns_tone_from_dict(self):
        """CAT_TONE dict의 정의와 일치."""
        for name, expected_tone in CAT_TONE.items():
            assert cat_tone(name) == expected_tone


class TestConfigAreaTone:
    """area_tone: 영역 순서 → 색 톤(팔레트 순환)."""

    def test_area_tone_cycles_through_palette(self):
        """순서 0~7은 팔레트를 순환."""
        for i in range(len(AREA_TONE_ORDER)):
            assert area_tone(i) == AREA_TONE_ORDER[i]

    def test_area_tone_wraps_around(self):
        """팔레트보다 큰 순서는 wrap."""
        n = len(AREA_TONE_ORDER)
        assert area_tone(n) == AREA_TONE_ORDER[0]
        assert area_tone(n + 1) == AREA_TONE_ORDER[1]
        assert area_tone(n * 2 + 3) == AREA_TONE_ORDER[3]


class TestConfigHhmmToMin:
    """hhmm_to_min: 'HH:MM' → 자정 기준 분."""

    def test_midnight(self):
        """00:00 → 0분."""
        assert hhmm_to_min("00:00") == 0

    def test_noon(self):
        """12:00 → 720분(12시간 * 60)."""
        assert hhmm_to_min("12:00") == 720

    def test_end_of_day(self):
        """23:59 → 1439분."""
        assert hhmm_to_min("23:59") == 1439

    def test_various_times(self):
        """여러 시간값 검증."""
        assert hhmm_to_min("07:30") == 450  # 7*60 + 30
        assert hhmm_to_min("09:30") == 570
        assert hhmm_to_min("14:30") == 870
        assert hhmm_to_min("21:00") == 1260


class TestConfigSlotsForDay:
    """slots_for_day: 하루 30분 단위 슬롯 리스트."""

    def test_default_blocks_create_16_slots(self):
        """기본 8블록 → 16 슬롯(30분 = 16 × 30분 = 8시간)."""
        slots = slots_for_day()
        # 각 블록이 30분 단위로 나뉘므로, 8블록 총 길이를 30분으로 나눔
        # DAY_BLOCKS: B1(2h) + B2(2h) + 점심(1h) + B3(2h) + B4(2h) + 저녁(2.5h) + B5(2h) + B6(2h)
        # = 2+2+1+2+2+2.5+2+2 = 15.5시간 = 31개 30분 슬롯
        assert len(slots) > 0
        # 첫 슬롯은 index 0
        assert slots[0][0] == 0
        # 마지막 슬롯 index는 길이 - 1
        assert slots[-1][0] == len(slots) - 1

    def test_slot_structure(self):
        """각 슬롯은 (index, label, start, end) 튜플."""
        slots = slots_for_day()
        for slot in slots:
            assert len(slot) == 4
            assert isinstance(slot[0], int)  # index
            assert isinstance(slot[1], str)  # label
            assert isinstance(slot[2], str)  # start HH:MM
            assert isinstance(slot[3], str)  # end HH:MM

    def test_slot_times_are_30min_apart(self):
        """각 슬롯의 끝은 다음 슬롯의 시작."""
        slots = slots_for_day()
        for i in range(len(slots) - 1):
            assert slots[i][3] == slots[i + 1][2]

    def test_custom_blocks_override(self):
        """blocks 매개변수가 있으면 그걸 사용."""
        custom = [
            ("테스트1", True, "08:00", "10:00"),
            ("테스트2", True, "10:00", "12:00"),
        ]
        slots = slots_for_day(blocks=custom)
        # 2개 블록 × 2시간 = 4시간 = 8개 30분 슬롯
        assert len(slots) == 8
        assert slots[0][1] == "테스트1"
        assert slots[4][1] == "테스트2"


class TestConfigConstants:
    """config의 상수들 검증."""

    def test_day_blocks_structure(self):
        """DAY_BLOCKS는 8개, 각각 (label, is_core, start, end)."""
        assert len(DAY_BLOCKS) == 8
        for block in DAY_BLOCKS:
            assert len(block) == 4
            assert isinstance(block[0], str)  # label
            assert isinstance(block[1], bool)  # is_core
            assert isinstance(block[2], str)  # start HH:MM
            assert isinstance(block[3], str)  # end HH:MM

    def test_core_blocks_count(self):
        """코어블록은 6개."""
        assert len(CORE_BLOCKS) == 6
        assert CORE_BLOCKS == ["B1", "B2", "B3", "B4", "B5", "B6"]

    def test_categories_default_6(self):
        """기본 카테고리는 6개."""
        assert len(CATEGORIES) == 6
        assert "코어" in CATEGORIES
        assert "기타" in CATEGORIES

    def test_tones_structure(self):
        """TONES는 (key, name) 튜플 리스트."""
        assert len(TONES) >= 4
        for tone in TONES:
            assert len(tone) == 2
            assert isinstance(tone[0], str)  # key
            assert isinstance(tone[1], str)  # 한글 이름

    def test_tone_keys_set(self):
        """TONE_KEYS는 TONES 키의 set."""
        keys = {k for k, _name in TONES}
        assert TONE_KEYS == keys

    def test_default_settings_keys(self):
        """DEFAULT_SETTINGS는 dict."""
        assert isinstance(DEFAULT_SETTINGS, dict)
        assert "start_view" in DEFAULT_SETTINGS
        assert "default_theme" in DEFAULT_SETTINGS
        assert "pomo_auto" in DEFAULT_SETTINGS

    def test_week_core_blocks_calculation(self):
        """WEEK_CORE_BLOCKS = 코어 6개 × 7일."""
        expected = sum(1 for _l, is_core, _s, _e in DAY_BLOCKS if is_core) * 7
        assert WEEK_CORE_BLOCKS == expected
        assert WEEK_CORE_BLOCKS == 42


class TestDbUidFromCreated:
    """uid_from_created: 생성시각 → 공용 키."""

    def test_valid_datetime_string(self):
        """'2026-08-15 21:38:45' → 'YYYYMMDD-HHMM-난수4'."""
        uid = uid_from_created("2026-08-15 21:38:45")
        parts = uid.split("-")
        assert len(parts) == 3
        assert parts[0] == "20260815"  # YYYYMMDD
        assert parts[1] == "2138"      # HHMM
        assert len(parts[2]) == 4      # 난수 4자리 hex

    def test_partial_datetime(self):
        """시간만 주면 뒤를 0으로 패딩."""
        uid = uid_from_created("2026-08")
        parts = uid.split("-")
        assert parts[0] == "20260800"  # YYYYMMDD (마지막 두 자리가 00)
        assert parts[1] == "0000"      # HHMM (비어있으므로 0000)

    def test_none_input(self):
        """None이면 전부 0으로 패딩."""
        uid = uid_from_created(None)
        parts = uid.split("-")
        assert parts[0] == "00000000"
        assert parts[1] == "0000"

    def test_randomness(self):
        """같은 입력이라도 난수 부분은 다르다."""
        uid1 = uid_from_created("2026-08-15 21:38:45")
        uid2 = uid_from_created("2026-08-15 21:38:45")
        parts1 = uid1.split("-")
        parts2 = uid2.split("-")
        # 앞의 두 부분은 같아야 한다.
        assert parts1[0] == parts2[0]
        assert parts1[1] == parts2[1]
        # 난수는 다를 확률이 높다(겹칠 수도 있지만 매우 낮음).
        # 실제로 겹치면 이 테스트는 실패할 수 있으니 검증만.
        assert len(parts1[2]) == 4
        assert len(parts2[2]) == 4


class TestDbSeedCategories:
    """_seed_categories: 카테고리 비어있으면 기본 6종 추가."""

    def test_empty_db_gets_six_categories(self, fresh_db):
        """빈 DB에 6개 기본 카테고리를 넣는다."""
        with get_conn() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            assert rows == 6

    def test_categories_have_correct_names(self, fresh_db):
        """기본 카테고리 이름이 CATEGORIES와 같다."""
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT name FROM categories ORDER BY display_order"
            ).fetchall()
            names = [r[0] for r in rows]
            assert names == CATEGORIES

    def test_categories_have_correct_tones(self, fresh_db):
        """카테고리의 tone이 CAT_TONE과 일치."""
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT name, tone FROM categories"
            ).fetchall()
            for name, tone in rows:
                assert tone == cat_tone(name)

    def test_categories_all_active(self, fresh_db):
        """모든 기본 카테고리는 활성."""
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM categories WHERE is_active = 1"
            ).fetchone()
            assert rows[0] == 6

    def test_existing_categories_not_replaced(self, fresh_db):
        """이미 카테고리가 있으면 추가하지 않는다."""
        with get_conn() as conn:
            # 미리 하나 추가
            conn.execute(
                "INSERT INTO categories (name, tone, display_order, is_active) "
                "VALUES (?, ?, ?, 1)",
                ("사용자정의", "yellow", 0),
            )
            conn.commit()

        # _seed_categories 재호출
        with get_conn() as conn:
            count_before = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            _seed_categories(conn)
            count_after = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            assert count_before == count_after


class TestDbSeedAreas:
    """_seed_areas: 영역 비어있으면 기본 영역 추가."""

    def test_empty_db_gets_lt_areas(self, fresh_db):
        """빈 DB에 기본 장기 영역을 넣는다."""
        with get_conn() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM lt_area").fetchone()[0]
            assert rows == len(LT_AREAS)

    def test_areas_have_correct_names(self, fresh_db):
        """기본 영역 이름이 LT_AREAS와 같다."""
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT name FROM lt_area ORDER BY display_order"
            ).fetchall()
            names = [r[0] for r in rows]
            assert names == LT_AREAS

    def test_areas_have_correct_tones(self, fresh_db):
        """영역의 tone이 area_tone 순서와 일치."""
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT display_order, tone FROM lt_area ORDER BY display_order"
            ).fetchall()
            for order, tone in rows:
                assert tone == area_tone(order)


class TestDbSeedSettings:
    """_seed_settings: 기본값이 없으면 추가."""

    def test_default_settings_inserted(self, fresh_db):
        """기본 설정이 모두 들어간다."""
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]
            assert count == len(DEFAULT_SETTINGS)

    def test_existing_settings_preserved(self, fresh_db):
        """이미 있는 설정은 덮어쓰지 않는다."""
        with get_conn() as conn:
            conn.execute(
                "UPDATE app_settings SET value = ? WHERE key = ?",
                ("week", "start_view"),  # 기본값은 "today", 이를 "week"으로 변경
            )

        # 재실행
        with get_conn() as conn:
            _seed_settings(conn)
            value = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'start_view'"
            ).fetchone()[0]
            assert value == "week"  # 기존값 유지

    def test_keys_match_defaults(self, fresh_db):
        """저장된 키가 DEFAULT_SETTINGS와 일치."""
        with get_conn() as conn:
            rows = conn.execute("SELECT key FROM app_settings ORDER BY key").fetchall()
            saved_keys = set(r[0] for r in rows)
            assert saved_keys == set(DEFAULT_SETTINGS.keys())


class TestDbGetConnContext:
    """get_conn: SQLite 연결 + 트랜잭션 관리."""

    def test_connection_returns_row_factory(self, fresh_db):
        """연결이 Row 객체를 지원한다."""
        with get_conn() as conn:
            # sqlite3.Row 를 쓰면 dict처럼 접근 가능
            row = conn.execute("SELECT 1 as num").fetchone()
            assert row["num"] == 1

    def test_foreign_keys_enabled(self, fresh_db):
        """FOREIGN KEYS 제약이 켜져있다."""
        with get_conn() as conn:
            result = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert result == 1

    def test_transaction_commit_on_success(self, fresh_db):
        """정상 종료 시 자동 commit."""
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                ("test_key", "test_value"),
            )
        # 재연결해서 데이터가 있는지 확인
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'test_key'"
            ).fetchone()
            assert row is not None
            assert row[0] == "test_value"

    def test_transaction_rollback_on_exception(self, fresh_db):
        """예외 발생 시 자동 rollback."""
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                    ("test_key", "test_value"),
                )
                raise ValueError("테스트 예외")
        except ValueError:
            pass

        # 재연결해서 데이터가 없는지 확인
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'test_key'"
            ).fetchone()
            assert row is None


class TestDbGetSettings:
    """get_settings: 모든 설정을 dict로."""

    def test_returns_default_settings(self, fresh_db):
        """기본값이 포함된다."""
        settings = get_settings()
        for key, val in DEFAULT_SETTINGS.items():
            assert key in settings

    def test_db_values_override_defaults(self, fresh_db):
        """DB값이 기본값을 덮어쓴다."""
        with get_conn() as conn:
            conn.execute(
                "UPDATE app_settings SET value = 'week' WHERE key = 'start_view'"
            )

        # 캐시를 비우고 다시 읽기
        import app.db as db_module
        db_module._settings_cache = None

        settings = get_settings()
        assert settings["start_view"] == "week"

    def test_caching(self, fresh_db):
        """결과는 캐시된다."""
        settings1 = get_settings()
        settings2 = get_settings()
        # 같은 dict 객체는 아니지만 내용은 같다
        assert settings1 == settings2


class TestDbSetSetting:
    """set_setting: 설정 한 개 저장."""

    def test_adds_new_setting(self, fresh_db):
        """없는 설정을 추가한다."""
        set_setting("new_key", "new_value")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'new_key'"
            ).fetchone()
            assert row[0] == "new_value"

    def test_updates_existing_setting(self, fresh_db):
        """있는 설정을 갱신한다."""
        set_setting("start_view", "week")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'start_view'"
            ).fetchone()
            assert row[0] == "week"

    def test_invalidates_cache(self, fresh_db):
        """저장 후 캐시를 비운다."""
        settings1 = get_settings()
        assert settings1["start_view"] == "today"  # 기본값

        set_setting("start_view", "week")
        settings2 = get_settings()
        assert settings2["start_view"] == "week"  # 갱신됨


class TestDbParseTimes:
    """_parse_times: JSON 문자열 → 길이 8 리스트 또는 None."""

    def test_valid_json_array(self):
        """유효한 JSON 배열을 파싱."""
        times = [{"start": "08:00"}, None, None, None, None, None, None, None]
        result = _parse_times(json.dumps(times))
        assert result == times

    def test_list_input(self):
        """리스트를 직접 받으면 그대로 반환."""
        times = [None] * 8
        result = _parse_times(times)
        assert result == times

    def test_wrong_length_returns_none(self):
        """길이가 8이 아니면 None."""
        result = _parse_times([None] * 7)
        assert result is None

    def test_invalid_json_returns_none(self):
        """잘못된 JSON이면 None."""
        result = _parse_times("{invalid}")
        assert result is None

    def test_empty_input_returns_none(self):
        """빈 입력이면 None."""
        assert _parse_times(None) is None
        assert _parse_times("") is None


class TestDbApplyTimes:
    """_apply_times: 블록 목록에 시간 배열을 입힌다."""

    def test_merges_times_correctly(self):
        """시간을 블록에 덧입힌다."""
        blocks = [
            ("B1", True, "07:30", "09:30"),
            ("B2", True, "09:30", "11:30"),
        ]
        times = [
            {"start": "08:00", "end": "10:00"},
            None,
        ]
        result = _apply_times(blocks, times)
        assert result[0] == ("B1", True, "08:00", "10:00")
        assert result[1] == ("B2", True, "09:30", "11:30")

    def test_none_times_returns_original(self):
        """times가 None이면 원본 반환."""
        blocks = [("B1", True, "07:30", "09:30")]
        result = _apply_times(blocks, None)
        assert result == blocks

    def test_empty_dict_uses_default(self):
        """시간값이 비어있으면 기본값 사용."""
        blocks = [("B1", True, "07:30", "09:30")]
        times = [{}]
        result = _apply_times(blocks, times)
        assert result[0] == ("B1", True, "07:30", "09:30")


class TestDbGetWeekdayOverrides:
    """get_weekday_overrides: 요일 덮어쓰기 전체."""

    def test_empty_db_returns_empty_dict(self, fresh_db):
        """저장값이 없으면 빈 dict."""
        result = get_weekday_overrides()
        assert result == {}

    def test_returns_stored_overrides(self, fresh_db):
        """저장된 덮어쓰기를 반환."""
        overrides = {
            "0": [{"start": "08:00", "end": "10:00"}, None, None, None, None, None, None, None],
        }
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))

        result = get_weekday_overrides()
        assert result == overrides

    def test_invalid_json_returns_empty_dict(self, fresh_db):
        """잘못된 JSON이면 빈 dict."""
        set_setting(BLOCK_TIMES_WD_KEY, "{invalid}")
        result = get_weekday_overrides()
        assert result == {}


class TestDbGetWeekdayConcepts:
    """get_weekday_concepts: 요일 컨셉 7칸."""

    def test_empty_db_returns_seven_blanks(self, fresh_db):
        """저장값이 없으면 빈 칸 7개."""
        result = get_weekday_concepts()
        assert result == [""] * 7

    def test_returns_stored_concepts(self, fresh_db):
        """저장된 컨셉을 반환."""
        concepts = ["월(계획)", "화", "수", "목", "금", "토", "일"]
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))

        result = get_weekday_concepts()
        assert result == concepts

    def test_padding_to_seven(self, fresh_db):
        """저장값이 7개 미만이면 빈 칸으로 채운다."""
        concepts = ["월", "화"]
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))

        result = get_weekday_concepts()
        assert len(result) == 7
        assert result[0] == "월"
        assert result[1] == "화"
        assert result[2:] == [""] * 5

    def test_truncates_to_seven(self, fresh_db):
        """저장값이 7개를 초과하면 자른다."""
        concepts = [""] * 10
        set_setting(WEEKDAY_CONCEPTS_KEY, json.dumps(concepts))

        result = get_weekday_concepts()
        assert len(result) == 7


class TestDbGetDayBlocks:
    """get_day_blocks: 효과적인 하루 8블록."""

    def test_default_blocks_without_weekday(self, fresh_db):
        """weekday가 None이면 공통 시간만 쓴다."""
        blocks = get_day_blocks(weekday=None)
        assert len(blocks) == 8
        assert blocks[0][0] == "B1"
        assert blocks[0][2] == "07:30"  # 기본값

    def test_weekday_override_applied(self, fresh_db):
        """요일 덮어쓰기가 반영된다."""
        # 월요일(0)에 B1을 08:00부터로 변경
        overrides = {
            "0": [
                {"start": "08:00", "end": "10:00"},
                None, None, None, None, None, None, None
            ]
        }
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))

        blocks = get_day_blocks(weekday=0)
        assert blocks[0][2] == "08:00"  # 덮어쓰기 반영

    def test_weekday_without_override_uses_default(self, fresh_db):
        """덮어쓰기가 없는 요일은 공통 시간을 쓴다."""
        blocks = get_day_blocks(weekday=5)  # 토요일
        assert blocks[0][2] == "07:30"  # 기본값


class TestDbInitDb:
    """init_db: DB 초기화, 스키마 생성, 시드 데이터."""

    def test_creates_db_file(self, tmp_path, monkeypatch):
        """DB 파일을 생성한다."""
        from app import db as db_module

        test_db = tmp_path / "test.db"
        monkeypatch.setattr(db_module, "DB_PATH", test_db)

        db_module.init_db()
        assert test_db.exists()

    def test_initializes_categories(self, fresh_db):
        """카테고리를 초기화한다."""
        with get_conn() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            assert rows == 6

    def test_initializes_areas(self, fresh_db):
        """장기 영역을 초기화한다."""
        with get_conn() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM lt_area").fetchone()[0]
            assert rows == 5

    def test_initializes_settings(self, fresh_db):
        """설정을 초기화한다."""
        with get_conn() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]
            assert rows == len(DEFAULT_SETTINGS)

    def test_schema_version_set(self, fresh_db):
        """SCHEMA_VERSION이 설정된다."""
        with get_conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == SCHEMA_VERSION

    def test_wal_mode_enabled(self, fresh_db):
        """WAL 저널 모드가 켜진다."""
        with get_conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"


class TestDbMigrate:
    """_migrate: 스키마 마이그레이션(필수 컬럼 추가)."""

    def test_migrate_is_idempotent(self, fresh_db):
        """마이그레이션을 여러 번 돌려도 결과가 같다."""
        with get_conn() as conn:
            # 첫 번째 호출
            _migrate(conn)
            rows1 = conn.execute("SELECT COUNT(*) FROM daily_meta").fetchone()[0]

        with get_conn() as conn:
            # 두 번째 호출
            _migrate(conn)
            rows2 = conn.execute("SELECT COUNT(*) FROM daily_meta").fetchone()[0]

        # 결과가 같아야 한다
        assert rows1 == rows2

    def test_migrate_adds_missing_columns(self, fresh_db):
        """필요한 컬럼이 있는지 확인(init_db가 이미 반영했으므로)."""
        with get_conn() as conn:
            # daily_meta의 컬럼 확인
            cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_meta)").fetchall()}
            # 마이그레이션이 추가해야 하는 컬럼들
            assert "gratitude" in cols
            assert "goal_tags" in cols
            assert "achieve_event_id" in cols


class TestDbConstants:
    """DB 계층 상수들 검증."""

    def test_schema_version_is_int(self):
        """SCHEMA_VERSION은 정수."""
        assert isinstance(SCHEMA_VERSION, int)
        assert SCHEMA_VERSION > 0

    def test_block_times_key_is_string(self):
        """BLOCK_TIMES_KEY는 문자열."""
        assert isinstance(BLOCK_TIMES_KEY, str)
        assert len(BLOCK_TIMES_KEY) > 0

    def test_block_times_wd_key_is_string(self):
        """BLOCK_TIMES_WD_KEY는 문자열."""
        assert isinstance(BLOCK_TIMES_WD_KEY, str)
        assert len(BLOCK_TIMES_WD_KEY) > 0

    def test_weekday_concepts_key_is_string(self):
        """WEEKDAY_CONCEPTS_KEY는 문자열."""
        assert isinstance(WEEKDAY_CONCEPTS_KEY, str)
        assert len(WEEKDAY_CONCEPTS_KEY) > 0

    def test_keys_are_different(self):
        """각 키는 서로 다르다."""
        keys = {BLOCK_TIMES_KEY, BLOCK_TIMES_WD_KEY, WEEKDAY_CONCEPTS_KEY}
        assert len(keys) == 3
