# 설정·데이터·고결감 통합 테스트. 설정 변경이 기존 기록에 미치는 영향을 검증한다
import pytest

from app.common import ensure_day_skeleton, _day_has_content
from app.db import get_conn


class TestBlockTimesDataPreservation:
    """블록 시간 변경이 기존 사용자 입력을 어떻게 다루는지 검증한다.

    핵심: _day_has_content 가 확인하는 모든 컬럼이 실제로 보존되는지 확인해야 한다.
    - slots: is_routine, do_text, category_id, did_text, done, wk_todo
    - blocks: plan_text, see_text, name, is_core(카테고리는 코어만),
              category_id, location, wk_todo
    """

    def _set_new_block_times(self, client):
        """블록 시간을 새로운 값으로 변경한다."""
        response = client.post(
            "/settings/blocktimes",
            data={
                "scope": "",  # 공통 (모든 요일)
                "start_0": "08:00", "end_0": "09:00",
                "start_1": "09:00", "end_1": "10:00",
                "start_2": "10:00", "end_2": "11:00",
                "start_3": "11:00", "end_3": "12:00",
                "start_4": "12:00", "end_4": "13:00",
                "start_5": "13:00", "end_5": "14:00",
                "start_6": "14:00", "end_6": "15:00",
                "start_7": "15:00", "end_7": "16:00",
            }
        )
        assert response.status_code == 200, f"블록 시간 설정 실패: {response.json()}"

    def test_slot_do_text_preserved_after_block_time_change(self, client):
        """슬롯의 do_text(계획)가 블록 시간 변경 후 보존되는가."""
        date_str = "2026-08-15"

        # 1. 골격 생성 및 데이터 입력
        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)
            slot = conn.execute(
                "SELECT id FROM slots WHERE date = ? ORDER BY slot_index LIMIT 1",
                (date_str,)
            ).fetchone()
            slot_id = slot["id"]
            conn.execute(
                "UPDATE slots SET do_text = ? WHERE id = ?",
                ("테스트 계획", slot_id)
            )

        # 2. 블록 시간 변경
        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")  # ensure_day_skeleton 호출 유도

        # 3. 데이터 보존 확인
        with get_conn() as conn:
            result = conn.execute(
                "SELECT do_text FROM slots WHERE id = ?", (slot_id,)
            ).fetchone()
            assert result["do_text"] == "테스트 계획", \
                f"do_text 손실됨: {result['do_text']}"

    def test_slot_did_text_preserved_after_block_time_change(self, client):
        """슬롯의 did_text(한 일)가 블록 시간 변경 후 보존되는가."""
        date_str = "2026-08-16"

        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)
            slot = conn.execute(
                "SELECT id FROM slots WHERE date = ? ORDER BY slot_index LIMIT 1",
                (date_str,)
            ).fetchone()
            slot_id = slot["id"]
            conn.execute(
                "UPDATE slots SET did_text = ? WHERE id = ?",
                ("완료한 작업", slot_id)
            )

        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")

        with get_conn() as conn:
            result = conn.execute(
                "SELECT did_text FROM slots WHERE id = ?", (slot_id,)
            ).fetchone()
            assert result["did_text"] == "완료한 작업"

    def test_slot_done_flag_preserved_after_block_time_change(self, client):
        """슬롯의 완료 플래그가 블록 시간 변경 후 보존되는가."""
        date_str = "2026-08-17"

        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)
            slot = conn.execute(
                "SELECT id FROM slots WHERE date = ? ORDER BY slot_index LIMIT 1",
                (date_str,)
            ).fetchone()
            slot_id = slot["id"]
            conn.execute(
                "UPDATE slots SET done = 1, did_text = ? WHERE id = ?",
                ("완료됨", slot_id)
            )

        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")

        with get_conn() as conn:
            result = conn.execute(
                "SELECT done FROM slots WHERE id = ?", (slot_id,)
            ).fetchone()
            assert result["done"] == 1

    def test_slot_category_preserved_after_block_time_change(self, client):
        """슬롯의 구분이 블록 시간 변경 후 보존되는가."""
        date_str = "2026-08-18"

        with get_conn() as conn:
            # 구분 생성
            cat = conn.execute(
                "INSERT INTO categories (name, tone, display_order, is_active) "
                "VALUES (?, ?, ?, 1) RETURNING id",
                ("테스트분류", "blue", 1)
            ).fetchone()
            cat_id = cat["id"]

            ensure_day_skeleton(conn, date_str)
            slot = conn.execute(
                "SELECT id FROM slots WHERE date = ? ORDER BY slot_index LIMIT 1",
                (date_str,)
            ).fetchone()
            slot_id = slot["id"]
            # 사용자 입력으로 인정되려면 do_text나 is_routine=0이 필요
            conn.execute(
                "UPDATE slots SET category_id = ?, do_text = ?, is_routine = 0 WHERE id = ?",
                (cat_id, "구분 지정", slot_id)
            )

        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")

        with get_conn() as conn:
            result = conn.execute(
                "SELECT category_id FROM slots WHERE id = ?", (slot_id,)
            ).fetchone()
            assert result["category_id"] == cat_id

    def test_block_plan_text_preserved_after_block_time_change(self, client):
        """블록의 plan_text가 블록 시간 변경 후 보존되는가."""
        date_str = "2026-08-19"

        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)
            block = conn.execute(
                "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
                (date_str,)
            ).fetchone()
            block_id = block["id"]
            conn.execute(
                "UPDATE blocks SET plan_text = ? WHERE id = ?",
                ("블록 계획", block_id)
            )

        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")

        with get_conn() as conn:
            result = conn.execute(
                "SELECT plan_text FROM blocks WHERE id = ?", (block_id,)
            ).fetchone()
            assert result["plan_text"] == "블록 계획"

    def test_block_see_text_preserved_after_block_time_change(self, client):
        """블록의 see_text가 블록 시간 변경 후 보존되는가."""
        date_str = "2026-08-20"

        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)
            block = conn.execute(
                "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
                (date_str,)
            ).fetchone()
            block_id = block["id"]
            conn.execute(
                "UPDATE blocks SET see_text = ? WHERE id = ?",
                ("블록 검토", block_id)
            )

        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")

        with get_conn() as conn:
            result = conn.execute(
                "SELECT see_text FROM blocks WHERE id = ?", (block_id,)
            ).fetchone()
            assert result["see_text"] == "블록 검토"

    def test_block_name_preserved_after_block_time_change(self, client):
        """블록의 name이 블록 시간 변경 후 보존되는가."""
        date_str = "2026-08-21"

        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)
            block = conn.execute(
                "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
                (date_str,)
            ).fetchone()
            block_id = block["id"]
            conn.execute(
                "UPDATE blocks SET name = ? WHERE id = ?",
                ("커스텀 이름", block_id)
            )

        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")

        with get_conn() as conn:
            result = conn.execute(
                "SELECT name FROM blocks WHERE id = ?", (block_id,)
            ).fetchone()
            assert result["name"] == "커스텀 이름"

    def test_block_location_preserved_after_block_time_change(self, client):
        """블록의 location이 블록 시간 변경 후 보존되는가."""
        date_str = "2026-08-22"

        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)
            block = conn.execute(
                "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
                (date_str,)
            ).fetchone()
            block_id = block["id"]
            conn.execute(
                "UPDATE blocks SET location = ? WHERE id = ?",
                ("회의실 B", block_id)
            )

        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")

        with get_conn() as conn:
            result = conn.execute(
                "SELECT location FROM blocks WHERE id = ?", (block_id,)
            ).fetchone()
            assert result["location"] == "회의실 B"

    def test_block_category_preserved_after_block_time_change(self, client):
        """블록의 구분이 블록 시간 변경 후 보존되는가(코어 블록만)."""
        date_str = "2026-08-23"

        with get_conn() as conn:
            # 구분 생성
            cat = conn.execute(
                "INSERT INTO categories (name, tone, display_order, is_active) "
                "VALUES (?, ?, ?, 1) RETURNING id",
                ("블록구분", "red", 2)
            ).fetchone()
            cat_id = cat["id"]

            ensure_day_skeleton(conn, date_str)
            # 코어 블록만 (is_core=1)
            block = conn.execute(
                "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
                (date_str,)
            ).fetchone()
            block_id = block["id"]
            conn.execute(
                "UPDATE blocks SET category_id = ? WHERE id = ?",
                (cat_id, block_id)
            )

        self._set_new_block_times(client)
        client.get(f"/day/{date_str}")

        with get_conn() as conn:
            result = conn.execute(
                "SELECT category_id FROM blocks WHERE id = ?", (block_id,)
            ).fetchone()
            assert result["category_id"] == cat_id


class TestBlockTimeSkeletonRebuild:
    """입력이 없는 날은 블록 시간 변경 후 골격이 재생성되는지 검증한다."""

    def test_skeleton_rebuilds_when_no_user_data(self, client):
        """데이터가 없는 날은 블록 시간 변경 후 골격이 재생성되는가."""
        date_str = "2026-09-01"

        # 초기 골격 생성 (기본 시간표)
        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)
            original_blocks = conn.execute(
                "SELECT COUNT(*) as cnt FROM blocks WHERE date = ?", (date_str,)
            ).fetchone()["cnt"]
            # 원래 첫 블록 시간 확인 (기본값: B1은 07:30-09:30)
            original_first = conn.execute(
                "SELECT start_time FROM blocks WHERE date = ? ORDER BY block_order LIMIT 1",
                (date_str,)
            ).fetchone()
            assert original_first["start_time"] == "07:30"

        # 블록 시간 변경 (08:00 시작으로)
        response = client.post(
            "/settings/blocktimes",
            data={
                "scope": "",
                "start_0": "08:00", "end_0": "09:00",
                "start_1": "09:00", "end_1": "10:00",
                "start_2": "10:00", "end_2": "11:00",
                "start_3": "11:00", "end_3": "12:00",
                "start_4": "12:00", "end_4": "13:00",
                "start_5": "13:00", "end_5": "14:00",
                "start_6": "14:00", "end_6": "15:00",
                "start_7": "15:00", "end_7": "16:00",
            }
        )
        assert response.status_code == 200

        # 페이지 방문으로 ensure_day_skeleton 호출 유도
        client.get(f"/day/{date_str}")

        # 블록이 새 시간표로 재생성되었는가?
        with get_conn() as conn:
            new_blocks = conn.execute(
                "SELECT COUNT(*) as cnt FROM blocks WHERE date = ?", (date_str,)
            ).fetchone()["cnt"]
            new_first = conn.execute(
                "SELECT start_time FROM blocks WHERE date = ? ORDER BY block_order LIMIT 1",
                (date_str,)
            ).fetchone()
            assert new_blocks == original_blocks, \
                f"블록 개수가 달라짐: {original_blocks} -> {new_blocks}"
            assert new_first["start_time"] == "08:00", \
                f"첫 블록이 새 시간으로 변경되지 않음: {new_first['start_time']}"


class TestCategoryManagement:
    """구분 추가·수정·삭제가 제대로 작동하는지 검증한다."""

    def test_add_category_via_http(self, client):
        """새로운 구분을 HTTP로 추가한다."""
        response = client.post(
            "/settings/category/add",
            data={"name": "새로운구분", "tone": "green"}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["ok"] is True
        assert result["name"] == "새로운구분"

        # DB 확인
        with get_conn() as conn:
            cat = conn.execute(
                "SELECT * FROM categories WHERE name = ?", ("새로운구분",)
            ).fetchone()
            assert cat is not None
            assert cat["is_active"] == 1

    def test_update_category_name_via_http(self, client):
        """구분의 이름을 HTTP로 수정한다."""
        with get_conn() as conn:
            cat = conn.execute(
                "INSERT INTO categories (name, tone, display_order, is_active) "
                "VALUES (?, ?, ?, 1) RETURNING id",
                ("원래이름", "blue", 1)
            ).fetchone()
            cat_id = cat["id"]

        response = client.post(
            "/settings/category/update",
            data={"id": str(cat_id), "name": "수정된이름"}
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

        with get_conn() as conn:
            updated = conn.execute(
                "SELECT name FROM categories WHERE id = ?", (cat_id,)
            ).fetchone()
            assert updated["name"] == "수정된이름"

    def test_cannot_hide_last_active_category(self, client):
        """마지막 활성 구분을 숨길 수 없다."""
        # 현재 활성 구분이 몇 개인지 확인
        with get_conn() as conn:
            active_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM categories WHERE is_active = 1"
            ).fetchone()["cnt"]

            if active_count == 1:
                # 마지막이므로 숨길 수 없음
                cat = conn.execute(
                    "SELECT id FROM categories WHERE is_active = 1 LIMIT 1"
                ).fetchone()
                cat_id = cat["id"]

                response = client.post(
                    "/settings/category/update",
                    data={"id": str(cat_id), "is_active": "0"}
                )
                assert response.status_code == 400
                assert "최소" in response.json()["error"]


class TestEnvFileMasking:
    """env 파일의 마스킹·언마스킹이 제대로 작동하는지 검증한다."""

    def test_env_values_masked_in_response(self, client, test_env_file):
        """GET /settings 응답에서 env 값이 마스킹되는가."""
        response = client.get("/settings")
        assert response.status_code == 200
        text = response.text
        # 실제 키는 있어야 하지만 값은 없어야 함
        assert "AI_API_KEY" in text
        assert "sk-test-secret" not in text

    def test_env_save_unmasks_masked_placeholder(self, client, test_env_file):
        """저장할 때 마스킹 자리표시(****)는 기존 값으로 복구된다."""
        # 원래 파일 읽기
        original = test_env_file.read_text()
        assert "sk-test-secret" in original

        # 마스킹된 값으로 저장
        response = client.post(
            "/settings/env/save",
            data={"content": "AI_API_KEY=********\nEMPTY=\n"}
        )
        assert response.status_code == 200

        # 원래 값이 복구되었는가?
        saved = test_env_file.read_text()
        assert "sk-test-secret" in saved


class TestCompleteSettingsFlow:
    """설정·데이터 흐름을 통합으로 검증한다."""

    def test_multiple_data_columns_preserved_across_block_time_changes(self, client):
        """
        여러 데이터 컬럼을 입력하고, 2회 블록 시간 변경 후에도 모두 보존되는가.
        """
        date_str = "2026-10-01"

        # 1. 구분 생성
        cat_resp = client.post(
            "/settings/category/add",
            data={"name": "업무", "tone": "blue"}
        )
        cat_id = cat_resp.json()["id"]

        # 2. 초기 골격
        with get_conn() as conn:
            ensure_day_skeleton(conn, date_str)

            # 슬롯 데이터 입력
            slot = conn.execute(
                "SELECT id FROM slots WHERE date = ? ORDER BY slot_index LIMIT 1",
                (date_str,)
            ).fetchone()
            slot_id = slot["id"]
            conn.execute(
                "UPDATE slots SET do_text = ?, did_text = ?, done = 1, is_routine = 0, category_id = ? WHERE id = ?",
                ("계획", "완료", cat_id, slot_id)
            )

            # 블록 데이터 입력
            block = conn.execute(
                "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
                (date_str,)
            ).fetchone()
            block_id = block["id"]
            conn.execute(
                "UPDATE blocks SET plan_text = ?, see_text = ?, name = ?, location = ?, category_id = ? WHERE id = ?",
                ("블록계획", "블록검토", "블록이름", "장소", cat_id, block_id)
            )

        # 3. 첫 번째 블록 시간 변경
        client.post(
            "/settings/blocktimes",
            data={
                "scope": "",
                "start_0": "08:00", "end_0": "09:00",
                "start_1": "09:00", "end_1": "10:00",
                "start_2": "10:00", "end_2": "11:00",
                "start_3": "11:00", "end_3": "12:00",
                "start_4": "12:00", "end_4": "13:00",
                "start_5": "13:00", "end_5": "14:00",
                "start_6": "14:00", "end_6": "15:00",
                "start_7": "15:00", "end_7": "16:00",
            }
        )
        client.get(f"/day/{date_str}")

        # 4. 두 번째 블록 시간 변경
        client.post(
            "/settings/blocktimes",
            data={
                "scope": "",
                "start_0": "07:00", "end_0": "08:00",
                "start_1": "08:00", "end_1": "09:00",
                "start_2": "09:00", "end_2": "10:00",
                "start_3": "10:00", "end_3": "11:00",
                "start_4": "11:00", "end_4": "12:00",
                "start_5": "12:00", "end_5": "13:00",
                "start_6": "13:00", "end_6": "14:00",
                "start_7": "14:00", "end_7": "15:00",
            }
        )
        client.get(f"/day/{date_str}")

        # 5. 모든 데이터 보존 확인
        with get_conn() as conn:
            slot_result = conn.execute(
                "SELECT do_text, did_text, done, category_id FROM slots WHERE id = ?",
                (slot_id,)
            ).fetchone()
            assert slot_result["do_text"] == "계획"
            assert slot_result["did_text"] == "완료"
            assert slot_result["done"] == 1
            assert slot_result["category_id"] == cat_id

            block_result = conn.execute(
                "SELECT plan_text, see_text, name, location, category_id FROM blocks WHERE id = ?",
                (block_id,)
            ).fetchone()
            assert block_result["plan_text"] == "블록계획"
            assert block_result["see_text"] == "블록검토"
            assert block_result["name"] == "블록이름"
            assert block_result["location"] == "장소"
            assert block_result["category_id"] == cat_id
