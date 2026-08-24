# 분석·검색 화면(지표·퍼널·개선점·기록 검색·예전 오늘)을 담당하는 라우터
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.common import (
    KO_WEEKDAYS,
    KST,
    SLOT_HAS_CONTENT,
    _ko_weekday,
    _like_pattern,
    _off_loop,
    _short_date,
    templates,
)
from app.db import get_conn
from app.integrations import ai

router = APIRouter()


# -- 분석 -------------------------------------------------------------------


def _calc_streak(rec_dates: set, today: date) -> int:
    """오늘(기록 없으면 어제)부터 거꾸로 연속으로 기록이 있는 날 수를 센다."""
    if not rec_dates:
        return 0
    cur = today
    if cur.strftime("%Y-%m-%d") not in rec_dates:
        cur = today - timedelta(days=1)
    streak = 0
    while cur.strftime("%Y-%m-%d") in rec_dates:
        streak += 1
        cur = cur - timedelta(days=1)
    return streak


def _on_this_day(today: date):
    """예전 오늘(어제·1주·한 달·1년 전)의 한 일(슬롯)과 고결감을 모아 회고용으로 돌려준다."""
    points = [(1, "어제"), (7, "1주 전"), (30, "한 달 전"), (365, "1년 전")]
    out = []
    with get_conn() as conn:
        for off, label in points:
            d = (today - timedelta(days=off)).strftime("%Y-%m-%d")
            slots = [
                dict(r) for r in conn.execute(
                    "SELECT b.block_label, s.start_time, s.do_text, s.did_text "
                    "FROM slots s JOIN blocks b ON b.id = s.block_id "
                    "WHERE s.date = ? AND (TRIM(COALESCE(s.do_text,'')) != '' "
                    "OR TRIM(COALESCE(s.did_text,'')) != '') ORDER BY s.slot_index LIMIT 10",
                    (d,),
                )
            ]
            refls = [
                dict(r) for r in conn.execute(
                    "SELECT kind, title, text FROM reflection WHERE event_date = ? "
                    "ORDER BY id DESC LIMIT 10",
                    (d,),
                )
            ]
            if slots or refls:
                out.append({"label": label, "date": d, "slots": slots, "reflections": refls})
    return out


def _build_insights(summary, weekday_data, block_pd, cats) -> list[str]:
    """축적 데이터에서 규칙기반 개선점 문장을 만든다(근거가 충분한 항목만)."""
    out: list[str] = []
    wd_valid = [w for w in weekday_data if w["planned"] >= 3]
    if wd_valid:
        worst = min(wd_valid, key=lambda w: w["pct"])
        best = max(wd_valid, key=lambda w: w["pct"])
        if worst["pct"] + 15 <= best["pct"]:
            out.append(
                f"{worst['label']}요일 완료율이 {worst['pct']}%로 가장 낮습니다"
                f"(최고 {best['label']} {best['pct']}%). 그 요일 계획을 줄이거나 "
                f"쉬운 일부터 배치해 보세요."
            )
    bp_valid = [b for b in block_pd if b["planned"] >= 3]
    if bp_valid:
        worst_b = min(bp_valid, key=lambda b: b["pct"])
        if worst_b["pct"] < 60:
            out.append(
                f"{worst_b['label']} 블록이 계획 대비 실행 {worst_b['pct']}%로 가장 "
                f"낮습니다. 그 시간대에 무리한 계획을 잡고 있지 않은지 살펴보세요."
            )
    if cats and cats[0]["pct"] >= 40:
        out.append(
            f"'{cats[0]['name']}' 구분이 전체 시간의 {cats[0]['pct']}%로 가장 큽니다. "
            f"구분 배분이 목표와 맞는지 돌아보세요."
        )
    if summary["pd_pct"] and summary["pd_pct"] < 50:
        out.append(
            f"코어 블록 계획→실행이 {summary['pd_pct']}%입니다. 계획을 줄여 "
            f"실행률부터 올리는 편이 낫습니다."
        )
    if summary["avg_done"] >= 80:
        out.append(
            f"평균 완료율 {summary['avg_done']}%로 잘 지키고 있습니다. "
            f"계획량을 조금 늘려도 좋습니다."
        )
    if not out:
        out.append("아직 개선점을 뽑을 만큼 데이터가 충분하지 않습니다. 기록을 더 쌓아 보세요.")
    return out


def _ai_insights(summary, weekday_data, block_pd, cats) -> str | None:
    """AI로 지표를 요약한 짧은 개선 제안. 실패·미설정 시 None."""
    wd = ", ".join(f"{w['label']} {w['pct']}%" for w in weekday_data if w["planned"])
    bp = ", ".join(f"{b['label']} {b['pct']}%" for b in block_pd)
    ct = ", ".join(f"{c['name']} {c['pct']}%" for c in cats[:5])
    metrics = (
        f"평균 완료율 {summary['avg_done']}%, 코어 계획→실행 {summary['pd_pct']}%, "
        f"연속기록 {summary['streak']}일.\n요일별 완료율: {wd or '자료 부족'}.\n"
        f"블록별 계획대비 실행: {bp or '자료 부족'}.\n구분 배분: {ct or '자료 부족'}."
    )
    system = ("당신은 개인 시간관리 코치입니다. 아래 지표를 보고 한국어로 구체적이고 "
              "실천 가능한 개선 제안을 2~3가지, 각 한 문장으로 제시합니다. 군더더기 없이.")
    return ai.complete(system, metrics, max_tokens=400, temperature=0.5)


def _exec_funnel(conn, start, end):
    """실행 퍼널: 코어 블록 계획(구분) → 슬롯 구체화(DO) → 슬롯 실행(done·한일) 3단계 비율과,
    실행 점수(3단계 곱)·실질 실행율(실행 슬롯/전체 코어 슬롯)을 [start,end] 기간으로 계산한다.
    계획된 블록 = 구분(category_id)을 넣은 코어 블록. 실행 = done 체크 또는 '한일'(did_text) 기록.
    고정 할일이 채운 칸(is_routine=1)은 사람이 세운 계획이 아니므로 구체화에서 뺀다."""
    b = conn.execute(
        "SELECT COUNT(*) AS core_blocks, "
        "SUM(CASE WHEN category_id IS NOT NULL THEN 1 ELSE 0 END) AS designed_blocks "
        "FROM blocks WHERE is_core = 1 AND date >= ? AND date <= ?",
        (start, end),
    ).fetchone()
    s = conn.execute(
        "SELECT COUNT(*) AS slots_in_designed, "
        "SUM(CASE WHEN TRIM(COALESCE(s.do_text,'')) != '' AND s.is_routine = 0 "
        "         THEN 1 ELSE 0 END) AS detailed_slots, "
        "SUM(CASE WHEN TRIM(COALESCE(s.do_text,'')) != '' AND s.is_routine = 0 "
        "         AND (s.done = 1 OR TRIM(COALESCE(s.did_text,'')) != '') THEN 1 ELSE 0 END) AS executed_detailed "
        "FROM slots s JOIN blocks b ON b.id = s.block_id "
        "WHERE b.is_core = 1 AND b.category_id IS NOT NULL "
        "  AND s.date >= ? AND s.date <= ?",
        (start, end),
    ).fetchone()
    a = conn.execute(
        "SELECT COUNT(*) AS core_slots, "
        "SUM(CASE WHEN s.done = 1 OR TRIM(COALESCE(s.did_text,'')) != '' THEN 1 ELSE 0 END) AS done_slots "
        "FROM slots s JOIN blocks b ON b.id = s.block_id "
        "WHERE b.is_core = 1 AND s.date >= ? AND s.date <= ?",
        (start, end),
    ).fetchone()
    core_blocks = b["core_blocks"] or 0
    designed = b["designed_blocks"] or 0
    slots_in_designed = s["slots_in_designed"] or 0
    detailed = s["detailed_slots"] or 0
    executed = s["executed_detailed"] or 0
    core_slots = a["core_slots"] or 0
    done_slots = a["done_slots"] or 0
    design_r = designed / core_blocks if core_blocks else 0
    detail_r = detailed / slots_in_designed if slots_in_designed else 0
    exec_r = executed / detailed if detailed else 0
    return {
        "core_blocks": core_blocks, "designed_blocks": designed,
        "slots_in_designed": slots_in_designed, "detailed_slots": detailed,
        "executed_detailed": executed, "core_slots": core_slots, "done_slots": done_slots,
        "design_pct": round(design_r * 100), "detail_pct": round(detail_r * 100),
        "exec_pct": round(exec_r * 100),
        "exec_score": round(design_r * detail_r * exec_r * 100),
        "real_exec": round(done_slots / core_slots * 100) if core_slots else 0,
    }


def _analytics_data(rng: str) -> dict:
    """분석 지표를 한 번에 계산한다. 화면(/analytics)과 AI 제안(/analytics/ai)이 함께 쓴다."""
    today = datetime.now(KST).date()
    today_s = today.strftime("%Y-%m-%d")
    with get_conn() as conn:
        if rng == "all":
            row = conn.execute("SELECT MIN(date) FROM slots").fetchone()
            start = row[0] or today_s
            range_label = "전체"
        else:
            rng = "30" if rng == "30" else "7"
            days = int(rng)
            start = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
            range_label = f"최근 {days}일"
        # 슬롯 구분이 비면 블록 구분을 따라 집계한다(블록→슬롯 상속).
        # 주간 탭과 같은 기준으로 '내용이 있는 슬롯'만 세고, 구분이 없는 시간은
        # LEFT JOIN 으로 '미지정' 한 줄에 모은다(두 화면의 총 시간이 갈리지 않게).
        cat_rows = conn.execute(
            "SELECT COALESCE(c.name, '미지정') AS name, "
            "       COALESCE(c.tone, 'gray') AS tone, COUNT(s.id) AS cnt "
            "FROM slots s JOIN blocks b ON b.id = s.block_id "
            "LEFT JOIN categories c ON c.id = COALESCE(s.category_id, b.category_id) "
            f"WHERE s.date >= ? AND s.date <= ? AND {SLOT_HAS_CONTENT} "
            "GROUP BY c.id ORDER BY cnt DESC",
            (start, today_s),
        ).fetchall()
        # 날짜별 완료율. HAVING 으로 '내용이 있는 날'만 남긴다.
        #
        # 화면을 한 번 열기만 해도 그날 골격(블록·슬롯)이 만들어진다. 주간 탭은 7일치를
        # 한꺼번에 만든다. 그래서 예전에는 아무것도 안 적은 날까지 여기에 줄줄이 섞여,
        # '기록한 날'이 실제로는 열어만 본 날 수가 되고(빈 주에 6일), 그 0% 짜리 날들이
        # 평균 완료율까지 끌어내렸다(하루를 100% 채워도 17%). 추세 막대에도 0% 기둥이
        # 늘어서 '그날 다 흘려보냈다'처럼 보였다.
        day_rows = conn.execute(
            "SELECT date, "
            "SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done_cnt, "
            # 고정 할일 칸은 계획으로 세지 않는다. 다만 체크(done)한 칸은 실행 막대가
            # 계획 막대를 넘지 않도록 계획에도 넣는다.
            "SUM(CASE WHEN (is_routine = 0 AND ((do_text IS NOT NULL AND TRIM(do_text) != '') "
            "                                   OR category_id IS NOT NULL)) "
            "         OR done = 1 THEN 1 ELSE 0 END) AS planned_cnt "
            "FROM slots s WHERE date >= ? AND date <= ? GROUP BY date "
            f"HAVING SUM(CASE WHEN {SLOT_HAS_CONTENT} THEN 1 ELSE 0 END) > 0 "
            "ORDER BY date",
            (start, today_s),
        ).fetchall()
        pd_rows = conn.execute(
            "SELECT b.date, COUNT(*) AS planned, "
            "SUM(CASE WHEN EXISTS(SELECT 1 FROM slots s WHERE s.block_id = b.id "
            "    AND ((s.do_text IS NOT NULL AND TRIM(s.do_text) != '' AND s.is_routine = 0) "
            "         OR s.done = 1)) "
            "    THEN 1 ELSE 0 END) AS achieved "
            "FROM blocks b WHERE b.is_core = 1 AND TRIM(COALESCE(b.plan_text, '')) != '' "
            "  AND b.date >= ? AND b.date <= ? GROUP BY b.date ORDER BY b.date",
            (start, today_s),
        ).fetchall()
        # 블록별(B1~B6) 계획 대비 실행: 어느 시간대를 반복적으로 흘려보내는지 본다.
        block_pd_rows = conn.execute(
            "SELECT b.block_label AS lbl, MIN(b.block_order) AS ord, COUNT(*) AS planned, "
            "SUM(CASE WHEN EXISTS(SELECT 1 FROM slots s WHERE s.block_id = b.id "
            "    AND ((s.do_text IS NOT NULL AND TRIM(s.do_text) != '' AND s.is_routine = 0) "
            "         OR s.done = 1)) "
            "    THEN 1 ELSE 0 END) AS achieved "
            "FROM blocks b WHERE b.is_core = 1 AND TRIM(COALESCE(b.plan_text, '')) != '' "
            "  AND b.date >= ? AND b.date <= ? GROUP BY b.block_label ORDER BY ord",
            (start, today_s),
        ).fetchall()
        # 연속 기록(streak)용. 기간과 무관하게 전체 기록일이 필요하다.
        # 판정은 위 HAVING·주간 '기록된 시간'과 같은 SLOT_HAS_CONTENT 하나를 쓴다.
        # 예전에는 여기만 '한 일(did_text)'을 빼고 세어서, 같은 화면의 세 숫자가
        # 저마다 다른 기준으로 '기록한 날'을 판단했다.
        rec_dates = {
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT date FROM slots s WHERE {SLOT_HAS_CONTENT}"
            )
        }
        funnel = _exec_funnel(conn, start, today_s)
    cat_total = sum(r["cnt"] for r in cat_rows)
    cats = [
        {"name": r["name"], "tone": r["tone"], "hours": r["cnt"] * 0.5,
         "pct": round(r["cnt"] / cat_total * 100) if cat_total else 0}
        for r in cat_rows
    ]
    days_data = [
        {"date": r["date"], "wd": _ko_weekday(r["date"]), "short": _short_date(r["date"]),
         "done": r["done_cnt"], "planned": r["planned_cnt"],
         "pct": round(r["done_cnt"] / r["planned_cnt"] * 100) if r["planned_cnt"] else 0}
        for r in day_rows
    ]
    pd_total_p = sum(r["planned"] for r in pd_rows)
    pd_total_a = sum(r["achieved"] for r in pd_rows)
    pd_data = [
        {"date": r["date"], "short": _short_date(r["date"]),
         "planned": r["planned"], "achieved": r["achieved"],
         "pct": round(r["achieved"] / r["planned"] * 100) if r["planned"] else 0}
        for r in pd_rows
    ]
    summary = {
        "streak": _calc_streak(rec_dates, today),
        "rec_days": len(days_data),
        "total_hours": round(sum(c["hours"] for c in cats), 1),
        "avg_done": round(sum(d["pct"] for d in days_data) / len(days_data)) if days_data else 0,
        "pd_pct": round(pd_total_a / pd_total_p * 100) if pd_total_p else 0,
    }
    # 요일별 완료율(어느 요일을 자주 흘려보내는지) — 일자 데이터를 요일로 묶는다.
    wd_acc = {i: [0, 0] for i in range(7)}
    for r in day_rows:
        wd = datetime.strptime(r["date"], "%Y-%m-%d").date().weekday()
        wd_acc[wd][0] += r["done_cnt"] or 0
        wd_acc[wd][1] += r["planned_cnt"] or 0
    weekday_data = [
        {"label": KO_WEEKDAYS[i], "done": wd_acc[i][0], "planned": wd_acc[i][1],
         "pct": round(wd_acc[i][0] / wd_acc[i][1] * 100) if wd_acc[i][1] else 0}
        for i in range(7)
    ]
    block_pd = [
        {"label": r["lbl"], "planned": r["planned"], "achieved": r["achieved"],
         "pct": round(r["achieved"] / r["planned"] * 100) if r["planned"] else 0}
        for r in block_pd_rows
    ]
    return {
        "rng": rng,
        "range_label": range_label,
        "start": start,
        "end": today_s,
        "today": today,
        "cats": cats,
        "days_data": days_data,
        "pd_data": pd_data,
        "weekday_data": weekday_data,
        "block_pd": block_pd,
        "summary": summary,
        "funnel": funnel,
        "insights": _build_insights(summary, weekday_data, block_pd, cats),
    }


@router.get("/analytics")
def analytics_view(request: Request, rng: str = "7", q: str = ""):
    data = _analytics_data(rng)
    # 분석·검색 병합: 검색어가 있으면 지난 슬롯/블록 기록을 같은 화면에서 함께 보여준다.
    q = (q or "").strip()
    s_slots, s_blocks = _search_records(q)
    ctx = {k: v for k, v in data.items() if k != "today"}
    ctx.update({
        "request": request,
        # AI 제안은 버튼을 누를 때만 부른다(화면 로드마다 부르면 매번 기다리고 토큰도 쓴다).
        "ai_on": ai.enabled(),
        "q": q,
        "s_slots": s_slots,
        "s_blocks": s_blocks,
        "flashback": _on_this_day(data["today"]),
    })
    return templates.TemplateResponse(request, "analytics.html", ctx)


@router.post("/analytics/ai")
async def analytics_ai(request: Request):
    """분석 화면의 'AI 제안 받기' 버튼. 누를 때만 AI를 호출한다(로드마다 부르지 않는다)."""
    form = await request.form()
    rng = (form.get("rng") or "7").strip()
    if not ai.enabled():
        return JSONResponse({"ok": False, "error": ".env의 AI_API_KEY와 주소·모델을 확인하세요"})
    data = _analytics_data(rng)
    text = await _off_loop(_ai_insights, data["summary"], data["weekday_data"],
                           data["block_pd"], data["cats"])
    if not text:
        return JSONResponse({"ok": False, "error": "호출 실패 · 키·주소·모델·잔액을 확인하세요"})
    return JSONResponse({"ok": True, "text": text})


def _search_records(q: str):
    """슬롯 DO·한일과 블록 PLAN·SEE·이름을 날짜를 가로질러 찾아 (slots, blocks) 반환."""
    q = (q or "").strip()
    if not q:
        return [], []
    like = _like_pattern(q)
    with get_conn() as conn:
        slots = [
            dict(r)
            for r in conn.execute(
                "SELECT s.date, s.start_time, b.block_order, b.block_label, "
                "       s.do_text, s.did_text "
                "FROM slots s JOIN blocks b ON b.id = s.block_id "
                "WHERE s.do_text LIKE ? ESCAPE '\\' OR s.did_text LIKE ? ESCAPE '\\' "
                "ORDER BY s.date DESC, s.slot_index LIMIT 300",
                (like, like),
            )
        ]
        blocks = [
            dict(r)
            for r in conn.execute(
                "SELECT date, block_order, block_label, name, plan_text, see_text "
                "FROM blocks "
                "WHERE plan_text LIKE ? ESCAPE '\\' OR see_text LIKE ? ESCAPE '\\' "
                "   OR name LIKE ? ESCAPE '\\' "
                "ORDER BY date DESC, block_order LIMIT 300",
                (like, like, like),
            )
        ]
    return slots, blocks
