# 6block FastAPI 앱 조립. 화면별 라우터를 모으고 미들웨어·PWA·헬스체크만 여기서 다룬다.
import json
import threading
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.common import BASE_DIR, KST, asset_ver
from app.config import ALLOWED_ORIGINS
from app.db import get_settings, init_db
from app.integrations import gcal, things
from app.routes import analytics, day, plan, reflect, settings, week


def _warm_caches():
    """구글 캘린더·Things 캐시를 미리 채운다.

    이 둘은 캐시가 있으면 즉시 응답하고 만료돼도 뒤에서 새로 받지만, 캐시가 아예 없는
    기동 직후 첫 요청만은 기다려야 한다(합쳐 1.5초쯤). 재시작 뒤 처음 여는 화면이
    그 값을 물지 않도록 미리 받아 둔다. 실패해도 그냥 넘어간다(다음 요청이 다시 받는다).
    """
    try:
        gcal.events_for_date(datetime.now(KST).date())
    except Exception:
        pass
    try:
        things.today_tasks(datetime.now(KST).date())
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    threading.Thread(target=_warm_caches, daemon=True).start()
    yield


app = FastAPI(title="6block", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

for _router in (day.router, week.router, plan.router, settings.router,
                analytics.router, reflect.router):
    app.include_router(_router)


# 이 서버에는 로그인이 없다. 그래서 사용자가 다른 웹사이트를 열어둔 것만으로 그 사이트가
# 사용자의 브라우저를 시켜 여기로 POST를 보낼 수 있다(CSRF). 기록 삭제·.env 저장·재시작이
# 모두 POST라 실제 위험이다. 브라우저는 교차 출처 요청에 Origin을 반드시 붙이므로,
# Origin(없으면 Referer)이 있으면서 이 서버 호스트와 다르면 거절한다. curl·스크립트처럼
# 두 헤더가 아예 없는 요청은 브라우저발 CSRF가 아니므로 그대로 통과시킨다.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DEFAULT_PORTS = {"http": "80", "https": "443"}


def _netloc_key(scheme: str, netloc: str) -> str:
    """'호스트:포트'로 정규화한다(기본 포트는 생략해 http://a 와 a:80 을 같게 본다)."""
    netloc = netloc.lower()
    host, _, port = netloc.partition(":")
    if port and port == DEFAULT_PORTS.get(scheme):
        port = ""
    return f"{host}:{port}" if port else host


def _origin_allowed(source: str, host_header: str) -> bool:
    """요청 출처(Origin/Referer)가 이 서버 자신인지. 설정의 추가 허용 호스트도 인정한다."""
    parts = urllib.parse.urlsplit(source)
    if not parts.netloc:
        return False
    src = _netloc_key(parts.scheme or "http", parts.netloc)
    if src in ALLOWED_ORIGINS:
        return True
    # Host 헤더에는 스킴이 없다. http/https 어느 쪽으로 접속했든 같게 보도록 둘 다 비교한다.
    return src in {_netloc_key(s, host_header) for s in ("http", "https")}


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    """쓰기 요청(POST 등)이 다른 사이트에서 온 것이면 막는다."""
    if request.method not in SAFE_METHODS:
        source = request.headers.get("origin") or request.headers.get("referer") or ""
        if source and not _origin_allowed(source, request.headers.get("host", "")):
            return JSONResponse(
                {"ok": False, "error": "다른 사이트에서 온 요청이라 거부했습니다"},
                status_code=403,
            )
    return await call_next(request)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """HTML은 늘 다시 받고, 버전이 붙은 정적 파일은 오래 캐시한다.

    HTML·매니페스트는 no-cache 로 두어 옛 화면(특히 폰 PWA)이 남지 않게 한다.
    /static 은 ?v=<수정시각> 이 붙어 나가므로 내용이 바뀌면 주소 자체가 바뀐다.
    그래서 ?v= 가 있으면 다시 물어볼 이유가 없어 1년 캐시(immutable)로 준다.
    예전에는 여기에도 no-cache 를 걸어, 페이지를 열 때마다 app.js·style.css 를
    두 번씩 재검증(304)했다. 폰에서 테일스케일로 붙으면 그 왕복이 그대로 체감된다.
    ?v= 없이 부르는 경로(서비스워커가 미리 담아 두는 목록 등)는 종전대로 재검증한다.
    """
    response = await call_next(request)
    path = request.url.path
    ctype = response.headers.get("content-type", "")
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if request.url.query.startswith("v=")
            else "no-cache"
        )
    elif path.endswith(".webmanifest") or ctype.startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# HTML 은 no-cache 라 탭을 누를 때마다 통째로 다시 받는다. 그 HTML 이 같은 구조를 수십 번
# 반복하는 표라 압축이 아주 잘 듣는다. 폰에서 테일스케일로 붙으면 그 차이가 그대로 탭 전환
# 체감이 된다. 테일스케일 serve 는 압축을 붙여 주지 않으므로 여기서 붙여야 한다.
# 마지막에 더한 미들웨어가 가장 바깥이라, 위 두 미들웨어가 헤더를 다 정한 뒤에 압축한다.
#
# 실측(2026-08-24, 실데이터 사본). 숫자는 코드가 자라면 낡으니 비율을 테스트로 못 박아 뒀다
# (tests/test_frontend_static.py).
#   오늘 134KB → 8.8KB · 주간 120KB → 6.3KB · 장기 235KB → 12.9KB
#   app.js 219KB → 56KB · style.css 117KB → 27KB
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.get("/")
def root():
    view = get_settings().get("start_view", "today")
    return RedirectResponse(url="/week" if view == "week" else "/today")


# -- PWA --------------------------------------------------------------------


@app.get("/version")
def version():
    """지금 서버가 내보내는 app.js/style.css 버전. 화면이 옛 코드를 들고 있는지 스스로 판단해
    새로고침하는 데 쓴다(기기마다 다른 버전이 도는 문제를 막는다)."""
    return JSONResponse({"v": asset_ver()}, headers={"Cache-Control": "no-store"})


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )


@app.get("/manifest.webmanifest")
def manifest():
    """매니페스트를 그때그때 만들어 아이콘 주소에 ?v= 를 붙여 내보낸다.

    /static 은 ?v= 가 붙어야 1년 캐시(immutable)로 나간다(위 cache_headers 참고).
    manifest.json 안의 아이콘 주소에는 그 값을 손으로 적어 둘 수 없어 예전에는 ?v= 없이
    나갔고, 그래서 화면을 열 때마다 아이콘을 다시 물었다(운영 로그에서 /static/icon.svg
    1,305회 중 1,284회가 304 재검증이었다). 폰에서 테일스케일로 붙으면 그 왕복 하나하나가
    그대로 체감이 된다. 아이콘 파일은 VERSIONED_ASSETS 에 들어 있어 그림을 바꾸면 값이
    저절로 바뀐다.
    """
    ver = asset_ver()
    data = json.loads(
        (BASE_DIR / "static" / "manifest.json").read_text(encoding="utf-8")
    )
    for icon in data.get("icons", []):
        src = icon.get("src", "")
        if src.startswith("/static/") and "?" not in src:
            icon["src"] = f"{src}?v={ver}"
    return JSONResponse(data, media_type="application/manifest+json")


# 브라우저와 iOS는 링크 태그와 무관하게 이 두 주소를 뿌리에서 먼저 찾는다. 없으면 404가
# 나고, 아이폰 홈화면 아이콘은 앱 화면 스크린샷으로 잡힌다(iOS는 SVG 아이콘을 무시한다).
@app.get("/favicon.ico")
def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.ico",
                        media_type="image/x-icon")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return FileResponse(BASE_DIR / "static" / "apple-touch-icon.png",
                        media_type="image/png")


@app.get("/api/now")
def api_now():
    """클라이언트가 서버 시각 기준으로 포모도로 정렬할 수 있게 KST를 반환."""
    n = datetime.now(KST)
    return {"iso": n.isoformat(timespec="seconds"), "epoch_ms": int(n.timestamp() * 1000)}
