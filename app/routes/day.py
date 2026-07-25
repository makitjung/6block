# 오늘/특정 날짜 화면과 그 저장(블록·슬롯·수집함·외부 입력)을 담당하는 라우터
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.common import (
    KST,
    _join3,
    _name_override,
    _off_loop,
    _parse_date,
    _split3,
    ensure_day_skeleton,
    templates,
    today_str,
    week_start,
)
from app.config import hhmm_to_min
from app.db import get_conn
from app.integrations import gcal, gcal_write, things

router = APIRouter()


@router.get("/today")
def today_view(request: Request):
    return _day_view(request, today_str())


@router.get("/day/{date_str}")
def day_view(request: Request, date_str: str):
    return _day_view(request, date_str)


def _distribute(blocks, timed_items):
    """시각이 있는 항목을 시작 분 기준으로 해당 블록에 배치한다.

    반환: (block_id -> [item...], 어느 블록에도 안 들어간 leftover 리스트).
    """
    ranges = [
        (b["id"], hhmm_to_min(b["start_time"]), hhmm_to_min(b["end_time"]))
        for b in blocks
    ]
    by_block: dict[int, list] = {b["id"]: [] for b in blocks}
    leftover: list = []
    for it in timed_items:
        m = it["start_min"]
        for bid, s, e in ranges:
            if s <= m < e:
                by_block[bid].append(it)
                break
        else:
            leftover.append(it)
    for items in by_block.values():
        items.sort(key=lambda x: x["start_min"])
    return by_block, leftover


def _day_agenda(blocks, d, is_today):
    """그날의 캘린더 일정·Things Today를 모으고 시간 항목을 블록에 배치한다.

    반환: (cal_events 전체, task_list 전체, block_id -> [시간 항목...]).
    """
    cal_events = gcal.events_for_date(d)
    task_list = things.today_tasks(d, include_overdue=is_today)
    timed: list = []
    for ev in cal_events:
        if not ev["all_day"] and ev["start_min"] is not None:
            timed.append(
                {
                    "kind": "event",
                    "title": ev["title"],
                    "time": ev["start"],
                    "end": ev["end"],
                    "start_min": ev["start_min"],
                    "color": ev["color"],
                }
            )
    for t in task_list:
        if t["time_min"] is not None:
            timed.append(
                {
                    "kind": "task",
                    "title": t["title"],
                    "time": t["time"],
                    "end": None,
                    "start_min": t["time_min"],
                }
            )
    block_events, _leftover = _distribute(blocks, timed)
    return cal_events, task_list, block_events


def _day_view(request: Request, date_str: str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    prev_date = (d - timedelta(days=1)).strftime("%Y-%m-%d")
    next_date = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    is_today = date_str == today_str()
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
        categories = [
            {"id": r["id"], "name": r["name"], "tone": r["tone"]}
            for r in conn.execute(
                "SELECT id, name, tone FROM categories "
                "WHERE is_active = 1 ORDER BY display_order"
            )
        ]
        blocks = conn.execute(
            "SELECT * FROM blocks WHERE date = ? ORDER BY block_order",
            (date_str,),
        ).fetchall()
        slots = conn.execute(
            "SELECT * FROM slots WHERE date = ? ORDER BY slot_index",
            (date_str,),
        ).fetchall()
        meta = conn.execute(
            "SELECT * FROM daily_meta WHERE date = ?", (date_str,)
        ).fetchone()
        # 오늘이 속한 주의 B1-B6 테마를 가져와 PLAN 영역 위에 placeholder로 노출
        wk_start = week_start(d).strftime("%Y-%m-%d")
        theme_rows = conn.execute(
            "SELECT block_label, theme_text FROM weekly_block_themes "
            "WHERE week_start = ?",
            (wk_start,),
        ).fetchall()
        inbox = conn.execute(
            "SELECT id, text FROM inbox WHERE done = 0 ORDER BY id DESC"
        ).fetchall()
        # '다시 볼 날짜'가 이 날짜인 고민·감상(그날 다시 보라고 잡아둔 것)
        due_reflections = conn.execute(
            "SELECT id, kind, title, text, tags, event_date, review_note FROM reflection "
            "WHERE (review_date = ? AND source_id IS NULL) "
            "   OR (source_id IS NOT NULL AND event_date = ?) "
            "ORDER BY id DESC",
            (date_str, date_str),
        ).fetchall()

    themes_by_label = {r["block_label"]: r["theme_text"] for r in theme_rows}
    # 일간 블록 이름 = 일간 덮어쓰기(blocks.name)가 있으면 그것, 없으면 주간 이름.
    block_name_by_id = {
        b["id"]: ((b["name"] or "").strip() or (themes_by_label.get(b["block_label"]) or ""))
        for b in blocks
    }
    slots_by_block: dict[int, list] = {}
    for s in slots:
        slots_by_block.setdefault(s["block_id"], []).append(s)

    # 하루 마감 요약: 완료·기록 슬롯 수와 코어 블록 계획→실행 달성.
    done_slots = sum(1 for s in slots if s["done"])
    recorded_slots = sum(
        1 for s in slots
        if (s["do_text"] or "").strip() or (s["did_text"] or "").strip()
        or s["category_id"] or s["done"]
    )
    core_planned = core_achieved = 0
    for b in blocks:
        if b["is_core"] and (b["plan_text"] or "").strip():
            core_planned += 1
            if any((s["do_text"] or "").strip() or s["done"]
                   for s in slots_by_block.get(b["id"], [])):
                core_achieved += 1
    day_stats = {
        "done": done_slots, "recorded": recorded_slots,
        "core_planned": core_planned, "core_achieved": core_achieved,
    }

    # 외부 연동: Things3 Today + 구글 캘린더 일정.
    # 전체 목록은 최상단에 1번만 줄바꿈으로 노출(cal_events, task_list),
    # 시각이 있는 항목만 해당 시간 블록의 아젠다로 배치한다.
    cal_events, task_list, block_events = _day_agenda(blocks, d, is_today)

    # 오늘 목표/달성/감사·반성을 각각 3개로 분리(줄바꿈 저장, 레거시 1줄도 호환).
    goals = _split3(meta["today_goal"] if meta else "")
    plans = _split3(meta["daily_plan"] if meta else "")
    grats = _split3(meta["gratitude"] if meta else "")
    # 그날 컨셉 3칸(빠른 수집함 자리의 1행 3열).
    concepts = _split3(meta["concept"] if meta else "")
    # 각 3줄에 직접 입력한 자유 태그도 같은 방식으로 3칸으로 분리.
    goal_tags = _split3(meta["goal_tags"] if meta else "")
    plan_tags = _split3(meta["plan_tags"] if meta else "")
    grat_tags = _split3(meta["grat_tags"] if meta else "")

    return templates.TemplateResponse(
        "today.html",
        {
            "request": request,
            "date_str": date_str,
            "prev_date": prev_date,
            "next_date": next_date,
            "is_today": is_today,
            "blocks": blocks,
            "slots_by_block": slots_by_block,
            "categories": categories,
            "meta": meta,
            "goals": goals,
            "plans": plans,
            "grats": grats,
            "concepts": concepts,
            "goal_tags": goal_tags,
            "plan_tags": plan_tags,
            "grat_tags": grat_tags,
            "themes_by_label": themes_by_label,
            "block_name_by_id": block_name_by_id,
            "block_events": block_events,
            "cal_events": cal_events,
            "task_list": task_list,
            "inbox": inbox,
            "due_reflections": [dict(r) for r in due_reflections],
            "cal_enabled": gcal.enabled(),
            "things_write_on": things.enabled(),
            "gcal_events_on": gcal_write.events_enabled(),
            "day_stats": day_stats,
        },
    )


@router.post("/save/day/{date_str}")
async def save_day(date_str: str, request: Request):
    form = await request.form()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
        # 일간 블록 이름 덮어쓰기 판정을 위해 주간 이름과 블록 라벨을 미리 로드
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        wk = week_start(d).strftime("%Y-%m-%d")
        block_label_by_id = {
            r["id"]: r["block_label"]
            for r in conn.execute(
                "SELECT id, block_label FROM blocks WHERE date = ?", (date_str,)
            )
        }
        weekly_name = {
            r["block_label"]: (r["theme_text"] or "").strip()
            for r in conn.execute(
                "SELECT block_label, theme_text FROM weekly_block_themes "
                "WHERE week_start = ?",
                (wk,),
            )
        }
        for key, val in form.multi_items():
            prefix, _, suffix = key.partition("_")
            if not suffix.isdigit():
                continue
            sid = int(suffix)
            if prefix == "plan":
                conn.execute(
                    "UPDATE blocks SET plan_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "see":
                conn.execute(
                    "UPDATE blocks SET see_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "do":
                conn.execute(
                    "UPDATE slots SET do_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "did":
                conn.execute(
                    "UPDATE slots SET did_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "cat":
                cid = int(val) if val else None
                conn.execute(
                    "UPDATE slots SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, sid),
                )
            elif prefix == "bcat":
                cid = int(val) if val else None
                conn.execute(
                    "UPDATE blocks SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, sid),
                )
            elif prefix == "bname":
                label = block_label_by_id.get(sid, "")
                override = _name_override(val, weekly_name.get(label, ""))
                conn.execute(
                    "UPDATE blocks SET name = ?, updated_at = ? WHERE id = ?",
                    (override, now, sid),
                )
            elif prefix == "bloc":
                conn.execute(
                    "UPDATE blocks SET location = ?, updated_at = ? WHERE id = ?",
                    (val or None, now, sid),
                )
        conn.execute(
            """
            INSERT INTO daily_meta (date, today_goal, daily_plan, memo, vow, gratitude,
                                    goal_tags, plan_tags, grat_tags, concept)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                today_goal = excluded.today_goal,
                daily_plan = excluded.daily_plan,
                memo = excluded.memo,
                vow = excluded.vow,
                gratitude = excluded.gratitude,
                goal_tags = excluded.goal_tags,
                plan_tags = excluded.plan_tags,
                grat_tags = excluded.grat_tags,
                concept = excluded.concept
            """,
            (
                date_str,
                _join3(form, "goal"),
                _join3(form, "dplan"),
                form.get("memo", ""),
                form.get("vow", ""),
                _join3(form, "grat"),
                _join3(form, "goaltag"),
                _join3(form, "plantag"),
                _join3(form, "grattag"),
                _join3(form, "concept"),
            ),
        )
    # 저장 후: 오늘 달성 3줄을 '성과' 캘린더에 종일 이벤트로 자동 반영(설명란 1. 2. 3.).
    # 캘린더 I/O는 DB 잠금 밖에서 하고, 실패해도 저장은 그대로 둔다.
    if gcal_write.achieve_enabled():
        try:
            items = [(form.get(f"dplan{i}", "") or "").strip() for i in (1, 2, 3)]
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT achieve_event_id FROM daily_meta WHERE date = ?", (date_str,)
                ).fetchone()
            existing = row["achieve_event_id"] if row else None
            new_id = await _off_loop(gcal_write.upsert_achievement_event, date_str, items, existing)
            if new_id != existing:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE daily_meta SET achieve_event_id = ? WHERE date = ?",
                        (new_id, date_str),
                    )
        except Exception:
            pass
    return RedirectResponse(url=f"/day/{date_str}", status_code=303)


# -- 필드별 자동저장 (blur/debounce 한 칸 즉시 저장) -------------------------
# 장기플랜 /plan/cell/save 와 같은 단일 필드 저장 패턴. 전체 폼 저장(저장 버튼)과
# 병행해 쓴다. 클라이언트는 한 필드가 바뀌면 곧장 이 엔드포인트로 보낸다.

_VALID_BLOCK_FIELDS = {"plan_text", "see_text", "bcat", "bname", "bloc"}
_VALID_SLOT_FIELDS = {"do_text", "did_text", "cat"}


@router.post("/save/field")
async def save_field(request: Request):
    """한 필드만 즉시 저장한다. entity=block|slot|meta, id, field, value 를 받는다."""
    form = await request.form()
    entity = (form.get("entity") or "").strip()
    field = (form.get("field") or "").strip()
    raw_id = form.get("id")
    value = form.get("value") or ""
    now = datetime.now(KST).isoformat(timespec="seconds")
    # block/slot 은 숫자 id, meta(날짜)·wmeta(주 시작일)·theme(주 시작일) 는 문자열 id 를 쓴다.
    rid = None
    if entity not in ("meta", "wmeta", "theme"):
        try:
            rid = int(raw_id)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)

    # 달성(dplan) 자동저장이면 저장 뒤 성과 캘린더에 반영할 날짜를 담는다(없으면 None).
    achieve_date = None
    with get_conn() as conn:
        if entity == "block":
            if field not in _VALID_BLOCK_FIELDS:
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            row = conn.execute(
                "SELECT date, block_label FROM blocks WHERE id = ?", (rid,)
            ).fetchone()
            if not row:
                return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
            if field == "bname":
                # 일간 덮어쓰기 판정을 위해 주간 이름과 비교(None이면 상속)
                wk = week_start(
                    datetime.strptime(row["date"], "%Y-%m-%d").date()
                ).strftime("%Y-%m-%d")
                wrow = conn.execute(
                    "SELECT theme_text FROM weekly_block_themes "
                    "WHERE week_start = ? AND block_label = ?",
                    (wk, row["block_label"]),
                ).fetchone()
                override = _name_override(value, (wrow["theme_text"] if wrow else ""))
                conn.execute(
                    "UPDATE blocks SET name = ?, updated_at = ? WHERE id = ?",
                    (override, now, rid),
                )
            elif field == "bcat":
                cid = int(value) if value else None
                conn.execute(
                    "UPDATE blocks SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, rid),
                )
            else:  # plan_text | see_text | bloc
                col = "location" if field == "bloc" else field
                conn.execute(
                    f"UPDATE blocks SET {col} = ?, updated_at = ? WHERE id = ?",
                    ((value or None) if field == "bloc" else value, now, rid),
                )
        elif entity == "slot":
            if field not in _VALID_SLOT_FIELDS:
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            if field == "cat":
                cid = int(value) if value else None
                conn.execute(
                    "UPDATE slots SET category_id = ?, updated_at = ? WHERE id = ?",
                    (cid, now, rid),
                )
            else:  # do_text | did_text
                conn.execute(
                    f"UPDATE slots SET {field} = ?, updated_at = ? WHERE id = ?",
                    (value, now, rid),
                )
        elif entity == "meta":
            # id 자리에 날짜(문자열)가 온다. field: goal1~3|dplan1~3|memo|vow
            date_str = form.get("id") or ""
            if not _parse_date(date_str):
                return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
            if field in ("memo", "vow"):
                conn.execute(
                    "INSERT INTO daily_meta (date, %s) VALUES (?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET %s = excluded.%s"
                    % (field, field, field),
                    (date_str, value),
                )
            elif field.startswith("goaltag") or field.startswith("plantag") or field.startswith("grattag"):
                # 목표/달성/감사 각 줄의 자유 태그 3칸(직접 입력). 바뀐 칸과 그룹 나머지 값을
                # prefix+번호(goaltag1) 키로 함께 받아 줄바꿈으로 합친다.
                if field.startswith("goaltag"):
                    prefix, col = "goaltag", "goal_tags"
                elif field.startswith("plantag"):
                    prefix, col = "plantag", "plan_tags"
                else:
                    prefix, col = "grattag", "grat_tags"
                existing = conn.execute(
                    f"SELECT {col} FROM daily_meta WHERE date = ?", (date_str,)
                ).fetchone()
                parts = (existing[col] if existing and existing[col] else "").split("\n") if existing else []
                parts = (parts + ["", "", ""])[:3]
                for i in range(3):
                    key = f"{prefix}{i + 1}"
                    if key not in form:
                        continue
                    parts[i] = (form.get(key, "") or "").replace("\r", " ").replace("\n", " ").strip()
                joined = "\n".join(parts)
                joined = joined if joined.strip() else ""
                conn.execute(
                    "INSERT INTO daily_meta (date, %s) VALUES (?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET %s = excluded.%s"
                    % (col, col, col),
                    (date_str, joined),
                )
            elif (field.startswith("goal") or field.startswith("dplan")
                  or field.startswith("grat") or field.startswith("concept")):
                # 목표/달성/감사·반성/컨셉 3칸: 바뀐 한 칸과 나머지 두 칸을 prefix+번호(goal1) 키로
                # 함께 받아 줄바꿈으로 합친다. 각 칸 내부 줄바꿈은 공백으로 눌러 3칸 구분을 지킨다.
                if field.startswith("goal"):
                    prefix, col = "goal", "today_goal"
                elif field.startswith("dplan"):
                    prefix, col = "dplan", "daily_plan"
                elif field.startswith("concept"):
                    prefix, col = "concept", "concept"
                else:
                    prefix, col = "grat", "gratitude"
                existing = conn.execute(
                    f"SELECT {col} FROM daily_meta WHERE date = ?", (date_str,)
                ).fetchone()
                parts = (existing[col] if existing and existing[col] else "").split("\n") if existing else []
                parts = (parts + ["", "", ""])[:3]
                for i in range(3):
                    key = f"{prefix}{i + 1}"
                    if key not in form:
                        continue
                    parts[i] = (form.get(key, "") or "").replace("\r", " ").replace("\n", " ")
                joined = "\n".join(p.strip() for p in parts)
                joined = joined if joined.strip() else ""
                conn.execute(
                    "INSERT INTO daily_meta (date, %s) VALUES (?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET %s = excluded.%s"
                    % (col, col, col),
                    (date_str, joined),
                )
                if prefix == "dplan":
                    achieve_date = date_str  # 달성이 바뀌었으니 저장 후 성과 캘린더 반영
            else:
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
        elif entity == "wmeta":
            # id 자리에 주 시작일(week_start). field: weekly_goal|appointments|vow|memo
            ws = form.get("id") or ""
            if not _parse_date(ws):
                return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
            if field not in ("weekly_goal", "appointments", "vow", "memo"):
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            conn.execute(
                "INSERT INTO weekly_meta (week_start, %s) VALUES (?, ?) "
                "ON CONFLICT(week_start) DO UPDATE SET %s = excluded.%s"
                % (field, field, field),
                (ws, value),
            )
        elif entity == "theme":
            # id=week_start, label=블록 라벨(B1..B6), value=테마 텍스트
            ws = form.get("id") or ""
            if not _parse_date(ws):
                return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
            label = (form.get("label") or "").strip()
            if not label:
                return JSONResponse({"ok": False, "error": "bad-label"}, status_code=400)
            conn.execute(
                "INSERT INTO weekly_block_themes (week_start, block_label, theme_text, "
                "updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(week_start, block_label) DO UPDATE SET "
                "theme_text = excluded.theme_text, updated_at = excluded.updated_at",
                (ws, label, value, now),
            )
        else:
            return JSONResponse({"ok": False, "error": "bad-entity"}, status_code=400)
    # 달성 자동저장이면 성과 캘린더에도 즉시 반영한다(저장 버튼 없이도 최신 유지). DB 잠금 밖에서 I/O.
    if achieve_date and gcal_write.achieve_enabled():
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT daily_plan, achieve_event_id FROM daily_meta WHERE date = ?",
                    (achieve_date,),
                ).fetchone()
            items = (row["daily_plan"] or "").split("\n") if row else []
            existing = row["achieve_event_id"] if row else None
            new_id = await _off_loop(gcal_write.upsert_achievement_event, achieve_date, items, existing)
            if new_id != existing:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE daily_meta SET achieve_event_id = ? WHERE date = ?",
                        (new_id, achieve_date),
                    )
        except Exception:
            pass
    return JSONResponse({"ok": True})


# -- GTD 빠른 수집함 --------------------------------------------------------


@router.post("/inbox/add")
async def inbox_add(request: Request):
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO inbox (text, created_at) VALUES (?, ?)", (text, now)
        )
        new_id = cur.lastrowid
    return JSONResponse({"ok": True, "id": new_id, "text": text})


@router.post("/inbox/done/{item_id}")
def inbox_done(item_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE inbox SET done = 1 WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True})


@router.post("/inbox/delete/{item_id}")
def inbox_delete(item_id: int):
    """수집함 항목을 완전히 삭제한다(정리 ✓와 달리 DB에서 지움)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM inbox WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True})


@router.post("/inbox/update")
async def inbox_update(request: Request):
    """수집함 항목 텍스트를 수정한다(오늘·주간 공용. 같은 inbox 테이블)."""
    form = await request.form()
    try:
        item_id = int(form.get("item_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    text = (form.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE inbox SET text = ? WHERE id = ?", (text, item_id))
    return JSONResponse({"ok": True})


INBOX_STATUSES = {"", "next", "wait", "someday", "ref"}


@router.post("/inbox/status")
async def inbox_status(request: Request):
    """수집함 항목의 GTD 상태(미분류/다음행동/대기/언젠가/참고)를 저장한다(주간 정리 단계)."""
    form = await request.form()
    try:
        item_id = int(form.get("item_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    status = (form.get("status") or "").strip()
    if status not in INBOX_STATUSES:
        return JSONResponse({"ok": False, "error": "bad-status"}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE inbox SET status = ? WHERE id = ?", (status, item_id))
    return JSONResponse({"ok": True})


# -- 오늘 외부 입력: Things3 할일 / 구글 일정 쓰기 -------------------------


@router.post("/things/add")
async def things_add(request: Request):
    """오늘 탭에서 입력한 할일을 Things3 Today에 만든다(macOS AppleScript)."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    if not things.enabled():
        return JSONResponse({"ok": False, "error": "things-off"}, status_code=400)
    ok = await _off_loop(things.add_todo, title)
    if not ok:
        return JSONResponse({"ok": False, "error": "권한 미승인 또는 Things3 미실행"},
                            status_code=502)
    return JSONResponse({"ok": True})


@router.post("/gcal/event/add")
async def gcal_event_add(request: Request):
    """오늘 탭에서 입력한 일정을 일정용 구글 캘린더에 만든다(서비스계정)."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    time_hhmm = (form.get("time") or "").strip() or None
    date_str = (form.get("date") or today_str()).strip()
    if not title:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    if not gcal_write.events_enabled():
        return JSONResponse(
            {"ok": False, "error": "일정 쓰기 미설정(캘린더 공유 + GCAL_WRITE_EVENTS_CALENDAR_ID)"},
            status_code=400,
        )
    try:
        ev = await _off_loop(gcal_write.create_calendar_event, title, date_str, time_hhmm)
    except Exception:
        ev = None
    if not ev:
        return JSONResponse({"ok": False, "error": "캘린더 생성 실패"}, status_code=502)
    return JSONResponse({"ok": True, "id": ev})


@router.post("/inbox/assign")
async def inbox_assign(request: Request):
    """수집함 항목을 한 블록의 PLAN 끝에 한 줄로 옮기고 수집함에서는 정리한다(GTD 정리 단계)."""
    form = await request.form()
    try:
        item_id = int(form.get("item_id"))
        block_id = int(form.get("block_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        it = conn.execute("SELECT text FROM inbox WHERE id = ?", (item_id,)).fetchone()
        blk = conn.execute(
            "SELECT plan_text FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        if not it or not blk:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        cur = (blk["plan_text"] or "").rstrip()
        plan_text = f"{cur}\n{it['text']}" if cur else it["text"]
        conn.execute(
            "UPDATE blocks SET plan_text = ?, updated_at = ? WHERE id = ?",
            (plan_text, now, block_id),
        )
        conn.execute("UPDATE inbox SET done = 1 WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True, "block_id": block_id, "plan_text": plan_text})


@router.post("/block/rollover")
async def block_rollover(request: Request):
    """이 블록의 PLAN을 다음 날 같은 블록 PLAN 끝에 복사한다(미룬 계획 이월)."""
    form = await request.form()
    try:
        block_id = int(form.get("block_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        src = conn.execute(
            "SELECT date, block_label, plan_text FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        if not src:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        plan = (src["plan_text"] or "").strip()
        if not plan:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        d = datetime.strptime(src["date"], "%Y-%m-%d").date()
        nxt = (d + timedelta(days=1)).strftime("%Y-%m-%d")
        ensure_day_skeleton(conn, nxt)
        dst = conn.execute(
            "SELECT id, plan_text FROM blocks WHERE date = ? AND block_label = ?",
            (nxt, src["block_label"]),
        ).fetchone()
        if not dst:
            return JSONResponse({"ok": False, "error": "no-target"}, status_code=404)
        cur = (dst["plan_text"] or "").rstrip()
        new_plan = f"{cur}\n{plan}" if cur else plan
        conn.execute(
            "UPDATE blocks SET plan_text = ?, updated_at = ? WHERE id = ?",
            (new_plan, now, dst["id"]),
        )
    return JSONResponse({"ok": True, "date": nxt, "label": src["block_label"]})


@router.post("/meta/tomorrow-goal")
async def meta_tomorrow_goal(request: Request):
    """하루 마감에서 적은 '내일 가장 중요한 일'을 다음 날 목표 1번에 저장한다."""
    form = await request.form()
    date_str = (form.get("date") or "").strip()
    text = (form.get("text") or "").strip()
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
    nxt = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT today_goal FROM daily_meta WHERE date = ?", (nxt,)
        ).fetchone()
        base = (row["today_goal"] if row and row["today_goal"] else "")
        parts = (base.split("\n") + ["", "", ""])[:3]
        parts[0] = text
        joined = "\n".join(p.strip() for p in parts)
        joined = joined if joined.strip() else ""
        conn.execute(
            "INSERT INTO daily_meta (date, today_goal) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET today_goal = excluded.today_goal",
            (nxt, joined),
        )
    return JSONResponse({"ok": True, "date": nxt})


# -- 슬롯 실행 체크 + 실시간 폴링 -------------------------------------------


@router.post("/slot/done/{slot_id}")
async def slot_done(slot_id: int, request: Request):
    """DO 옆 체크박스. 즉시 저장(폼 저장과 별개)."""
    form = await request.form()
    val = 1 if (form.get("done") in ("1", "true", "on")) else 0
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "UPDATE slots SET done = ?, updated_at = ? WHERE id = ?",
            (val, now, slot_id),
        )
    return JSONResponse({"ok": True, "done": val})


@router.get("/api/day/{date_str}")
def api_day(date_str: str):
    """현재 캘린더·Things 아젠다를 JSON으로. 클라이언트가 주기적으로 폴링해 갱신."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    is_today = date_str == today_str()
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
        blocks = conn.execute(
            "SELECT * FROM blocks WHERE date = ? ORDER BY block_order",
            (date_str,),
        ).fetchall()
    cal_events, task_list, block_events = _day_agenda(blocks, d, is_today)
    order_by_id = {b["id"]: b["block_order"] for b in blocks}
    blocks_json: dict[str, list] = {}
    for bid, items in block_events.items():
        if items:
            blocks_json[str(order_by_id[bid])] = items
    return JSONResponse(
        {
            "cal_enabled": gcal.enabled(),
            "events": [
                {"all_day": e["all_day"], "start": e["start"], "title": e["title"],
                 "color": e["color"]}
                for e in cal_events
            ],
            "tasks": [
                {
                    "time": t["time"],
                    "title": t["title"],
                    "deadline": t["deadline"],
                    "overdue": t["overdue"],
                    "tags": t.get("tags", []),
                }
                for t in task_list
            ],
            "blocks": blocks_json,
        }
    )
