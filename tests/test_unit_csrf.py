# main.py 의 CSRF Origin 가드 단위 테스트. 로그인이 없는 서버라 이 가드가 유일한 방어선이다.
import pytest

import app.config as cfg
import app.main as main


def test_netloc_정규화_기본포트는_생략된다():
    assert main._netloc_key("http", "Example.COM:80") == "example.com"
    assert main._netloc_key("https", "example.com:443") == "example.com"
    assert main._netloc_key("http", "example.com:8000") == "example.com:8000"


@pytest.mark.parametrize("source", [
    "http://127.0.0.1:8000",
    "https://127.0.0.1:8000",
    "http://127.0.0.1:8000/some/page",
])
def test_같은_호스트는_허용(source):
    assert main._origin_allowed(source, "127.0.0.1:8000") is True


@pytest.mark.parametrize("source", [
    "http://evil.example",
    "https://evil.example/page",
    "http://127.0.0.1:9999",
    "http://127.0.0.1.evil.com",
    "http://evil.com/?x=127.0.0.1:8000",
])
def test_다른_호스트는_차단(source):
    assert main._origin_allowed(source, "127.0.0.1:8000") is False


def test_접미사_일치로_뚫리지_않는다():
    """evil-6block.ts.net 이 6block.ts.net 으로 통과되면 안 된다."""
    assert main._origin_allowed("https://evil-mac-mini.tail8a0bff.ts.net",
                                "mac-mini.tail8a0bff.ts.net") is False


def test_netloc_이_없으면_차단():
    for source in ("null", "", "about:blank", "file://", "javascript:alert(1)"):
        assert main._origin_allowed(source, "127.0.0.1:8000") is False


def test_후행점_호스트는_통과되지_않아야_한다():
    """'host.' 는 DNS 상 같은 곳을 가리키므로 브라우저가 보낼 수 있다."""
    assert main._origin_allowed("http://127.0.0.1.:8000", "127.0.0.1:8000") is False


def test_추가_허용_호스트_설정이_동작한다(monkeypatch):
    monkeypatch.setattr(cfg, "ALLOWED_ORIGINS", {"6block.example.com"})
    monkeypatch.setattr(main, "ALLOWED_ORIGINS", {"6block.example.com"})
    assert main._origin_allowed("https://6block.example.com", "127.0.0.1:8000") is True
    assert main._origin_allowed("https://other.example.com", "127.0.0.1:8000") is False


def test_안전한_메서드_집합():
    assert main.SAFE_METHODS == {"GET", "HEAD", "OPTIONS"}
