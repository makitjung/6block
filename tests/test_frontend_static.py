# 프런트엔드 정적 점검. 문법, 한글 IME 안전성, 자산 버전, 서비스워커 정책을 파일 내용으로 확인한다.
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from app.common import BASE_DIR, VERSIONED_ASSETS

STATIC = BASE_DIR / "static"
TEMPLATES = BASE_DIR / "templates"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
SW_JS = (STATIC / "sw.js").read_text(encoding="utf-8")
NODE = shutil.which("node") or "/opt/homebrew/bin/node"


# -- 문법 -------------------------------------------------------------------


@pytest.mark.skipif(not pathlib.Path(NODE).exists(), reason="node 가 없다")
@pytest.mark.parametrize("name", ["app.js", "sw.js"])
def test_자바스크립트_문법이_통과한다(name):
    res = subprocess.run([NODE, "--check", str(STATIC / name)],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr


# -- 한글 IME (이 프로젝트에서 가장 자주 재발한 버그) -----------------------------


ENTER_LINES = [
    (i, line) for i, line in enumerate(APP_JS.splitlines(), start=1)
    if re.search(r"""["']Enter["']|keyCode\s*===?\s*13|which\s*===?\s*13""", line)
]


def test_Enter_를_다루는_줄이_실제로_있다():
    """검사 자체가 헛도는 것을 막는다(정규식이 아무것도 못 잡으면 이 테스트가 먼저 실패한다)."""
    assert len(ENTER_LINES) >= 8, len(ENTER_LINES)


@pytest.mark.parametrize("lineno,line", ENTER_LINES, ids=[str(n) for n, _ in ENTER_LINES])
def test_모든_Enter_처리에_한글_조합_가드가_있다(lineno, line):
    """조합 중 Enter 를 가로채면 '한글' 이 'ㅎㅏㄴㄱㅡㄹ' 로 갈라진다.

    가드는 같은 줄에 있거나, 바로 앞 3줄 안에서 미리 return 하는 형태여도 된다.
    """
    window = "\n".join(APP_JS.splitlines()[max(0, lineno - 4):lineno])
    assert ("isComposing" in window and "229" in window), (
        f"app.js:{lineno} 에 IME 가드가 없다 → {line.strip()[:90]}"
    )


def test_한글을_입력하는_칸에_datalist_가_붙어_있지_않다():
    """datalist 드롭다운이 다시 그려지면 크롬 맥에서 한글 조합이 깨진다."""
    hits = []
    for path in list(TEMPLATES.glob("*.html")) + [STATIC / "app.js"]:
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if re.search(r"""list\s*=\s*["']|\.setAttribute\(\s*['"]list['"]""", line):
                hits.append(f"{path.name}:{i}")
    assert not hits, f"datalist 가 붙은 입력칸: {hits}"


# -- 자산 버전 (옛 코드가 남는 문제) --------------------------------------------


BASE_HTML = (TEMPLATES / "base.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("asset", ["app.js", "style.css"])
def test_핵심_자산은_버전을_달고_나간다(asset):
    assert re.search(rf"/static/{re.escape(asset)}\?v=\{{\{{ asset_ver\(\) \}}\}}", BASE_HTML), (
        f"{asset} 에 ?v= 가 없어 옛 파일이 캐시에 남는다"
    )


def test_버전이_붙는_자산은_전부_VERSIONED_ASSETS_에_들어_있다():
    """여기 빠진 파일에 ?v= 를 달면 파일을 고쳐도 버전이 안 올라가 옛것이 계속 쓰인다."""
    referenced = set()
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        referenced |= set(re.findall(r"/static/([\w.\-]+)\?v=", text))
    missing = referenced - set(VERSIONED_ASSETS)
    assert not missing, f"?v= 를 달았는데 VERSIONED_ASSETS 에 없는 파일: {sorted(missing)}"


def test_VERSIONED_ASSETS_의_파일이_실제로_존재한다():
    없는것 = [n for n in VERSIONED_ASSETS if not (STATIC / n).exists()]
    assert not 없는것, f"목록에는 있는데 파일이 없다: {없는것}"


def test_템플릿이_가리키는_정적파일이_전부_존재한다():
    없는것 = []
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for name in re.findall(r"/static/([\w.\-/]+?)(?:\?|\"|')", text):
            if not (STATIC / name).exists():
                없는것.append(f"{path.name} → {name}")
    assert not 없는것, 없는것


def test_매니페스트가_가리키는_아이콘이_존재한다():
    data = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
    for icon in data.get("icons", []):
        src = icon["src"].split("?")[0].lstrip("/")
        p = BASE_DIR / src if src.startswith("static/") else STATIC / src.replace("static/", "")
        assert p.exists(), f"매니페스트 아이콘이 없다: {icon['src']}"
    assert data.get("start_url"), "start_url 이 없으면 앱으로 설치해도 시작 화면이 없다"
    assert data.get("display") in ("standalone", "fullscreen", "minimal-ui"), data.get("display")


# -- 서비스워커 --------------------------------------------------------------


def test_서비스워커는_화면_요청을_가로채지_않는다():
    """navigate 를 캐시로 폴백하면 주소와 내용이 어긋난 화면(멈춘 화면)이 나온다."""
    assert "request.mode === 'navigate'" not in SW_JS
    assert "req.mode === 'navigate'" not in SW_JS


def test_서비스워커가_옛_캐시를_지운다():
    assert "caches.delete" in SW_JS
    assert "skipWaiting" in SW_JS and "clients.claim" in SW_JS


def test_서비스워커가_캐시를_두지_않는다():
    """캐시 자체를 없앴다. 예전에는 캐시 이름에 ?v= 를 붙여 옛 캐시를 갈아치웠는데,
    실패 폴백이 ignoreSearch 로 ?v= 를 무시해 결국 옛 app.js 가 나왔다.
    자세한 판단 근거는 app/static/sw.js 첫 주석과 tests/qa/test_p8_sw_manifest.py 에 있다."""
    code = "\n".join(ln for ln in SW_JS.splitlines() if not ln.strip().startswith("//"))
    assert "caches.open" not in code, "캐시 저장소를 다시 열고 있다"
    assert "respondWith" not in code, "요청을 가로채 캐시로 답하고 있다"
    assert "caches.delete" in code, "이미 설치된 기기의 옛 캐시를 지우는 코드가 없다"


# -- 응답 확인 없는 fetch (서버가 오류 HTML 을 주면 화면이 멈춘다) -----------------


def test_json_을_읽기_전에_응답_상태를_보는_곳이_있다():
    """전부 확인하라는 뜻이 아니라, 확인하는 습관이 코드에 있는지 본다."""
    assert re.search(r"\.ok\b|status\s*[=!]==?\s*\d{3}", APP_JS), (
        "fetch 응답 상태를 보는 코드가 하나도 없다"
    )


# -- 날짜 입력 (2자리 연도가 0026 으로 저장되는 함정) ----------------------------


def test_날짜칸은_8자리를_다_쳐야_값이_선다():
    assert "slice(0, 8)" in APP_JS or "slice(0,8)" in APP_JS, "날짜 마스크가 8자리로 안 끊는다"
    assert "length === 10" in APP_JS, (
        "10자리(YYYY-MM-DD)가 다 차기 전에 값을 넘기면 0026 같은 연도가 저장된다"
    )
