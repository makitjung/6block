# 오늘/특정 날짜 화면과 그 저장(블록·슬롯·수집함·외부 입력)을 담당하는 라우터
import hashlib
import hmac
import json
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.common import (
    KST,
    RowId,
    _join3,
    _name_override,
    _off_loop,
    _parse_date,
    _split3,
    ensure_day_skeleton,
    int_id,
    lt_leaves,
    opt_id,
    schedule_version,
    templates,
    today_str,
    week_start,
    week_todos,
)
from app.config import hhmm_to_min
from app.db import get_conn, get_settings, get_weekday_concepts
from app.integrations import gcal, gcal_write, things

router = APIRouter()


def _hidden_task_titles() -> set[str]:
    """설정에서 정한 '화면에 안 보이게 할 할일 제목'(쉼표 구분). 제목이 정확히 같은 것만."""
    raw = get_settings().get("hide_task_titles", "") or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


@router.get("/today")
def today_view(request: Request):
    return _day_view(request, today_str())


@router.get("/day/{date_str}")
def day_view(request: Request, date_str: str):
    # 주소에 날짜가 아닌 것이 들어오면(옛 북마크·오타·잘못 만든 링크) 화면이 통째로
    # 500 이 되지 않게 오늘로 보낸다. 개인용 서버라 오류 화면보다 오늘이 낫다.
    if _parse_date(date_str) is None:
        return RedirectResponse(url="/today")
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
    # 설정에서 숨기기로 한 제목은 여기서 한 번에 걷어 낸다. 주간 띠 칩·블록 팝오버·개수가
    # 모두 이 목록에서 나오므로, 한 군데서 빼면 화면 어디에도 나오지 않는다.
    hidden = _hidden_task_titles()
    task_list = [
        t for t in things.today_tasks(d, include_overdue=is_today)
        if t["title"].strip() not in hidden
    ]
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


MAX_LT_COLS = 3      # 오늘 띠는 최대 세 열. 영역이 더 많으면 열을 돌려 쓴다.


def _lt_columns(items: list[dict]) -> list[list[dict]]:
    """오늘 띠 항목을 영역별로 묶어 최대 세 열로 나눈다.

    같은 영역은 반드시 같은 열에 모이고, 영역이 세 개를 넘으면 앞 열부터 돌려 쓴다.
    items 는 이미 영역 표시 순서로 정렬돼 있어 열 순서도 그 순서를 따른다.
    """
    order: list[str] = []
    for it in items:
        if it["area_name"] not in order:
            order.append(it["area_name"])
    col_of = {name: i % MAX_LT_COLS for i, name in enumerate(order)}
    cols: list[list[dict]] = [[] for _ in range(min(len(order), MAX_LT_COLS))]
    for it in items:
        cols[col_of[it["area_name"]]].append(it)
    return cols


def _lt_items_on(conn, d) -> list[dict]:
    """그 날짜에 걸친 장기 항목 중 최하위 것만. 오늘 탭 위 띠에 쓴다.

    상위는 하위를 묶는 껍데기라 빼고, 대신 어느 상위에서 내려온 것인지 제목을 붙인다.
    남은 일수(D-n)와, 그 주에 따로 적어 둔 계획(goal)이 있으면 그것도 함께 준다.
    장기 탭에서 '가리기'를 건 항목(masked)은 누른 막대 하나만 여기서 뺀다.
    """
    date_str = d.strftime("%Y-%m-%d")
    wk = week_start(d).strftime("%Y-%m-%d")
    goals = {
        r["item_id"]: (r["goal_text"] or "").strip()
        for r in conn.execute(
            "SELECT item_id, goal_text FROM weekly_lt_goal WHERE week_start = ?", (wk,)
        )
    }
    rows = conn.execute(
        "SELECT i.id, i.parent_id, i.title, i.end_date, i.progress, i.block_label, "
        "       a.name AS area_name, "
        "       EXISTS(SELECT 1 FROM lt_item c WHERE c.parent_id = i.id) AS has_children "
        "FROM lt_item i JOIN lt_area a ON a.id = i.area_id "
        "WHERE i.start_date <= ? AND i.end_date >= ? AND a.is_active = 1 "
        "AND i.masked = 0 "
        "ORDER BY a.display_order, i.end_date, i.id",
        (date_str, date_str),
    ).fetchall()
    out = []
    for r in lt_leaves(rows):
        end = _parse_date(r["end_date"])
        left = (end - d).days if end else 0
        out.append({
            "id": r["id"], "title": r["title"], "area_name": r["area_name"],
            "progress": r["progress"], "parent_title": r["parent_title"],
            # 주간 탭에서 그 항목에 따로 적어 둔 이번 주 계획. 없으면 장기 이름을 그대로 쓴다.
            "goal": goals.get(r["id"], ""),
            # 장기 탭에서 정한 코어블록(B1~B6). 어느 블록에서 할 일인지 오늘 띠에 같이 보인다.
            "blocks": (r["block_label"] or "").strip(),
            "dday": "D-day" if left <= 0 else f"D-{left}",
        })
    return out


def _day_view(request: Request, date_str: str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    prev_date = (d - timedelta(days=1)).strftime("%Y-%m-%d")
    next_date = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    is_today = date_str == today_str()
    with get_conn() as conn:
        ensure_day_skeleton(conn, date_str)
        version = schedule_version(conn, (date_str,), include_slots=True)
        categories = [
            dict(r)
            for r in conn.execute(
                "SELECT id, name, tone, is_active FROM categories ORDER BY display_order"
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
        # 그날에 걸친 장기 계획(장기 탭 막대)을 오늘 화면 위에 띠로 노출
        lt_today = _lt_items_on(conn, d)
        # 그 주 '목표' 열에 적은 할 일 목록. 블록·슬롯을 여기에 이어 둔다(글은 직접 입력).
        wk_todos = week_todos(conn, wk_start)
        # 다시보기 사본이 있어도 화면과 메모 편집의 대상은 원본 한 건이다.
        due_reflections = conn.execute(
            "SELECT r.id, r.kind, r.title, r.text, r.tags, r.event_date, r.review_note "
            "FROM reflection r WHERE r.source_id IS NULL AND r.id IN ("
            "SELECT id FROM reflection WHERE source_id IS NULL AND review_date = ? "
            "UNION SELECT source_id FROM reflection WHERE source_id IS NOT NULL AND event_date = ?"
            ") ORDER BY r.id DESC",
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
    # 고정 할일이 채운 칸(is_routine)은 사람이 남긴 기록이 아니므로 세지 않는다.
    done_slots = sum(1 for s in slots if s["done"])
    recorded_slots = sum(
        1 for s in slots
        if (s["did_text"] or "").strip() or s["done"]
        or (not s["is_routine"] and ((s["do_text"] or "").strip() or s["category_id"]))
    )
    core_planned = core_achieved = 0
    for b in blocks:
        if b["is_core"] and (b["plan_text"] or "").strip():
            core_planned += 1
            if any(s["done"] or (s["did_text"] or "").strip()
                   or (not s["is_routine"] and (s["do_text"] or "").strip())
                   for s in slots_by_block.get(b["id"], [])):
                core_achieved += 1
    day_stats = {
        "done": done_slots, "recorded": recorded_slots, "total": len(slots),
        "core_planned": core_planned, "core_achieved": core_achieved,
    }

    # 하루 마감에서 한 번에 채울 '지나갔는데 기록이 없는' 코어 슬롯.
    # 실행 퍼널이 낮게 나오는 까닭이 계획을 안 세워서가 아니라 하고도 안 남겨서라,
    # 하루 끝에 빈 칸만 모아 한 화면에서 메우게 한다. 버퍼(점심·저녁)는 뺀다.
    catchup = []
    if is_today:
        now_min = datetime.now(KST).hour * 60 + datetime.now(KST).minute
        for b in blocks:
            if not b["is_core"]:
                continue
            name = block_name_by_id.get(b["id"], "")
            for s in slots_by_block.get(b["id"], []):
                if hhmm_to_min(s["end_time"]) > now_min:
                    continue                        # 아직 오지 않은 슬롯
                if s["done"] or (s["did_text"] or "").strip():
                    continue                        # 이미 기록이 있다
                catchup.append({
                    "id": s["id"], "time": s["start_time"],
                    "block": b["block_label"], "name": name,
                    "plan": (s["do_text"] or "").strip(),
                })

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
    # 목표 3줄이 각각 이은 그 주 할 일 키(빈 칸이면 연결 없음).
    goal_links = _split3(meta["goal_links"] if meta else "")
    goal_tags = _split3(meta["goal_tags"] if meta else "")
    plan_tags = _split3(meta["plan_tags"] if meta else "")
    grat_tags = _split3(meta["grat_tags"] if meta else "")

    return templates.TemplateResponse(
        request,
        "today.html",
        {
            "date_str": date_str,
            "schedule_version": version,
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
            # 설정에서 정한 그 요일의 컨셉(날짜 옆 괄호). 비었으면 괄호도 안 나온다.
            "weekday_concept": get_weekday_concepts()[d.weekday()],
            "goal_tags": goal_tags,
            "goal_links": goal_links,
            "plan_tags": plan_tags,
            "grat_tags": grat_tags,
            "lt_today": lt_today,
            "lt_today_cols": _lt_columns(lt_today),
            "wk_todos": wk_todos,
            "themes_by_label": themes_by_label,
            "block_name_by_id": block_name_by_id,
            "block_events": block_events,
            "cal_events": cal_events,
            "task_list": task_list,
            "inbox": inbox,
            "due_reflections": [dict(r) for r in due_reflections],
            "cal_enabled": gcal.enabled(),
            "things_write_on": things.enabled(),
            "gcal_events_on": gcal_write.write_enabled("events"),
            "day_stats": day_stats,
            "catchup": catchup,
        },
    )


@router.post("/save/day/{date_str}")
async def save_day(date_str: str, request: Request):
    if _parse_date(date_str) is None:
        return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
    form = await request.form()
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure_day_skeleton(conn, date_str)
        if form.get("schedule_version") and form["schedule_version"] != schedule_version(
            conn, (date_str,), include_slots=True
        ):
            conn.rollback()
            return _stale_schedule()
        # 일간 블록 이름 덮어쓰기 판정을 위해 주간 이름과 블록 라벨을 미리 로드
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        wk = week_start(d).strftime("%Y-%m-%d")
        block_label_by_id = {
            r["id"]: r["block_label"]
            for r in conn.execute(
                "SELECT id, block_label FROM blocks WHERE date = ?", (date_str,)
            )
        }
        slot_ids = {r["id"] for r in conn.execute("SELECT id FROM slots WHERE date = ?", (date_str,))}
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
            # 칸 이름 뒤 숫자가 행 id 다. 못 읽거나 범위를 벗어나면 맞는 행이 없으니 건너뛴다
            # (예전에는 아주 큰 숫자가 그대로 쿼리에 들어가 저장 전체가 500 이 됐다).
            sid = opt_id(suffix)
            if sid is None:
                continue
            if prefix in {"plan", "see", "wtb", "bcat", "bname", "bloc"} and sid not in block_label_by_id:
                continue
            if prefix in {"do", "did", "wts", "cat"} and sid not in slot_ids:
                continue
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
                # 폼은 손대지 않은 칸까지 함께 보내므로, 값이 달라진 칸만 고정 할일 표시를 푼다.
                conn.execute(
                    "UPDATE slots SET do_text = ?, updated_at = ?, is_routine = "
                    "CASE WHEN TRIM(COALESCE(do_text, '')) = TRIM(?) "
                    "     THEN is_routine ELSE 0 END WHERE id = ?",
                    (val, now, val, sid),
                )
            elif prefix == "did":
                conn.execute(
                    "UPDATE slots SET did_text = ?, updated_at = ? WHERE id = ?",
                    (val, now, sid),
                )
            elif prefix == "wtb":       # 블록 ↔ 그 주 할 일 연결(키만 저장)
                conn.execute(
                    "UPDATE blocks SET wk_todo = ?, updated_at = ? WHERE id = ?",
                    (val or None, now, sid),
                )
            elif prefix == "wts":       # 슬롯 ↔ 그 주 할 일 연결(키만 저장)
                conn.execute(
                    "UPDATE slots SET wk_todo = ?, updated_at = ? WHERE id = ?",
                    (val or None, now, sid),
                )
            elif prefix == "cat":
                cid = opt_id(val)
                conn.execute(
                    "UPDATE slots SET category_id = ?, updated_at = ?, is_routine = "
                    "CASE WHEN category_id IS ? THEN is_routine ELSE 0 END WHERE id = ?",
                    (cid, now, cid, sid),
                )
            elif prefix == "bcat":
                cid = opt_id(val)
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
                                    goal_tags, plan_tags, grat_tags, concept, goal_links)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                today_goal = excluded.today_goal,
                daily_plan = excluded.daily_plan,
                memo = excluded.memo,
                vow = excluded.vow,
                gratitude = excluded.gratitude,
                goal_tags = excluded.goal_tags,
                plan_tags = excluded.plan_tags,
                grat_tags = excluded.grat_tags,
                concept = excluded.concept,
                goal_links = excluded.goal_links
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
                _join3(form, "goallink"),
            ),
        )
        # 하루 평가는 '오늘' 화면(하루 마감 카드)에만 있는 칸이라, 지난 날짜를 저장할 때는
        # 폼에 아예 오지 않는다. 왔을 때만 덮어써야 옛 기록이 빈 값으로 지워지지 않는다.
        if "day_review" in form:
            conn.execute(
                "UPDATE daily_meta SET day_review = ? WHERE date = ?",
                (form.get("day_review", ""), date_str),
            )
    # 저장 후: 오늘 달성 3줄을 '성과' 캘린더에 종일 이벤트로 자동 반영(설명란 1. 2. 3.).
    # 캘린더 I/O는 DB 잠금 밖에서 하고, 실패해도 저장은 그대로 둔다.
    if gcal_write.write_enabled("achieve"):
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

_VALID_BLOCK_FIELDS = {"plan_text", "see_text", "bcat", "bname", "bloc", "wk_todo"}
_VALID_SLOT_FIELDS = {"do_text", "did_text", "cat", "wk_todo"}

# daily_meta 에서 3칸을 줄바꿈으로 합쳐 한 컬럼에 담는 필드(폼 접두어 → 컬럼).
# 접두어가 긴 것을 먼저 둔다. 'goaltag1'·'goallink1' 이 'goal' 에 먼저 걸리면
# 태그·연결이 목표 본문 컬럼에 저장돼 버린다(같은 이유로 grattag 가 grat 보다 앞).
_META_TRIPLES = (
    ("goaltag", "goal_tags"), ("goallink", "goal_links"),
    ("plantag", "plan_tags"), ("grattag", "grat_tags"),
    ("goal", "today_goal"), ("dplan", "daily_plan"),
    ("grat", "gratitude"), ("concept", "concept"),
)
# 3칸이 아니라 통째로 한 칸인 필드.
_META_SINGLES = ("memo", "vow", "day_review")


def _stale_schedule():
    return JSONResponse({"ok": False, "error": "stale-schedule"}, status_code=409)


def _identity_error(row, form, *, block=False):
    keys = ("date", "start_time", "end_time")
    if block or "block_label" in form:
        keys += ("block_label",)
    if any(key in form for key in keys):
        if row is None or any(form.get(key) != row[key] for key in keys):
            return _stale_schedule()
    if row is None:
        return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
    return None


def _edit_conflict(form, current, submitted, *, done=None, normalize=None):
    """Allow an already-applied retry while rejecting edits from an obsolete view."""
    current = str(current) if current is not None else ""
    submitted = str(submitted) if submitted is not None else ""
    expected = form.get("expected_value")
    if expected is None:
        return None
    if normalize is not None:
        current, expected, submitted = map(normalize, (current, expected, submitted))
    if current == submitted:
        result = {"ok": True}
        if done is not None:
            result["done"] = done
        return JSONResponse(result)
    if expected != current:
        return JSONResponse({"ok": False, "error": "edit-conflict", "current_value": current},
                            status_code=409)
    return None


def _merge3(conn, key: str, prefix: str, col: str, field: str, value: str, form, *, weekly=False):
    """Merge the changed field, also accepting grouped forms from older clients."""
    table, key_col = ("weekly_meta", "week_start") if weekly else ("daily_meta", "date")
    row = conn.execute(
        f"SELECT {col} FROM {table} WHERE {key_col} = ?", (key,)
    ).fetchone()
    parts = _split3(row[col] if row else None)
    def normalize(text: str) -> str:
        return text.replace("\r", " ").replace("\n", " ").strip()

    requested = normalize(form.get(field, value) or "")
    error = _edit_conflict(form, parts[int(field[-1]) - 1], requested, normalize=normalize)
    if error is not None:
        return error
    changes = {field: requested} if "expected_value" in form else {**dict(form), field: requested}
    for i in range(3):
        change = f"{prefix}{i + 1}"
        if change in changes:
            parts[i] = normalize(changes[change] or "")
    joined = "\n".join(parts)
    conn.execute(
        f"INSERT INTO {table} ({key_col}, {col}) VALUES (?, ?) "
        f"ON CONFLICT({key_col}) DO UPDATE SET {col} = excluded.{col}",
        (key, joined if joined.strip() else ""),
    )
    return None


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
            rid = int_id(raw_id)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)

    # 달성(dplan) 자동저장이면 저장 뒤 성과 캘린더에 반영할 날짜를 담는다(없으면 None).
    achieve_date = None
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if entity == "block":
            if field not in _VALID_BLOCK_FIELDS:
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            row = conn.execute(
                "SELECT * FROM blocks WHERE id = ?", (rid,)
            ).fetchone()
            error = _identity_error(row, form, block=True)
            if error is not None:
                return error
            col = {"bname": "name", "bcat": "category_id", "bloc": "location"}.get(field, field)
            current = row[col]
            stored = value
            submitted = value
            normalize = None
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
                inherited = ((wrow["theme_text"] if wrow else "") or "").strip()
                current = (current or "").strip()
                stored = _name_override(value, inherited)
                submitted = stored
                if form.get("name_view") != "override":
                    current = current or inherited
                    submitted = stored or inherited
                else:
                    def normalize_override(text):
                        return _name_override(text, inherited) or ""

                    normalize = normalize_override
            elif field == "bcat":
                stored = opt_id(value)
                submitted = stored
            elif field in ("bloc", "wk_todo"):
                stored = value or None
            error = _edit_conflict(form, current, submitted, normalize=normalize)
            if error is not None:
                return error
            conn.execute(
                f"UPDATE blocks SET {col} = ?, updated_at = ? WHERE id = ?", (stored, now, rid),
            )
        elif entity == "slot":
            if field not in _VALID_SLOT_FIELDS:
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            row = conn.execute(
                "SELECT s.*, b.block_label FROM slots s JOIN blocks b ON b.id = s.block_id "
                "WHERE s.id = ?", (rid,),
            ).fetchone()
            error = _identity_error(row, form)
            if error is not None:
                return error
            col = "category_id" if field == "cat" else field
            error = _edit_conflict(form, row[col], opt_id(value) if field == "cat" else value)
            if error is not None:
                return error
            # 사람이 값을 바꾼 칸은 고정 할일 표시를 푼다(그때부터 계획으로 잡히고, 템플릿을
            # 다시 적용해도 덮이지 않는다). 자동저장은 그냥 지나가기만 해도(blur) 한 번 부르므로
            # 값이 실제로 달라졌을 때만 푼다. SQLite 는 SET 식을 옛 행 값으로 계산한다.
            if field == "cat":
                cid = opt_id(value)
                conn.execute(
                    "UPDATE slots SET category_id = ?, updated_at = ?, is_routine = "
                    "CASE WHEN COALESCE(category_id, -1) = COALESCE(?, -1) "
                    "     THEN is_routine ELSE 0 END WHERE id = ?",
                    (cid, now, cid, rid),
                )
            elif field == "do_text":
                conn.execute(
                    "UPDATE slots SET do_text = ?, updated_at = ?, is_routine = "
                    "CASE WHEN TRIM(COALESCE(do_text, '')) = TRIM(?) "
                    "     THEN is_routine ELSE 0 END WHERE id = ?",
                    (value, now, value, rid),
                )
            else:  # did_text | wk_todo
                conn.execute(
                    f"UPDATE slots SET {field} = ?, updated_at = ? WHERE id = ?",
                    ((value or None) if field == "wk_todo" else value, now, rid),
                )
        elif entity == "meta":
            # id 자리에 날짜(문자열)가 온다.
            # field: goal1~3|dplan1~3|grat1~3|concept1~3|각 태그·연결 3칸|memo|vow|day_review
            date_str = form.get("id") or ""
            if not _parse_date(date_str):
                return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
            if field in _META_SINGLES:
                row = conn.execute(f"SELECT {field} FROM daily_meta WHERE date = ?", (date_str,)).fetchone()
                error = _edit_conflict(form, row[field] if row else None, value)
                if error is not None:
                    return error
                conn.execute(
                    f"INSERT INTO daily_meta (date, {field}) VALUES (?, ?) "
                    f"ON CONFLICT(date) DO UPDATE SET {field} = excluded.{field}",
                    (date_str, value),
                )
            else:
                for prefix, col in _META_TRIPLES:
                    if field[:-1] != prefix or field[-1] not in "123":
                        continue
                    error = _merge3(conn, date_str, prefix, col, field, value, form)
                    if error is not None:
                        return error
                    if prefix == "dplan":
                        achieve_date = date_str   # 달성이 바뀌었으니 성과 캘린더 반영
                    break
                else:
                    return JSONResponse({"ok": False, "error": "bad-field"},
                                        status_code=400)
        elif entity == "wmeta":
            # id 자리에 주 시작일(week_start).
            # field: wgoal1~3(목표 열 자유 란, 3칸을 합쳐 weekly_goal로)|appointments|vow|memo
            ws = form.get("id") or ""
            if not _parse_date(ws):
                return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
            if field in {"wgoal1", "wgoal2", "wgoal3"}:
                error = _merge3(conn, ws, "wgoal", "weekly_goal", field, value, form, weekly=True)
                return error if error is not None else JSONResponse({"ok": True})
            if field not in ("appointments", "vow", "memo"):
                return JSONResponse({"ok": False, "error": "bad-field"}, status_code=400)
            row = conn.execute(f"SELECT {field} FROM weekly_meta WHERE week_start = ?", (ws,)).fetchone()
            error = _edit_conflict(form, row[field] if row else None, value)
            if error is not None:
                return error
            conn.execute(
                f"INSERT INTO weekly_meta (week_start, {field}) VALUES (?, ?) "
                f"ON CONFLICT(week_start) DO UPDATE SET {field} = excluded.{field}",
                (ws, value),
            )
        elif entity == "ltgoal":
            # id=장기 항목 id, week_start=주 시작일. 목표 열에서 그 항목의 이번 주 계획.
            ws = (form.get("week_start") or "").strip()
            item_id = opt_id(form.get("id"))
            if not _parse_date(ws) or item_id is None:
                return JSONResponse({"ok": False, "error": "bad-input"}, status_code=400)
            if not conn.execute("SELECT 1 FROM lt_item WHERE id = ?", (item_id,)).fetchone():
                return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
            row = conn.execute("SELECT goal_text FROM weekly_lt_goal WHERE week_start = ? AND item_id = ?",
                               (ws, item_id)).fetchone()
            error = _edit_conflict(form, row["goal_text"] if row else None, value.strip())
            if error is not None:
                return error
            conn.execute(
                "INSERT INTO weekly_lt_goal (week_start, item_id, goal_text, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(week_start, item_id) DO UPDATE SET "
                "goal_text = excluded.goal_text, updated_at = excluded.updated_at",
                (ws, item_id, value.strip(), now),
            )
        elif entity == "theme":
            # id=week_start, label=블록 라벨(B1..B6), value=테마 텍스트
            ws = form.get("id") or ""
            if not _parse_date(ws):
                return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
            label = (form.get("label") or "").strip()
            if not label:
                return JSONResponse({"ok": False, "error": "bad-label"}, status_code=400)
            row = conn.execute("SELECT theme_text FROM weekly_block_themes WHERE week_start = ? AND block_label = ?",
                               (ws, label)).fetchone()
            error = _edit_conflict(form, row["theme_text"] if row else None, value, normalize=str.strip)
            if error is not None:
                return error
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
    if achieve_date and gcal_write.write_enabled("achieve"):
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
def inbox_done(item_id: RowId):
    with get_conn() as conn:
        conn.execute("UPDATE inbox SET done = 1 WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True})


@router.post("/inbox/delete/{item_id}")
def inbox_delete(item_id: RowId):
    """수집함 항목을 완전히 삭제한다(정리 ✓와 달리 DB에서 지움)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM inbox WHERE id = ?", (item_id,))
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
    if not gcal_write.write_enabled("events"):
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
        item_id = int_id(form.get("item_id"))
        block_id = int_id(form.get("block_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        it = conn.execute("SELECT text, done FROM inbox WHERE id = ?", (item_id,)).fetchone()
        blk = conn.execute(
            "SELECT * FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        error = _identity_error(blk, form, block=True)
        if error is not None:
            return error
        if not it:
            return JSONResponse({"ok": False, "error": "not-found"}, status_code=404)
        if it["done"]:
            return JSONResponse({"ok": False, "error": "inbox-already-handled"}, status_code=409)
        cur = (blk["plan_text"] or "").rstrip()
        plan_text = f"{cur}\n{it['text']}" if cur else it["text"]
        conn.execute(
            "UPDATE blocks SET plan_text = ?, updated_at = ? WHERE id = ?",
            (plan_text, now, block_id),
        )
        conn.execute("UPDATE inbox SET done = 1 WHERE id = ?", (item_id,))
    return JSONResponse({"ok": True, "block_id": block_id, "plan_text": plan_text})


_ROLLOVER_KEY = secrets.token_bytes(32)


def _rollover_token(data) -> str:
    body = urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    signature = hmac.new(_ROLLOVER_KEY, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


@router.post("/block/rollover")
async def block_rollover(request: Request):
    """이 블록에 적어 둔 것을 내일 같은 블록으로 옮겨 적는다(미룬 계획 이월).

    넘기는 것은 그 블록 슬롯의 DO 칸과, 블록 PLAN 칸에 적은 글이다. 내일 같은 자리에
    이미 무엇이 적혀 있어도 오늘 것으로 덮어쓴다 — 이월은 '어제 하려던 것을 오늘로
    끌어온다'는 뜻이라, 밀린 계획이 다른 칸으로 밀려나면 시간표가 어긋난다.

    자리는 같은 시작시각을 먼저 노린다. 요일마다 블록 시간이 달라 그 시각이 아예 없으면
    그 블록 안 같은 순번 칸으로 간다. 그 블록이 더 짧아 순번도 없으면 그것만 건너뛰고
    몇 개를 못 넘겼는지 알려 준다. 비워 둔 칸은 보내지 않으므로 내일의 그 칸은 그대로
    남는다. 오늘 칸도 지우지 않는다(이월은 옮기기가 아니라 복사다).

    고정 할일이 채운 칸(is_routine=1)은 넘기지 않는다. 내일도 템플릿이 다시 채우므로
    함께 넘기면 같은 것이 두 칸에 생긴다.

    화면 값(plan, do_<슬롯id>)이 함께 오면 저장값 대신 그것을 쓴다. 버튼을 누르면 방금
    고친 칸에서 blur 가 나며 자동저장이 같이 출발하는데, 두 요청의 도착 순서가 정해져
    있지 않아 예전에는 이월이 먼저 닿아 방금 적은 것을 못 보고 거절했다. 비운 채로
    눌렀으면 빈 값이 맞으므로 저장값으로 되돌리지 않는다.
    """
    form = await request.form()
    try:
        block_id = int_id(form.get("block_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad-id"}, status_code=400)
    mode = form.get("mode", "")
    if mode not in ("", "preview", "apply"):
        return JSONResponse({"ok": False, "error": "bad-mode"}, status_code=400)

    def on_screen(key: str, saved) -> str:
        """화면에서 온 값이 있으면 그것, 없으면 저장값."""
        sent = form.get(key)
        return (sent if sent is not None else (saved or "")).strip()

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        src = conn.execute(
            "SELECT * FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        error = _identity_error(src, form, block=True)
        if error is not None:
            return error
        if form.get("schedule_version") and form["schedule_version"] != schedule_version(
            conn, (src["date"],), include_slots=True
        ):
            return _stale_schedule()
        plan = on_screen("plan", src["plan_text"])
        # 내일로 넘길 (시각, 블록 안 순번, 글) 목록. 순번은 그 블록에서 몇 번째 칸인지다.
        carry = []
        for pos, s in enumerate(conn.execute(
            "SELECT id, start_time, do_text, is_routine FROM slots "
            "WHERE block_id = ? ORDER BY slot_index",
            (block_id,),
        )):
            text = on_screen(f"do_{s['id']}", s["do_text"])
            if s["is_routine"] and text == (s["do_text"] or "").strip():
                continue
            if text:
                carry.append((s["start_time"], pos, text))
        if not plan and not carry:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)

        d = datetime.strptime(src["date"], "%Y-%m-%d").date()
        nxt = (d + timedelta(days=1)).strftime("%Y-%m-%d")
        ensure_day_skeleton(conn, nxt)
        dst = conn.execute(
            "SELECT * FROM blocks WHERE date = ? AND block_label = ?",
            (nxt, src["block_label"]),
        ).fetchone()
        if not dst:
            return JSONResponse({"ok": False, "error": "no-target"}, status_code=404)

        dst_slots = [
            dict(r)
            for r in conn.execute(
                "SELECT id, start_time, end_time, do_text, did_text, done, is_routine "
                "FROM slots WHERE block_id = ? ORDER BY slot_index",
                (dst["id"],),
            )
        ]
        by_time = {r["start_time"]: r for r in dst_slots}
        # 같은 시각을 모두 먼저 예약하고 같은 순번으로 옮길 때는 그 자리가 비었을 때만 복사한다.
        reserved = {by_time[start]["id"] for start, _, _ in carry if start in by_time}
        assignments = []
        skipped = []
        for start_time, pos, text in carry:
            target = by_time.get(start_time)
            if target is None and pos < len(dst_slots) and dst_slots[pos]["id"] not in reserved:
                target = dst_slots[pos]
            if target is None:
                skipped.append({"slot": start_time, "text": text})
                continue
            reserved.add(target["id"])
            assignments.append((start_time, target, text))

        conflicts = []
        if plan and dst["plan_text"] and plan != dst["plan_text"]:
            conflicts.append({"slot": "PLAN", "current": dst["plan_text"],
                              "replacement": plan, "has_actual": False})
        for _, target, text in assignments:
            prior = target["do_text"] or ""
            has_actual = bool(target["done"] or (target["did_text"] or "").strip())
            if text != prior and (prior.strip() or has_actual):
                conflicts.append({"slot": target["start_time"], "current": prior,
                                  "replacement": text, "has_actual": has_actual})
        result = {"ok": True, "date": nxt, "label": dst["block_label"],
                  "block_order": dst["block_order"], "moved": len(assignments),
                  "skipped": len(skipped), "plan": bool(plan)}
        # Preview and confirmation can run before the destination day exists; use logical
        # times and values so temporary IDs do not make otherwise identical previews stale.
        snapshot = [
            src["date"], nxt, src["block_label"], dst["start_time"], dst["end_time"], plan,
            dst["plan_text"],
            [(source, {k: v for k, v in target.items() if k != "id"}, value)
             for source, target, value in assignments],
            skipped,
        ]
        preview_token = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        if mode == "preview":
            conn.rollback()
            return JSONResponse({**result, "preview_token": preview_token, "conflicts": conflicts,
                                 "skipped_slots": skipped})
        if mode == "apply" and form.get("preview_token") != preview_token:
            conn.rollback()
            return JSONResponse({"ok": False, "error": "rollover-changed"}, status_code=409)

        now = datetime.now(KST)
        stamp = now.isoformat(timespec="microseconds")
        undo = {"date": nxt, "stamp": stamp, "expires": (now + timedelta(minutes=5)).timestamp(),
                "version": schedule_version(conn, (nxt,), include_slots=True), "slots": []}
        if plan:
            conn.execute(
                "UPDATE blocks SET plan_text = ?, updated_at = ? WHERE id = ?",
                (plan, stamp, dst["id"]),
            )
            undo["block"] = [dst["id"], dst["plan_text"]]
        for _, target, text in assignments:
            conn.execute("UPDATE slots SET do_text = ?, is_routine = 0, updated_at = ? WHERE id = ?",
                         (text, stamp, target["id"]))
            undo["slots"].append([target["id"], target["do_text"], target["is_routine"]])
        if mode == "apply" and (plan or assignments):
            result["undo_token"] = _rollover_token(undo)
    return JSONResponse(result)


@router.post("/block/rollover/undo")
async def undo_rollover(request: Request):
    form = await request.form()
    body, _, signature = (form.get("undo_token") or "").partition(".")
    expected = hmac.new(_ROLLOVER_KEY, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return JSONResponse({"ok": False, "error": "invalid-undo"}, status_code=400)
    undo = json.loads(urlsafe_b64decode(body))
    now = datetime.now(KST)
    if undo["expires"] < now.timestamp():
        return JSONResponse({"ok": False, "error": "undo-expired"}, status_code=409)
    stamp = now.isoformat(timespec="microseconds")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if undo["version"] != schedule_version(conn, (undo["date"],), include_slots=True):
            return _stale_schedule()
        block = undo.get("block")
        if block is not None:
            saved = conn.execute("UPDATE blocks SET plan_text = ?, updated_at = ? "
                                 "WHERE id = ? AND updated_at = ?",
                                 (block[1], stamp, block[0], undo["stamp"]))
            if not saved.rowcount:
                return JSONResponse({"ok": False, "error": "rollover-changed"}, status_code=409)
        for row_id, text, is_routine in undo["slots"]:
            saved = conn.execute("UPDATE slots SET do_text = ?, is_routine = ?, updated_at = ? "
                                 "WHERE id = ? AND updated_at = ?",
                                 (text, is_routine, stamp, row_id, undo["stamp"]))
            if not saved.rowcount:
                conn.rollback()
                return JSONResponse({"ok": False, "error": "rollover-changed"}, status_code=409)
    return JSONResponse({"ok": True, "date": undo["date"]})


@router.post("/meta/tomorrow-goal")
async def meta_tomorrow_goal(request: Request):
    """하루 마감에서 적은 '내일 가장 중요한 일'을 다음 날 목표 1번에 저장한다."""
    form = await request.form()
    date_str = (form.get("date") or "").strip()
    text = (form.get("text") or "").replace("\r", " ").replace("\n", " ").strip()
    d = _parse_date(date_str)
    if d is None:
        return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
    mode = form.get("mode", "")
    if mode not in ("", "preview", "apply"):
        return JSONResponse({"ok": False, "error": "bad-mode"}, status_code=400)
    nxt = (d + timedelta(days=1)).isoformat()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT today_goal, goal_tags, goal_links FROM daily_meta WHERE date = ?", (nxt,)
        ).fetchone()
        goals, tags, links = (_split3(row[key] if row else None)
                               for key in ("today_goal", "goal_tags", "goal_links"))
        snapshot = [nxt, goals[0], tags[0], links[0], text]
        version = hashlib.sha256(json.dumps(snapshot).encode()).hexdigest()
        if mode == "preview":
            return JSONResponse({"ok": True, "date": nxt, "current": goals[0],
                                 "preview_token": version})
        if mode == "apply" and form.get("preview_token") != version:
            return JSONResponse({"ok": False, "error": "goal-changed"}, status_code=409)
        if goals[0] != text:
            goals[0], tags[0], links[0] = text, "", ""
        fields = ["\n".join(parts) if any(parts) else "" for parts in (goals, tags, links)]
        conn.execute(
            "INSERT INTO daily_meta (date, today_goal, goal_tags, goal_links) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET today_goal = excluded.today_goal, "
            "goal_tags = excluded.goal_tags, goal_links = excluded.goal_links",
            (nxt, *fields),
        )
    return JSONResponse({"ok": True, "date": nxt})


# -- 슬롯 실행 체크 + 실시간 폴링 -------------------------------------------


@router.post("/slot/done/{slot_id}")
async def slot_done(slot_id: RowId, request: Request):
    """DO 옆 체크박스. 즉시 저장(폼 저장과 별개)."""
    form = await request.form()
    val = 1 if (form.get("done") in ("1", "true", "on")) else 0
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT s.date, s.start_time, s.end_time, s.done, b.block_label "
            "FROM slots s JOIN blocks b ON b.id = s.block_id WHERE s.id = ?", (slot_id,),
        ).fetchone()
        error = _identity_error(row, form)
        if error is not None:
            return error
        error = _edit_conflict(form, row["done"], val, done=val)
        if error is not None:
            return error
        conn.execute(
            "UPDATE slots SET done = ?, updated_at = ? WHERE id = ?",
            (val, now, slot_id),
        )
    return JSONResponse({"ok": True, "done": val})


@router.get("/api/day/{date_str}")
def api_day(date_str: str):
    """현재 캘린더·Things 아젠다를 JSON으로. 클라이언트가 주기적으로 폴링해 갱신."""
    d = _parse_date(date_str)
    if d is None:
        return JSONResponse({"ok": False, "error": "bad-date"}, status_code=400)
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
                    # 주간 띠 할일 칩을 things:///show?id= 로 다시 걸기 위해 함께 보낸다.
                    "id": t.get("id", ""),
                }
                for t in task_list
            ],
            "blocks": blocks_json,
        }
    )
