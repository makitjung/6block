# 하루 기록 흐름 end-to-end 통합 테스트
import json
from datetime import date, datetime, timedelta

from app.common import today_str
from app.db import get_conn, init_db


def test_p3_day_flow_basic_scenario(client, fresh_db):
    """GET /today로 화면 진입 후 블록·슬롯 기본 구조 생성 확인"""
    resp = client.get("/today")
    assert resp.status_code == 200
    assert "today.html" in resp.text or "오늘" in resp.text

    # DB에 오늘 날짜의 블록·슬롯이 자동 생성되었는지 확인
    d = today_str()
    with get_conn() as conn:
        blocks = conn.execute(
            "SELECT id, date, block_label, is_core FROM blocks WHERE date = ?",
            (d,),
        ).fetchall()
        slots = conn.execute(
            "SELECT id, block_id, date, slot_index FROM slots WHERE date = ?",
            (d,),
        ).fetchall()
        meta = conn.execute(
            "SELECT date FROM daily_meta WHERE date = ?", (d,)
        ).fetchone()

    # 기본 구조: 6개 코어 블록 + 각 블록당 슬롯들
    assert len(blocks) > 0, "블록이 생성되지 않았다"
    assert len(slots) > 0, "슬롯이 생성되지 않았다"
    # meta는 아직 create되지 않을 수도 있음 (첫 /save/day까지)


def test_p3_day_save_field_single_slot_do(client, fresh_db):
    """POST /save/field로 슬롯 DO 칸 하나만 즉시저장"""
    d = today_str()
    # 먼저 /today 호출로 스켈레톤 생성
    client.get("/today")

    with get_conn() as conn:
        slots = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (d,)
        ).fetchall()
        assert len(slots) > 0
        slot_id = slots[0]["id"]

    # POST /save/field 로 이 슬롯의 do_text 저장
    resp = client.post(
        "/save/field",
        data={
            "entity": "slot",
            "field": "do_text",
            "id": str(slot_id),
            "value": "테스트 할 일",
        },
    )
    assert resp.status_code == 200 or resp.status_code == 303

    # DB에서 실제로 저장되었는지 확인
    with get_conn() as conn:
        slot = conn.execute(
            "SELECT do_text, is_routine FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert slot["do_text"] == "테스트 할 일", f"저장 값이 다르다: {slot['do_text']}"
        # 사람이 수정했으므로 is_routine은 0으로 변해야 함
        assert slot["is_routine"] == 0, f"is_routine이 풀리지 않았다: {slot['is_routine']}"


def test_p3_day_save_field_block_plan(client, fresh_db):
    """POST /save/field로 블록 PLAN 칸 즉시저장"""
    d = today_str()
    client.get("/today")

    with get_conn() as conn:
        blocks = conn.execute(
            "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1", (d,)
        ).fetchall()
        assert len(blocks) > 0
        block_id = blocks[0]["id"]

    # POST /save/field 로 블록 plan_text 저장
    resp = client.post(
        "/save/field",
        data={
            "entity": "block",
            "field": "plan_text",
            "id": str(block_id),
            "value": "오늘 블록 계획 테스트",
        },
    )
    assert resp.status_code == 200 or resp.status_code == 303

    # DB에서 실제로 저장되었는지 확인
    with get_conn() as conn:
        block = conn.execute(
            "SELECT plan_text FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        assert (
            block["plan_text"] == "오늘 블록 계획 테스트"
        ), f"저장 값이 다르다: {block['plan_text']}"


def test_p3_day_save_field_category_inheritance(client, fresh_db):
    """슬롯 구분(category_id) 저장 후 상속 규칙 검증 - NULL이면 블록 구분을 상속해야 함"""
    d = today_str()
    client.get("/today")

    with get_conn() as conn:
        # 블록에 구분 할당
        blocks = conn.execute(
            "SELECT id FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1", (d,)
        ).fetchall()
        block_id = blocks[0]["id"]

        # 이 블록에 구분 1 할당
        conn.execute(
            "UPDATE blocks SET category_id = 1 WHERE id = ?", (block_id,)
        )

        # 이 블록의 슬롯들 조회
        slots = conn.execute(
            "SELECT id FROM slots WHERE block_id = ? LIMIT 1", (block_id,)
        ).fetchall()
        slot_id = slots[0]["id"]

    # 슬롯의 category_id는 아직 NULL 상태 (상속 준비 상태)
    with get_conn() as conn:
        slot = conn.execute(
            "SELECT category_id FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert (
            slot["category_id"] is None
        ), "슬롯 category_id가 미리 설정되지 않아야 함"

    # 수집함이나 템플릿이 아닌 사용자가 직접 입력한 값으로 조회할 때
    # COALESCE(slots.category_id, blocks.category_id) 를 써야 1이 나와야 함
    with get_conn() as conn:
        result = conn.execute(
            """
            SELECT COALESCE(s.category_id, b.category_id) as cat_id
            FROM slots s
            JOIN blocks b ON s.block_id = b.id
            WHERE s.id = ?
            """,
            (slot_id,),
        ).fetchone()
        assert result["cat_id"] == 1, f"구분 상속이 작동하지 않았다: {result['cat_id']}"


def test_p3_day_save_day_goals_and_plans(client, fresh_db):
    """POST /save/day로 오늘 목표/달성/감사 3칸 저장"""
    d = today_str()
    client.get("/today")

    # POST /save/day 폼 데이터 준비
    form_data = {
        "goal1": "오늘 목표 1번",
        "goal2": "오늘 목표 2번",
        "goal3": "오늘 목표 3번",
        "dplan1": "오늘 달성 1번",
        "dplan2": "오늘 달성 2번",
        "dplan3": "오늘 달성 3번",
        "grat1": "감사 1번",
        "grat2": "감사 2번",
        "grat3": "감사 3번",
        "memo": "오늘 메모",
    }

    resp = client.post(f"/save/day/{d}", data=form_data)
    # 리다이렉트 또는 200 예상
    assert resp.status_code in (200, 303)

    # DB에서 daily_meta 확인
    with get_conn() as conn:
        meta = conn.execute(
            "SELECT today_goal, daily_plan, gratitude, memo FROM daily_meta WHERE date = ?",
            (d,),
        ).fetchone()

    assert meta is not None, "daily_meta가 생성되지 않았다"
    # _join3로 결합된 값들을 저장함 (줄바꿈으로 연결)
    assert "오늘 목표 1번" in meta["today_goal"], "목표가 저장되지 않았다"
    assert "오늘 달성 1번" in meta["daily_plan"], "달성이 저장되지 않았다"
    assert "감사 1번" in meta["gratitude"], "감사가 저장되지 않았다"
    assert meta["memo"] == "오늘 메모", "메모가 저장되지 않았다"


def test_p3_day_save_day_slots_do_and_did(client, fresh_db):
    """POST /save/day로 슬롯 DO/한 일 칸 저장"""
    d = today_str()
    client.get("/today")

    with get_conn() as conn:
        slots = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 2", (d,)
        ).fetchall()
        assert len(slots) >= 2
        slot1_id = slots[0]["id"]
        slot2_id = slots[1]["id"]

    # 폼 데이터에 슬롯 do_/did_ 필드 추가
    form_data = {
        f"do_{slot1_id}": "계획한 일 1",
        f"did_{slot1_id}": "실제로 한 일 1",
        f"do_{slot2_id}": "계획한 일 2",
        f"did_{slot2_id}": "실제로 한 일 2",
    }

    resp = client.post(f"/save/day/{d}", data=form_data)
    assert resp.status_code in (200, 303)

    # DB에서 슬롯 값 확인
    with get_conn() as conn:
        slot1 = conn.execute(
            "SELECT do_text, did_text FROM slots WHERE id = ?", (slot1_id,)
        ).fetchone()
        slot2 = conn.execute(
            "SELECT do_text, did_text FROM slots WHERE id = ?", (slot2_id,)
        ).fetchone()

    assert slot1["do_text"] == "계획한 일 1", f"슬롯1 do가 다르다: {slot1['do_text']}"
    assert slot1["did_text"] == "실제로 한 일 1", f"슬롯1 did가 다르다: {slot1['did_text']}"
    assert slot2["do_text"] == "계획한 일 2", f"슬롯2 do가 다르다: {slot2['do_text']}"
    assert slot2["did_text"] == "실제로 한 일 2", f"슬롯2 did가 다르다: {slot2['did_text']}"


def test_p3_day_slot_done_checkbox(client, fresh_db):
    """POST /slot/done/{slot_id}로 슬롯 완료 체크"""
    d = today_str()
    client.get("/today")

    with get_conn() as conn:
        slots = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (d,)
        ).fetchall()
        slot_id = slots[0]["id"]

    # 초기 상태: done=0
    with get_conn() as conn:
        slot = conn.execute(
            "SELECT done FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert slot["done"] == 0, "초기 done 값이 0이 아니다"

    # POST /slot/done 체크
    resp = client.post(f"/slot/done/{slot_id}", data={"done": "1"})
    assert resp.status_code == 200
    resp_data = resp.json()
    assert resp_data["ok"] is True, "응답이 실패했다"
    assert resp_data["done"] == 1, "응답의 done 값이 1이 아니다"

    # DB에서 done=1 확인
    with get_conn() as conn:
        slot = conn.execute(
            "SELECT done FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert slot["done"] == 1, f"done 값이 저장되지 않았다: {slot['done']}"

    # 다시 체크 해제
    resp = client.post(f"/slot/done/{slot_id}", data={"done": "0"})
    assert resp.status_code == 200
    resp_data = resp.json()
    assert resp_data["done"] == 0, "체크 해제가 안 됐다"

    with get_conn() as conn:
        slot = conn.execute(
            "SELECT done FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert slot["done"] == 0, "done 체크 해제가 DB에 반영되지 않았다"


def test_p3_day_rollover_block_plan_and_slots(client, fresh_db):
    """POST /block/rollover로 오늘 블록의 계획과 슬롯을 내일로 복사"""
    today = today_str()
    tomorrow = (
        (datetime.strptime(today, "%Y-%m-%d").date() + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
    )

    # 오늘 화면 진입
    client.get("/today")

    with get_conn() as conn:
        # 오늘 기준 블록 선택
        block = conn.execute(
            "SELECT id, block_label FROM blocks WHERE date = ? AND is_core = 1 LIMIT 1",
            (today,),
        ).fetchone()
        block_id = block["id"]
        block_label = block["block_label"]

        # 오늘 블록에 계획 문구 입력
        conn.execute(
            "UPDATE blocks SET plan_text = ? WHERE id = ?",
            ("이 계획을 내일로 넘기겠다", block_id),
        )

        # 오늘 이 블록의 슬롯 중 하나에 DO 입력 (고정 할일 아닌 것)
        slot = conn.execute(
            "SELECT id, start_time FROM slots WHERE block_id = ? AND is_routine = 0 LIMIT 1",
            (block_id,),
        ).fetchone()
        slot_id = slot["id"]
        slot_time = slot["start_time"]

        conn.execute(
            "UPDATE slots SET do_text = ? WHERE id = ?", ("이 슬롯을 내일로 넘기겠다", slot_id)
        )

    # POST /block/rollover 호출
    resp = client.post(
        "/block/rollover",
        data={
            "block_id": str(block_id),
            # 폼에서 온 최신 값 (버튼 누를 때 blur 자동저장이 함께 출발했을 경우 대비)
            "plan": "이 계획을 내일로 넘기겠다",
            f"do_{slot_id}": "이 슬롯을 내일로 넘기겠다",
        },
    )
    assert resp.status_code in (200, 303, 400), f"응답 코드 이상: {resp.status_code}"

    # DB 검증: 오늘 데이터는 유지되어야 함 (복사, 삭제 아님)
    with get_conn() as conn:
        today_block = conn.execute(
            "SELECT plan_text FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        assert (
            today_block["plan_text"] == "이 계획을 내일로 넘기겠다"
        ), "오늘 블록 계획이 지워졌다"

        today_slot = conn.execute(
            "SELECT do_text FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert (
            today_slot["do_text"] == "이 슬롯을 내일로 넘기겠다"
        ), "오늘 슬롯 DO가 지워졌다"

    # DB 검증: 내일 블록에 내용이 복사되었는지 확인
    with get_conn() as conn:
        # 내일에 같은 블록 라벨이 있는지 확인
        tomorrow_block = conn.execute(
            "SELECT id, plan_text FROM blocks WHERE date = ? AND block_label = ?",
            (tomorrow, block_label),
        ).fetchone()
        assert tomorrow_block is not None, "내일 블록이 없다"
        # 계획이 추가되었는지 확인 (기존 계획 있으면 줄바꿈으로 연결)
        assert (
            "이 계획을 내일로 넘기겠다" in tomorrow_block["plan_text"]
        ), f"내일 블록에 계획이 복사되지 않았다: {tomorrow_block['plan_text']}"

        # 내일 같은 블록의 같은 시각 슬롯 찾기
        tomorrow_slot = conn.execute(
            "SELECT do_text FROM slots WHERE block_id = ? AND start_time = ?",
            (tomorrow_block["id"], slot_time),
        ).fetchone()
        assert tomorrow_slot is not None, "내일 같은 시각 슬롯이 없다"
        assert (
            "이 슬롯을 내일로 넘기겠다" in tomorrow_slot["do_text"]
        ), f"내일 슬롯에 DO가 복사되지 않았다: {tomorrow_slot['do_text']}"


def test_p3_day_api_day_polling(client, fresh_db):
    """GET /api/day/{date_str}로 외부 일정·할일 폴링"""
    d = today_str()
    client.get("/today")

    # GET /api/day/{date_str} 호출
    resp = client.get(f"/api/day/{d}")
    assert resp.status_code == 200

    data = resp.json()
    assert "events" in data, "events 필드가 없다"
    assert "tasks" in data, "tasks 필드가 없다"
    assert isinstance(data["events"], list), "events가 리스트가 아니다"
    assert isinstance(data["tasks"], list), "tasks가 리스트가 아니다"
    # 스텁 환경에서는 빈 리스트일 것 (conftest에서 gcal·things 비활성화)


def test_p3_day_save_field_and_save_day_consistency(client, fresh_db):
    """
    /save/field와 /save/day가 같은 칸을 서로 다르게 저장하지 않는지 검증.
    같은 슬롯 DO 칸에 대해 두 경로로 저장했을 때 최종 값이 일치해야 함.
    """
    d = today_str()
    client.get("/today")

    with get_conn() as conn:
        slot = conn.execute(
            "SELECT id FROM slots WHERE date = ? LIMIT 1", (d,)
        ).fetchone()
        slot_id = slot["id"]

    # 1단계: /save/field 로 "값1" 저장
    client.post(
        "/save/field",
        data={
            "entity": "slot",
            "field": "do_text",
            "id": str(slot_id),
            "value": "값1",
        },
    )

    with get_conn() as conn:
        slot = conn.execute(
            "SELECT do_text FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert slot["do_text"] == "값1", "save_field 저장 실패"

    # 2단계: /save/day 로 "값2" 저장 (같은 칸을 다시 저장)
    resp = client.post(f"/save/day/{d}", data={f"do_{slot_id}": "값2"})
    assert resp.status_code in (200, 303)

    with get_conn() as conn:
        slot = conn.execute(
            "SELECT do_text FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert slot["do_text"] == "값2", "save_day 저장이 덮어쓰지 못했다"

    # 3단계: 다시 /save/field 로 "값3" 저장
    client.post(
        "/save/field",
        data={
            "entity": "slot",
            "field": "do_text",
            "id": str(slot_id),
            "value": "값3",
        },
    )

    with get_conn() as conn:
        slot = conn.execute(
            "SELECT do_text FROM slots WHERE id = ?", (slot_id,)
        ).fetchone()
        assert slot["do_text"] == "값3", "다시 save_field로 덮어쓰지 못했다"


def test_p3_day_meta_merge3_dplan_path(client, fresh_db):
    """(수정 후) POST /save/field 로 메타 3칸 필드를 최소 폼으로 보내도 저장된다.

    예전에는 /save/field 가 {entity, field, id, value} 만 받는데 _merge3 는 form["dplan1"]
    을 찾아, 200 을 돌려주면서 값을 조용히 버렸다. 지금은 그룹 키가 없을 때만 그 칸 하나를
    폼에 채워 넣어 반영한다.

    참고로 화면(app.js bindAutoSave)은 예나 지금이나 3칸을 함께 보내므로 사용자 경로에는
    영향이 없었다. 이 수정은 Record 앱·스크립트 같은 다른 클라이언트를 위한 것이다.
    """
    d = today_str()
    client.get("/today")

    # /save/field로 메타 dplan 저장 시도
    resp = client.post(
        "/save/field",
        data={
            "entity": "meta",
            "field": "dplan1",
            "id": d,
            "value": "save_field 달성 1",
        },
    )
    assert resp.status_code in (200, 303), f"save_field 응답 실패: {resp.status_code}"

    with get_conn() as conn:
        meta = conn.execute(
            "SELECT daily_plan FROM daily_meta WHERE date = ?", (d,)
        ).fetchone()

    assert meta is not None, "daily_meta가 생성되지 않았다"
    assert meta["daily_plan"].split("\n")[0] == "save_field 달성 1", meta["daily_plan"]

    # /save/day로 올바르게 저장
    resp = client.post(
        f"/save/day/{d}",
        data={
            "dplan1": "save_day 달성 1",
            "dplan2": "save_day 달성 2",
            "dplan3": "save_day 달성 3",
        },
    )
    assert resp.status_code in (200, 303)

    # DB에서 확인 - /save/day는 올바르게 작동
    with get_conn() as conn:
        meta = conn.execute(
            "SELECT daily_plan FROM daily_meta WHERE date = ?", (d,)
        ).fetchone()

    assert (
        "save_day 달성 1" in meta["daily_plan"]
    ), f"save_day 달성이 저장 안 됐다: {meta['daily_plan']}"
    assert (
        "save_day 달성 2" in meta["daily_plan"]
    ), f"dplan2가 저장 안 됐다: {meta['daily_plan']}"
    assert (
        "save_day 달성 3" in meta["daily_plan"]
    ), f"dplan3이 저장 안 됐다: {meta['daily_plan']}"
