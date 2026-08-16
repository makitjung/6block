# 서비스워커가 아무것도 캐시하지 않는지, 매니페스트 아이콘 규격이 갖춰졌는지 못 박는다.
import json
import pathlib

import pytest

STATIC = pathlib.Path(__file__).resolve().parent.parent.parent / "app" / "static"
SW_RAW = (STATIC / "sw.js").read_text(encoding="utf-8")
# 주석에는 '무엇을 왜 걷어냈는지'를 적어 두었으므로 금지어가 그대로 나온다. 코드만 본다.
SW = "\n".join(ln for ln in SW_RAW.splitlines() if not ln.strip().startswith("//"))


# ---------------------------------------------------------------------------
# 서비스워커: 캐시 금지 (CLAUDE.md 9절 16항)
#   캐싱 워커는 고칠 때마다 옛 코드를 내준다. 특히 예전 폴백은 ignoreSearch 로 ?v= 를
#   무시해, app.js?v=새것 요청이 실패하면 app.js?v=옛것 을 내줬다.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("banned", [
    "caches.open",      # 캐시 저장소를 여는 것 자체
    "addAll",           # 미리 담아 두기
    "cache.put",
    "c.put",
    "caches.match",     # 캐시에서 꺼내 응답하기
    "ignoreSearch",     # ?v= 를 무시해 옛 자원과 맞추던 폴백
    "respondWith",      # 요청을 가로채 대신 응답하기
])
def test_서비스워커가_캐시로_응답하지_않는다(banned):
    assert banned not in SW, f"sw.js 에 '{banned}' 가 남아 있다(캐시 금지 원칙 위반)"


def test_옛_캐시를_지우는_코드가_있다():
    """이미 설치된 기기에 남은 캐시를 활성화 때 비워야 옛 app.js 가 안 튀어나온다."""
    assert "caches.keys" in SW
    assert "caches.delete" in SW


def test_fetch_핸들러는_남아_있다():
    """크롬이 '앱으로 설치'를 제안하는 조건이 fetch 핸들러의 존재다. 가로채지만 않으면 된다."""
    assert "addEventListener('fetch'" in SW


def test_즉시_교체된다():
    """새 워커가 기다리지 않고 바로 올라와야 고친 코드가 다음 열기에 반영된다."""
    assert "skipWaiting" in SW
    assert "clients.claim" in SW


def test_서비스워커가_버전_대상에_들어_있다():
    """VERSIONED_ASSETS 에 없으면 sw.js 를 고쳐도 등록 주소(?v=)가 그대로라 새 워커가 안 뜬다."""
    from app.common import VERSIONED_ASSETS
    assert "sw.js" in VERSIONED_ASSETS


# ---------------------------------------------------------------------------
# 매니페스트 아이콘 (CLAUDE.md 9절 1항: 192px·512px)
# ---------------------------------------------------------------------------


def _manifest():
    return json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("size", ["192x192", "512x512"])
def test_설치용_png_아이콘_규격이_있다(size):
    icons = _manifest()["icons"]
    hit = [i for i in icons if i.get("sizes") == size and i.get("type") == "image/png"]
    assert hit, f"{size} PNG 아이콘이 매니페스트에 없다"
    for i in hit:
        p = STATIC / i["src"].split("?")[0].removeprefix("/static/")
        assert p.exists(), f"파일이 없다: {i['src']}"


def test_아이콘_파일_크기가_선언과_같다():
    """선언만 바꾸고 파일을 안 만들면 크롬이 조용히 무시한다."""
    from PIL import Image
    for i in _manifest()["icons"]:
        if i.get("type") != "image/png":
            continue
        w, h = (int(x) for x in i["sizes"].split("x"))
        p = STATIC / i["src"].split("?")[0].removeprefix("/static/")
        assert Image.open(p).size == (w, h), f"{i['src']} 실제 크기가 선언과 다르다"


def test_maskable_아이콘이_있다():
    """런처가 제 모양으로 자를 수 있게 maskable 이 하나는 있어야 한다."""
    assert any("maskable" in (i.get("purpose") or "") for i in _manifest()["icons"])


def test_아이콘이_버전_대상에_들어_있다():
    from app.common import VERSIONED_ASSETS
    assert "icon-192.png" in VERSIONED_ASSETS


# ---------------------------------------------------------------------------
# 서버가 실제로 내주는지
# ---------------------------------------------------------------------------


def test_서버가_새_아이콘과_매니페스트를_내준다(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert any(i.get("sizes") == "192x192" for i in r.json()["icons"])
    assert client.get("/static/icon-192.png").status_code == 200


def test_sw_는_캐시하지_말라고_내려간다(client):
    """sw.js 자체가 캐시되면 새 워커로 못 바꾼다."""
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", "")
    assert r.headers.get("service-worker-allowed") == "/"
