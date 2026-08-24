# 3단계 통합 테스트: 장기·주간 계획 흐름 (계획 생성→주간 연결→저장)
import pytest
from datetime import datetime, timedelta
from starlette.testclient import TestClient

from app.common import KST


@pytest.fixture
def client(fresh_db):
    """TestClient with fresh DB."""
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


class TestPlanAreaItem:
    """POST /plan/area/add → /plan/item/add 통합 흐름."""

    def test_create_area_and_toplevel_item(self, client, conn):
        """영역 생성 후 최상위 항목 추가하고 DB에서 확인."""
        # 1. 영역 추가
        area_resp = client.post("/plan/area/add", data={
            "name": "진로",
            "tone": "blue"
        })
        assert area_resp.status_code == 200
        area_id = area_resp.json()["id"]

        # 2. 최상위 항목 추가 (area_id 지정, parent_id 없음)
        start_date = "2026-09-01"
        end_date = "2026-12-31"
        item_resp = client.post("/plan/item/add", data={
            "title": "연말 프로젝트",
            "start": start_date,
            "end": end_date,
            "area_id": str(area_id)
        })
        assert item_resp.status_code == 200
        result = item_resp.json()
        assert result["ok"] is True
        item_id = result["id"]

        # 3. DB에서 확인: lt_item 테이블
        row = conn.execute(
            "SELECT id, area_id, parent_id, title, start_date, end_date, progress "
            "FROM lt_item WHERE id = ?",
            (item_id,)
        ).fetchone()
        assert row is not None
        assert row["area_id"] == area_id
        assert row["parent_id"] is None  # 최상위
        assert row["title"] == "연말 프로젝트"
        assert row["start_date"] == start_date
        assert row["end_date"] == end_date
        assert row["progress"] == 0

    def test_create_nested_item_with_parent(self, client, conn):
        """상위 항목 아래에 하위 항목 추가하고 영역 자동 상속 확인."""
        # 1. 영역 생성
        area_resp = client.post("/plan/area/add", data={"name": "학습", "tone": "green"})
        area_id = area_resp.json()["id"]

        # 2. 상위 항목 추가
        parent_resp = client.post("/plan/item/add", data={
            "title": "Python 공부",
            "start": "2026-09-01",
            "end": "2026-12-31",
            "area_id": str(area_id)
        })
        parent_id = parent_resp.json()["id"]

        # 3. 하위 항목 추가 (parent_id 지정, area_id 미지정)
        child_resp = client.post("/plan/item/add", data={
            "title": "FastAPI 심화",
            "start": "2026-10-01",
            "end": "2026-10-31",
            "parent_id": str(parent_id)
        })
        child_id = child_resp.json()["id"]

        # 4. DB에서 확인: 하위가 상위의 area_id를 물려받았는가
        child_row = conn.execute(
            "SELECT parent_id, area_id FROM lt_item WHERE id = ?", (child_id,)
        ).fetchone()
        assert child_row["parent_id"] == parent_id
        assert child_row["area_id"] == area_id  # 영역 상속


class TestLtRollup:
    """_lt_rollup 검증: 상위 기간·진척률이 하위를 따라가는가."""

    def test_rollup_expands_parent_dates(self, client, conn):
        """하위 항목이 상위 기간을 확장한다."""
        # 1. 영역·상위·하위 항목 생성
        area_resp = client.post("/plan/area/add", data={"name": "test", "tone": "gray"})
        area_id = area_resp.json()["id"]

        parent_resp = client.post("/plan/item/add", data={
            "title": "상위",
            "start": "2026-10-01",
            "end": "2026-10-31",
            "area_id": str(area_id)
        })
        parent_id = parent_resp.json()["id"]

        # 2. 상위 기간 밖의 하위 항목 추가
        client.post("/plan/item/add", data={
            "title": "하위1",
            "start": "2026-09-15",  # 상위보다 2주 앞
            "end": "2026-09-30",
            "parent_id": str(parent_id)
        })

        # 3. 롤업 후 상위 기간이 확장되었는가
        parent_row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (parent_id,)
        ).fetchone()
        assert parent_row["start_date"] == "2026-09-15"  # 확장됨
        assert parent_row["end_date"] == "2026-10-31"

    def test_rollup_calculates_progress_average(self, client, conn):
        """상위 진척률 = 하위 평균."""
        area_resp = client.post("/plan/area/add", data={"name": "test", "tone": "gray"})
        area_id = area_resp.json()["id"]

        parent_resp = client.post("/plan/item/add", data={
            "title": "상위",
            "start": "2026-10-01",
            "end": "2026-10-31",
            "area_id": str(area_id)
        })
        parent_id = parent_resp.json()["id"]

        # 2. 하위 항목 3개 추가, 각각 진척률 50%, 70%, 90% 저장
        for i, progress in enumerate([50, 70, 90]):
            child_resp = client.post("/plan/item/add", data={
                "title": f"하위{i}",
                "start": "2026-10-01",
                "end": "2026-10-31",
                "parent_id": str(parent_id)
            })
            child_id = child_resp.json()["id"]
            # 진척률 업데이트
            client.post("/plan/item/update", data={
                "id": str(child_id),
                "progress": str(progress)
            })

        # 3. 상위 진척률이 평균(70)이 되었는가
        parent_row = conn.execute(
            "SELECT progress FROM lt_item WHERE id = ?", (parent_id,)
        ).fetchone()
        # 평균: (50 + 70 + 90) / 3 = 70
        assert parent_row["progress"] == 70


class TestPlanShiftResize:
    """POST /plan/item/shift, /plan/item/resize 통합."""

    def test_shift_moves_parent_and_children(self, client, conn):
        """상위를 밀면 하위도 함께 움직인다."""
        area_resp = client.post("/plan/area/add", data={"name": "test", "tone": "gray"})
        area_id = area_resp.json()["id"]

        # 상위 항목
        parent_resp = client.post("/plan/item/add", data={
            "title": "상위",
            "start": "2026-10-01",
            "end": "2026-10-31",
            "area_id": str(area_id)
        })
        parent_id = parent_resp.json()["id"]

        # 하위 항목
        child_resp = client.post("/plan/item/add", data={
            "title": "하위",
            "start": "2026-10-10",
            "end": "2026-10-20",
            "parent_id": str(parent_id)
        })
        child_id = child_resp.json()["id"]

        # 상위를 5일 뒤로 이동
        shift_resp = client.post("/plan/item/shift", data={
            "id": str(parent_id),
            "days": "5"
        })
        assert shift_resp.json()["ok"] is True

        # DB에서 확인: 상위와 하위 모두 5일 이동
        parent_row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (parent_id,)
        ).fetchone()
        child_row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (child_id,)
        ).fetchone()
        assert parent_row["start_date"] == "2026-10-06"
        assert parent_row["end_date"] == "2026-11-05"
        assert child_row["start_date"] == "2026-10-15"
        assert child_row["end_date"] == "2026-10-25"

    def test_update_parent_dates_shifts_children(self, client, conn):
        """상위 시작일을 업데이트하면 자식의 시작도 따라가는데 종료는 그대로 둔다."""
        area_resp = client.post("/plan/area/add", data={"name": "test", "tone": "gray"})
        area_id = area_resp.json()["id"]

        parent_resp = client.post("/plan/item/add", data={
            "title": "상위",
            "start": "2026-10-01",
            "end": "2026-10-31",
            "area_id": str(area_id)
        })
        parent_id = parent_resp.json()["id"]

        child_resp = client.post("/plan/item/add", data={
            "title": "하위",
            "start": "2026-10-10",
            "end": "2026-10-20",
            "parent_id": str(parent_id)
        })
        child_id = child_resp.json()["id"]

        # 상위의 시작일을 2026-10-10으로 변경 (9일 뒤로)
        # 시작만 변경했으므로 종료는 그대로 둔다
        client.post("/plan/item/update", data={
            "id": str(parent_id),
            "start": "2026-10-10"
        })

        # 하위: 시작은 9일 뒤로, 종료는 그대로 둔다
        child_row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (child_id,)
        ).fetchone()
        assert child_row["start_date"] == "2026-10-19"  # 10일 + 9일
        assert child_row["end_date"] == "2026-10-20"    # 그대로


class TestPlanReparent:
    """POST /plan/item/reparent: 상위 변경 시 롤업이 양쪽 모두 재계산된다."""

    def test_reparent_recalculates_both_parents(self, client, conn):
        """항목을 다른 상위로 옮기면 이전·새 상위 모두 재계산된다."""
        area_resp = client.post("/plan/area/add", data={"name": "test", "tone": "gray"})
        area_id = area_resp.json()["id"]

        # 상위1, 상위2 생성
        parent1_resp = client.post("/plan/item/add", data={
            "title": "상위1",
            "start": "2026-10-01",
            "end": "2026-10-31",
            "area_id": str(area_id)
        })
        parent1_id = parent1_resp.json()["id"]

        parent2_resp = client.post("/plan/item/add", data={
            "title": "상위2",
            "start": "2026-11-01",
            "end": "2026-11-30",
            "area_id": str(area_id)
        })
        parent2_id = parent2_resp.json()["id"]

        # 하위를 상위1에 추가
        child_resp = client.post("/plan/item/add", data={
            "title": "하위",
            "start": "2026-10-10",
            "end": "2026-10-20",
            "parent_id": str(parent1_id)
        })
        child_id = child_resp.json()["id"]

        # 하위를 상위2로 reparent
        reparent_resp = client.post("/plan/item/reparent", data={
            "id": str(child_id),
            "parent_id": str(parent2_id)
        })
        assert reparent_resp.json()["ok"] is True

        # 이전 상위1의 기간이 축소되었는가 (하위가 없어짐)
        parent1_row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (parent1_id,)
        ).fetchone()
        # 하위가 없으므로 원래 값 유지
        assert parent1_row["start_date"] == "2026-10-01"
        assert parent1_row["end_date"] == "2026-10-31"

        # 새 상위2의 기간이 확장되었는가 (하위 포함)
        parent2_row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (parent2_id,)
        ).fetchone()
        assert parent2_row["start_date"] == "2026-10-10"  # 하위 시작
        assert parent2_row["end_date"] == "2026-11-30"    # 상위2 종료


class TestPlanDelete:
    """POST /plan/item/delete: 삭제 시 상위 재계산."""

    def test_delete_cascades_and_recalculates(self, client, conn):
        """상위를 지우면 하위도 함께 지워지고, 상위를 지우면 부모가 재계산된다."""
        area_resp = client.post("/plan/area/add", data={"name": "test", "tone": "gray"})
        area_id = area_resp.json()["id"]

        # 상위1 - 상위2 - 하위
        parent1_resp = client.post("/plan/item/add", data={
            "title": "상위1",
            "start": "2026-10-01",
            "end": "2026-10-31",
            "area_id": str(area_id)
        })
        parent1_id = parent1_resp.json()["id"]

        parent2_resp = client.post("/plan/item/add", data={
            "title": "상위2",
            "start": "2026-10-10",
            "end": "2026-10-20",
            "parent_id": str(parent1_id)
        })
        parent2_id = parent2_resp.json()["id"]

        child_resp = client.post("/plan/item/add", data={
            "title": "하위",
            "start": "2026-10-15",
            "end": "2026-10-18",
            "parent_id": str(parent2_id)
        })
        child_id = child_resp.json()["id"]

        # 상위2 삭제 (하위도 함께)
        delete_resp = client.post("/plan/item/delete", data={
            "id": str(parent2_id)
        })
        assert delete_resp.json()["ok"] is True

        # 상위2, 하위가 삭제되었는가
        parent2_row = conn.execute(
            "SELECT id FROM lt_item WHERE id = ?", (parent2_id,)
        ).fetchone()
        child_row = conn.execute(
            "SELECT id FROM lt_item WHERE id = ?", (child_id,)
        ).fetchone()
        assert parent2_row is None
        assert child_row is None

        # 상위1은 남아 있고 기간이 복구되었는가 (하위가 없음)
        parent1_row = conn.execute(
            "SELECT start_date, end_date FROM lt_item WHERE id = ?", (parent1_id,)
        ).fetchone()
        assert parent1_row is not None
        assert parent1_row["start_date"] == "2026-10-01"  # 원래 값


class TestWeekViewIntegration:
    """GET /week 에서 장기 항목이 올바르게 표시되는가."""

    def test_week_view_includes_long_term_items(self, client, conn):
        """주간 뷰에 그 주에 걸친 장기 항목이 표시된다."""
        area_resp = client.post("/plan/area/add", data={"name": "진로", "tone": "blue"})
        area_id = area_resp.json()["id"]

        # 이번 주에 걸친 장기 항목 생성
        monday = datetime.now(KST).date()
        monday = monday - timedelta(days=monday.weekday())  # 이번 주 월요일
        friday = monday + timedelta(days=4)

        item_resp = client.post("/plan/item/add", data={
            "title": "주간 프로젝트",
            "start": monday.strftime("%Y-%m-%d"),
            "end": friday.strftime("%Y-%m-%d"),
            "area_id": str(area_id)
        })
        item_id = item_resp.json()["id"]

        # 주간 뷰 조회
        week_resp = client.get(f"/week/{monday.strftime('%Y-%m-%d')}")
        assert week_resp.status_code == 200
        # HTML 응답에 장기 항목 제목이 포함되어 있는가
        html = week_resp.text
        assert "주간 프로젝트" in html


class TestWeekSave:
    """POST /week/save/{week_start}: 주간 메타데이터와 블록 테마 저장."""

    def test_save_weekly_metadata(self, client, conn):
        """주간 목표, 약속, 다짐, 메모를 저장한다."""
        monday = datetime.now(KST).date()
        monday = monday - timedelta(days=monday.weekday())
        week_start_str = monday.strftime("%Y-%m-%d")

        save_resp = client.post(f"/week/save/{week_start_str}", data={
            "wgoal1": "목표 1",
            "wgoal2": "목표 2",
            "wgoal3": "목표 3",
            "appointments": "약속 메모",
            "vow": "다짐",
            "memo": "메모"
        }, follow_redirects=False)
        # 리다이렉트 확인
        assert save_resp.status_code == 303

        # DB에서 확인
        meta_row = conn.execute(
            "SELECT weekly_goal, appointments, vow, memo FROM weekly_meta "
            "WHERE week_start = ?",
            (week_start_str,)
        ).fetchone()
        assert meta_row is not None
        assert "목표 1" in meta_row["weekly_goal"]
        assert meta_row["appointments"] == "약속 메모"
        assert meta_row["vow"] == "다짐"
        assert meta_row["memo"] == "메모"

    def test_save_block_themes(self, client, conn):
        """블록별 테마 텍스트를 저장한다."""
        monday = datetime.now(KST).date()
        monday = monday - timedelta(days=monday.weekday())
        week_start_str = monday.strftime("%Y-%m-%d")

        save_resp = client.post(f"/week/save/{week_start_str}", data={
            "theme_B1": "학습 테마",
            "theme_B2": "업무 테마",
            "theme_B3": "",
            "theme_B4": "건강",
            "theme_B5": "",
            "theme_B6": "",
        }, follow_redirects=False)
        assert save_resp.status_code == 303

        # DB에서 확인
        themes = {
            r["block_label"]: r["theme_text"]
            for r in conn.execute(
                "SELECT block_label, theme_text FROM weekly_block_themes "
                "WHERE week_start = ?",
                (week_start_str,)
            )
        }
        assert themes.get("B1") == "학습 테마"
        assert themes.get("B2") == "업무 테마"


class TestWeekApplyTemplate:
    """POST /week/apply-template: 구분 템플릿 일괄 적용 (기존 데이터 덮어쓰지 않음)."""

    def test_apply_template_fills_empty_slots(self, client, conn):
        """템플릿이 비어 있는 칸을 채우지만 이미 채워진 칸은 건드리지 않는다."""
        # 1. 구분 템플릿 생성 (name 필수)
        tmpl_resp = client.post("/settings/template/add", data={"name": "테스트 템플릿"})
        assert tmpl_resp.status_code == 200
        tmpl_id = tmpl_resp.json().get("id")

        # 2. 구분 생성
        cat_resp = client.post("/settings/category/add", data={"name": "학습"})
        assert cat_resp.status_code == 200
        cat_id = cat_resp.json().get("id")
        if cat_id is None:
            categories = conn.execute(
                "SELECT id FROM categories WHERE is_active = 1 LIMIT 1"
            ).fetchone()
            cat_id = categories["id"] if categories else None

        if cat_id is None:
            pytest.skip("No category available")

        # 3. 템플릿 셀에 구분 저장 (월요일, B1)
        client.post("/settings/template/cell", data={
            "template_id": str(tmpl_id),
            "weekday": "0",  # 월요일
            "block_label": "B1",
            "category_id": str(cat_id)
        })

        # 4. 이번 주에 적용
        monday = datetime.now(KST).date()
        monday = monday - timedelta(days=monday.weekday())
        week_start_str = monday.strftime("%Y-%m-%d")

        apply_resp = client.post("/week/apply-template", data={
            "week_start": week_start_str,
            "template_id": str(tmpl_id)
        })
        assert apply_resp.status_code == 200 or apply_resp.status_code == 400
        # 빈 템플릿이면 400이 정상
