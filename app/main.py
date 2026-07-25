# 6block FastAPI 앱 조립. 화면별 라우터를 모으고 미들웨어·PWA·헬스체크만 여기서 다룬다.
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.common import BASE_DIR, KST
from app.config import ALLOWED_ORIGINS
from app.db import get_settings, init_db
from app.integrations import gcal, gcal_write, things
from app.routes import analytics, day, plan, reflect, settings, week


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
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
async def no_cache_headers(request: Request, call_next):
    """정적 자원·HTML은 항상 서버와 재검증(no-cache)해 옛 캐시(특히 폰 PWA)가 남지 않게 한다.

    StaticFiles의 ETag/Last-Modified와 함께 동작해, 안 바뀌면 304로 가볍게,
    바뀌면 새 파일을 받게 한다.
    """
    response = await call_next(request)
    path = request.url.path
    ctype = response.headers.get("content-type", "")
    if (
        path.startswith("/static/")
        or path.endswith(".webmanifest")
        or ctype.startswith("text/html")
    ):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def root():
    view = get_settings().get("start_view", "today")
    return RedirectResponse(url="/week" if view == "week" else "/today")


# -- PWA --------------------------------------------------------------------


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
    return FileResponse(
        BASE_DIR / "static" / "manifest.json",
        media_type="application/manifest+json",
    )


@app.get("/api/health")
def api_health():
    """연동 상태 점검. 브라우저에서 /api/health로 캘린더·Things 연결 확인."""
    return {
        "gcal": gcal.status(),
        "gcal_write": gcal_write.status(),
        "things": things.status(),
    }


@app.get("/api/now")
def api_now():
    """클라이언트가 서버 시각 기준으로 포모도로 정렬할 수 있게 KST를 반환."""
    n = datetime.now(KST)
    return {"iso": n.isoformat(timespec="seconds"), "epoch_ms": int(n.timestamp() * 1000)}
