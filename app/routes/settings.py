# 설정·데이터 화면(구분·세션시간·연동·.env·백업·내보내기·재시작)을 담당하는 라우터
import json
import os
import re
import signal
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.common import BASE_DIR, CORE_LABELS, KST, KO_WEEKDAYS, _off_loop, templates, today_str
from app.config import (
    BACKUP_DIR,
    CLOUD_BACKUP_DIR,
    DAY_BLOCKS,
    TONE_KEYS,
    TONES,
    hhmm_to_min,
)
from app.db import (
    BLOCK_TIMES_KEY,
    BLOCK_TIMES_WD_KEY,
    get_conn,
    get_day_blocks,
    get_settings,
    get_weekday_overrides,
    set_setting,
)
from app.integrations import ai, gcal_write

router = APIRouter()


# -- 설정 -------------------------------------------------------------------


def _backup_status() -> list[dict]:
    """로컬·클라우드 백업 폴더의 최신 .sql 덤프 상태(파일명·크기KB·경과일)를 돌려준다."""
    today = datetime.now(KST).date()
    out = []
    for label, d in (("로컬", BACKUP_DIR), ("클라우드", CLOUD_BACKUP_DIR)):
        info = {"label": label, "ok": False, "name": "없음", "kb": 0, "age": None}
        try:
            files = sorted(d.glob("blocks-*.sql"))  # 파일명이 YYYYMMDD라 사전식=시간순
            if files:
                latest = files[-1]
                info["ok"] = True
                info["name"] = latest.name
                info["kb"] = round(latest.stat().st_size / 1024)
                m = re.match(r"blocks-(\d{8})\.sql", latest.name)
                if m:
                    fd = datetime.strptime(m.group(1), "%Y%m%d").date()
                    info["age"] = (today - fd).days
        except Exception:
            pass
        out.append(info)
    return out


def _load_cat_templates(conn) -> list[dict]:
    """구분 템플릿 목록을 셀(요일 0~6 × 코어블록 → 구분)까지 채워 돌려준다."""
    templates_ = [
        dict(r)
        for r in conn.execute(
            "SELECT id, name, display_order FROM cat_template "
            "ORDER BY display_order, id"
        )
    ]
    cmap: dict[int, dict[int, dict[str, int]]] = {}
    for r in conn.execute(
        "SELECT template_id, weekday, block_label, category_id FROM cat_template_cell"
    ):
        cmap.setdefault(r["template_id"], {}).setdefault(r["weekday"], {})[
            r["block_label"]
        ] = r["category_id"]
    for t in templates_:
        t["cells"] = cmap.get(t["id"], {})
    return templates_


@router.get("/settings")
def settings_view(request: Request):
    settings = get_settings()
    with get_conn() as conn:
        cats = conn.execute(
            "SELECT id, name, tone, is_active FROM categories "
            "ORDER BY is_active DESC, display_order"
        ).fetchall()
        cat_templates = _load_cat_templates(conn)
    active_categories = [dict(c) for c in cats if c["is_active"]]
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "categories": [dict(c) for c in cats],
            "active_categories": active_categories,
            "cat_templates": cat_templates,
            "core_labels": CORE_LABELS,
            "weekdays": list(enumerate(KO_WEEKDAYS)),
            "tones": TONES,
            "settings": settings,
            "block_scopes": _block_scopes(),
            "events_calendar_id": gcal_write.events_calendar_id(),
            "gcal_events_on": gcal_write.events_enabled(),
            "achieve_calendar_id": gcal_write.achieve_calendar_id(),
            "gcal_achieve_on": gcal_write.achieve_enabled(),
            "sa_email": gcal_write.service_account_email(),
            "ai_status": ai.status(),
            "env_path": str(_env_file_path()),
            # 시크릿은 가려서 보낸다. 실제 값은 서버 파일에만 있고 화면에는 나가지 않는다.
            "env_content": _mask_env_text(_read_env_text()),
        },
    )


def _data_summary() -> dict:
    """데이터 탭 요약(기록 일수·슬롯 수·기간·미처리 수집함·활성 구분)."""
    with get_conn() as conn:
        rec_filter = "(do_text IS NOT NULL AND TRIM(do_text) != '') OR done = 1"
        rec_days = conn.execute(
            f"SELECT COUNT(DISTINCT date) FROM slots WHERE {rec_filter}"
        ).fetchone()[0]
        slot_recs = conn.execute(
            f"SELECT COUNT(*) FROM slots WHERE {rec_filter}"
        ).fetchone()[0]
        span = conn.execute(
            f"SELECT MIN(date), MAX(date) FROM slots WHERE {rec_filter}"
        ).fetchone()
        inbox_open = conn.execute(
            "SELECT COUNT(*) FROM inbox WHERE done = 0"
        ).fetchone()[0]
        active_cats = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE is_active = 1"
        ).fetchone()[0]
    return {
        "rec_days": rec_days,
        "slot_recs": slot_recs,
        "first": span[0] or "-",
        "last": span[1] or "-",
        "inbox_open": inbox_open,
        "active_cats": active_cats,
    }


@router.get("/data")
def data_view(request: Request):
    """데이터 탭: 요약·백업·내보내기·삭제(설정에서 분리, 화면 2분할)."""
    return templates.TemplateResponse(
        "data.html",
        {
            "request": request,
            "summary": _data_summary(),
            "backup_status": _backup_status(),
            "today": today_str(),
        },
    )


def _block_scopes() -> list[dict]:
    """세션 시간 편집 범위 8개(공통 + 월~일). 덮어쓰지 않은 요일은 공통 값을 그대로 보여준다."""
    overrides = get_weekday_overrides()
    scopes = [{"key": "", "label": "공통", "sub": "모든 요일 기본", "overridden": False}]
    for i in range(7):
        scopes.append({
            "key": str(i), "label": KO_WEEKDAYS[i], "sub": f"{KO_WEEKDAYS[i]}요일",
            "overridden": bool(overrides.get(str(i))),
        })
    for sc in scopes:
        blocks = get_day_blocks(int(sc["key"]) if sc["key"] else None)
        sc["rows"] = [
            {"order": i, "label": lbl, "is_core": core, "start": s, "end": e}
            for i, (lbl, core, s, e) in enumerate(blocks)
        ]
    return scopes


def _valid_hhmm(s: str) -> bool:
    """'HH:MM' 이고 00:00~24:00 범위인지. 분은 자유 — 세션 30분 단위는 블록 길이(30분 배수)로 보장한다."""
    if not re.match(r"^\d{2}:\d{2}$", s or ""):
        return False
    h, m = int(s[:2]), int(s[3:5])
    return 0 <= h <= 24 and 0 <= m <= 59 and (h * 60 + m) <= 24 * 60


def _parse_scope(raw) -> tuple[bool, int | None]:
    """세션 시간 편집 범위 입력값을 (유효한가, 요일 또는 None) 으로. ''=공통, '0'~'6'=요일."""
    s = (raw or "").strip()
    if not s:
        return True, None
    if s.isdigit() and 0 <= int(s) <= 6:
        return True, int(s)
    return False, None


@router.post("/settings/blocktimes")
async def settings_blocktimes(request: Request):
    """8블록의 시작·끝 시간을 저장한다(라벨·코어여부·개수 고정). 30분 경계·겹침을 검증한다.

    scope 가 비면 공통(모든 요일 기본), '0'~'6' 이면 그 요일만 덮어쓴다.
    """
    form = await request.form()
    ok_scope, weekday = _parse_scope(form.get("scope"))
    if not ok_scope:
        return JSONResponse({"ok": False, "error": "요일 값이 잘못됨"}, status_code=400)
    n = len(DAY_BLOCKS)
    times = []
    prev_end = None
    for i in range(n):
        s = (form.get(f"start_{i}") or "").strip()
        e = (form.get(f"end_{i}") or "").strip()
        label = DAY_BLOCKS[i][0]
        if not _valid_hhmm(s) or not _valid_hhmm(e):
            return JSONResponse(
                {"ok": False, "error": f"{label} 시간 형식이 잘못됨(HH:MM)"},
                status_code=400,
            )
        if hhmm_to_min(s) >= hhmm_to_min(e):
            return JSONResponse(
                {"ok": False, "error": f"{label}: 시작이 끝보다 빨라야 합니다"},
                status_code=400,
            )
        if (hhmm_to_min(e) - hhmm_to_min(s)) % 30 != 0:
            return JSONResponse(
                {"ok": False, "error": f"{label}: 블록 길이가 30분 단위여야 합니다(세션 30분 유지)"},
                status_code=400,
            )
        if prev_end is not None and hhmm_to_min(s) < prev_end:
            return JSONResponse(
                {"ok": False, "error": f"{label}이 앞 블록과 겹칩니다"}, status_code=400
            )
        prev_end = hhmm_to_min(e)
        times.append({"start": s, "end": e})
    if weekday is None:
        set_setting(BLOCK_TIMES_KEY, json.dumps(times))
    else:
        overrides = get_weekday_overrides()
        overrides[str(weekday)] = times
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))
    return JSONResponse({"ok": True, "scope": "" if weekday is None else str(weekday)})


@router.post("/settings/blocktimes/reset")
async def settings_blocktimes_reset(request: Request):
    """공통은 기본 시간표로, 요일은 덮어쓰기를 지워 공통을 따르게 되돌린다."""
    form = await request.form()
    ok_scope, weekday = _parse_scope(form.get("scope"))
    if not ok_scope:
        return JSONResponse({"ok": False, "error": "요일 값이 잘못됨"}, status_code=400)
    if weekday is None:
        set_setting(BLOCK_TIMES_KEY, "")
    else:
        overrides = get_weekday_overrides()
        overrides.pop(str(weekday), None)
        set_setting(BLOCK_TIMES_WD_KEY, json.dumps(overrides))
    return JSONResponse({"ok": True})


@router.post("/settings/events-calendar")
async def settings_events_calendar(request: Request):
    """오늘 탭 일정 쓰기용 구글 캘린더 ID를 저장한다(빈 값이면 일정 쓰기 해제)."""
    form = await request.form()
    value = (form.get("value") or "").strip()
    set_setting("gcal_events_calendar_id", value)
    return JSONResponse({"ok": True, "enabled": gcal_write.events_enabled()})


@router.post("/settings/events-calendar/test")
async def settings_events_calendar_test():
    """저장된 일정용 캘린더에 테스트 이벤트를 만들고 지워 연결을 확인한다."""
    return JSONResponse(await _off_loop(gcal_write.test_events_write))


@router.post("/settings/achieve-calendar")
async def settings_achieve_calendar(request: Request):
    """오늘 '달성'을 쓸 성과 캘린더 ID를 저장한다(빈 값이면 성과 쓰기 해제)."""
    form = await request.form()
    value = (form.get("value") or "").strip()
    set_setting("gcal_achieve_calendar_id", value)
    return JSONResponse({"ok": True, "enabled": gcal_write.achieve_enabled()})


@router.post("/settings/achieve-calendar/test")
async def settings_achieve_calendar_test():
    """저장된 성과 캘린더에 테스트 이벤트를 만들고 지워 연결을 확인한다."""
    return JSONResponse(await _off_loop(gcal_write.test_achieve_write))


@router.post("/settings/category/add")
async def settings_cat_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    tone = (form.get("tone") or "black").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    if tone not in TONE_KEYS:
        tone = "black"
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
        if row:  # 같은 이름이 있으면(비활성 포함) 다시 활성화하고 톤만 갱신
            conn.execute(
                "UPDATE categories SET is_active = 1, tone = ? WHERE id = ?",
                (tone, row["id"]),
            )
            cid = row["id"]
        else:
            order = conn.execute(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM categories"
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO categories (name, tone, display_order, is_active) "
                "VALUES (?, ?, ?, 1)",
                (name, tone, order),
            )
            cid = cur.lastrowid
    return JSONResponse({"ok": True, "id": cid, "name": name, "tone": tone})


@router.post("/settings/category/update")
async def settings_cat_update(request: Request):
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    fields = {}
    if form.get("name") is not None and (form.get("name") or "").strip():
        fields["name"] = form.get("name").strip()
    if form.get("tone") in TONE_KEYS:
        fields["tone"] = form.get("tone")
    if form.get("is_active") is not None:
        fields["is_active"] = 1 if form.get("is_active") in ("1", "true", "on") else 0
    if not fields:
        return JSONResponse({"ok": False}, status_code=400)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE categories SET {sets} WHERE id = ?", (*fields.values(), cid)
        )
    return JSONResponse({"ok": True})


@router.post("/settings/category/move")
async def settings_cat_move(request: Request):
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    direction = form.get("dir")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, display_order FROM categories WHERE is_active = 1 "
            "ORDER BY display_order"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if cid not in ids:
            return JSONResponse({"ok": False}, status_code=404)
        i = ids.index(cid)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(rows):
            a, b = rows[i], rows[j]
            conn.execute(
                "UPDATE categories SET display_order = ? WHERE id = ?",
                (b["display_order"], a["id"]),
            )
            conn.execute(
                "UPDATE categories SET display_order = ? WHERE id = ?",
                (a["display_order"], b["id"]),
            )
    return JSONResponse({"ok": True})


@router.post("/settings/category/delete")
async def settings_cat_delete(request: Request):
    """카테고리를 숨김 처리한다(소프트 삭제). 슬롯·블록의 기존 참조는 보존된다."""
    form = await request.form()
    try:
        cid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE categories SET is_active = 0 WHERE id = ?", (cid,))
    return JSONResponse({"ok": True})


@router.post("/settings/save")
async def settings_save(request: Request):
    form = await request.form()
    allowed = {"start_view", "default_theme", "pomo_auto", "pomo_warn5", "collapse_blocks",
               "show_location", "show_did", "show_reflect", "show_inbox"}
    for key in allowed:
        if form.get(key) is not None:
            set_setting(key, form.get(key))
    return JSONResponse({"ok": True})


# -- 구분 템플릿 (설정 탭) --------------------------------------------------

@router.post("/settings/template/add")
async def settings_template_add(request: Request):
    """새 구분 템플릿을 빈 상태로 추가하고 생성된 id를 돌려준다."""
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "no-name"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        order = conn.execute(
            "SELECT COALESCE(MAX(display_order), -1) + 1 FROM cat_template"
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO cat_template (name, display_order, updated_at) "
            "VALUES (?, ?, ?)",
            (name, order, now),
        )
    return JSONResponse({"ok": True, "id": cur.lastrowid})


@router.post("/settings/template/rename")
async def settings_template_rename(request: Request):
    """구분 템플릿 이름을 바꾼다."""
    form = await request.form()
    try:
        tid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "no-name"}, status_code=400)
    now = datetime.now(KST).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "UPDATE cat_template SET name = ?, updated_at = ? WHERE id = ?",
            (name, now, tid),
        )
    return JSONResponse({"ok": True})


@router.post("/settings/template/delete")
async def settings_template_delete(request: Request):
    """구분 템플릿과 그 셀을 함께 삭제한다."""
    form = await request.form()
    try:
        tid = int(form.get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    with get_conn() as conn:
        conn.execute("DELETE FROM cat_template WHERE id = ?", (tid,))
    return JSONResponse({"ok": True})


@router.post("/settings/template/cell")
async def settings_template_cell(request: Request):
    """템플릿 한 칸(요일 0~6 × 코어블록)의 구분을 저장한다. 값이 비면 미지정."""
    form = await request.form()
    try:
        tid = int(form.get("template_id"))
        weekday = int(form.get("weekday"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False}, status_code=400)
    label = (form.get("block_label") or "").strip()
    if not (0 <= weekday <= 6) or label not in CORE_LABELS:
        return JSONResponse({"ok": False, "error": "bad-cell"}, status_code=400)
    raw = form.get("category_id")
    cid = int(raw) if raw else None
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cat_template_cell "
            "(template_id, weekday, block_label, category_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(template_id, weekday, block_label) DO UPDATE SET "
            "category_id = excluded.category_id",
            (tid, weekday, label, cid),
        )
    return JSONResponse({"ok": True})


# -- .env 편집 (설정 탭) ----------------------------------------------------

# 설정 탭의 .env 편집기. 값은 서버 재시작 후 반영된다(config.py가 기동 시 load_dotenv).
# 이 앱에는 로그인이 없으므로 API 키가 화면·브라우저 캐시에 남지 않도록 값은 ******** 로
# 가려서 내보내고, 그대로 돌아온 자리표시는 저장할 때 기존 값으로 되돌린다.
# 값을 바꿀 때는 자리표시를 지우고 새 값을 적으면 된다. 백업은 레포 밖(6block-data)에 둔다.
def _env_file_path() -> Path:
    """프로젝트 루트의 .env 경로."""
    return BASE_DIR.parent / ".env"


MASK = "********"          # 가려진 값 자리표시. 저장할 때 이 값이면 기존 값을 그대로 둔다.


def _read_env_text() -> str:
    """.env 내용을 문자열로 읽는다(없으면 빈 문자열)."""
    try:
        return _env_file_path().read_text(encoding="utf-8")
    except OSError:
        return ""


def _mask_env_text(text: str) -> str:
    """KEY=값 의 값을 가린다. 화면·브라우저 캐시·화면 공유에 시크릿이 그대로 남지 않게 한다."""
    out = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, _, value = line.partition("=")
        out.append(f"{key}={MASK}" if value.strip() else line)
    return "\n".join(out)


def _unmask_env_text(new_text: str, old_text: str) -> str:
    """가려진 채로 돌아온 값(********)을 기존 .env의 실제 값으로 되돌린다."""
    old_values: dict[str, str] = {}
    for line in old_text.split("\n"):
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            old_values[k.strip()] = v
    out = []
    for line in new_text.split("\n"):
        key, _, value = line.partition("=")
        if value.strip() == MASK and key.strip() in old_values:
            out.append(f"{key}={old_values[key.strip()]}")
        else:
            out.append(line)
    return "\n".join(out)


@router.post("/settings/env/save")
async def settings_env_save(request: Request):
    """.env 전체 내용을 저장한다. 직전 내용을 6block-data에 백업하고 임시파일로 원자적
    교체하며 권한 0o600을 유지한다. 저장 후 서버를 재시작해야 값이 반영된다.

    화면에는 값이 가려진 채로 나가므로, 그대로 돌아온 자리표시는 기존 값으로 되돌린다.
    """
    form = await request.form()
    content = form.get("content")
    if content is None:
        return JSONResponse({"ok": False, "error": "no-content"}, status_code=400)
    if len(content) > 100_000:
        return JSONResponse({"ok": False, "error": "too-large"}, status_code=400)
    text = _unmask_env_text(content.replace("\r\n", "\n"), _read_env_text())
    env_path = _env_file_path()
    tmp = env_path.with_name(".env.tmp")
    try:
        if env_path.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            bak = BACKUP_DIR / ".env.bak"       # 레포 밖(6block-data/backups)에 백업
            bak.write_bytes(env_path.read_bytes())
            os.chmod(bak, 0o600)
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, env_path)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


# -- 서버 재시작 (launchd) --------------------------------------------------

# 이 앱은 launchd 서비스(io.6block.uvicorn, KeepAlive)로 상시 구동된다. 설정의 재시작
# 버튼이 이 엔드포인트를 부르면 응답을 먼저 돌려준 뒤 약 1초 뒤 자기 자신에게 SIGTERM을
# 보낸다. 정상 종료(SIGKILL 아님)라 SQLite 연결·WAL이 깨끗이 닫히고, launchd가 KeepAlive로
# 새 프로세스를 띄워 코드·.env 변경을 반영한다.
@router.post("/settings/restart")
async def settings_restart():
    """이 서버를 재시작한다(응답 후 SIGTERM 자기 종료 → launchd가 KeepAlive로 재기동)."""
    threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return JSONResponse({"ok": True})


# -- AI 연결 (선택) --------------------------------------------------------


@router.post("/settings/ai/save")
async def settings_ai_save(request: Request):
    """AI 연결의 base URL·모델을 저장한다(키는 보안상 .env AI_API_KEY로만 관리)."""
    form = await request.form()
    set_setting("ai_base_url", (form.get("base_url") or "").strip())
    set_setting("ai_model", (form.get("model") or "").strip())
    return JSONResponse({"ok": True, "status": ai.status()})


@router.post("/settings/ai/test")
async def settings_ai_test():
    """현재 설정으로 AI에 짧은 호출을 보내 연결을 확인한다."""
    if not ai.enabled():
        return JSONResponse(
            {"ok": False, "error": ".env의 AI_API_KEY와 base URL·모델을 확인하세요"}
        )
    reply = await _off_loop(ai.complete, "You reply with a single word.",
                        "Reply with the word OK.", max_tokens=5, temperature=0)
    if reply:
        return JSONResponse({"ok": True, "reply": reply[:40]})
    return JSONResponse(
        {"ok": False, "error": "호출 실패 · 키·주소·모델·잔액을 확인하세요"}
    )


@router.post("/settings/backup")
def settings_backup():
    """scripts/backup.py를 즉시 실행해 .sql 덤프를 만든다."""
    try:
        import importlib.util

        path = BASE_DIR.parent / "scripts" / "backup.py"
        spec = importlib.util.spec_from_file_location("backup", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.dump()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)


@router.get("/settings/export.csv")
def settings_export(start: str, end: str):
    """기간 내 슬롯 기록을 CSV로 내보낸다(엑셀 호환 UTF-8 BOM)."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["날짜", "블록", "블록이름", "시각", "구분", "DO(계획)", "한일(실제)",
                "완료", "블록PLAN", "블록SEE"])
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT s.date, b.block_label, b.name AS bname, s.start_time, c.name AS cat, "
            "       s.do_text, s.did_text, s.done, b.plan_text, b.see_text "
            "FROM slots s JOIN blocks b ON b.id = s.block_id "
            "LEFT JOIN categories c ON c.id = COALESCE(s.category_id, b.category_id) "
            "WHERE s.date BETWEEN ? AND ? ORDER BY s.date, s.slot_index",
            (start, end),
        ):
            w.writerow([
                r["date"], r["block_label"], r["bname"] or "", r["start_time"],
                r["cat"] or "", r["do_text"] or "", r["did_text"] or "", r["done"],
                r["plan_text"] or "", r["see_text"] or "",
            ])
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=6block-{start}_{end}.csv"},
    )


@router.post("/settings/purge")
async def settings_purge(request: Request):
    """기간 내 기록(슬롯·블록·일 메타)을 삭제한다. 되돌릴 수 없다."""
    form = await request.form()
    start = (form.get("start") or "").strip()
    end = (form.get("end") or "").strip()
    if not start or not end:
        return JSONResponse({"ok": False, "error": "기간 필요"}, status_code=400)
    with get_conn() as conn:
        conn.execute("DELETE FROM slots WHERE date BETWEEN ? AND ?", (start, end))
        conn.execute("DELETE FROM blocks WHERE date BETWEEN ? AND ?", (start, end))
        conn.execute("DELETE FROM daily_meta WHERE date BETWEEN ? AND ?", (start, end))
    return JSONResponse({"ok": True})
