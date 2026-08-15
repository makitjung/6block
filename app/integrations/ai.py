# 선택적 OpenAI 호환 AI 연결(자동 세분화·개선점 문장). 키·주소·모델이 없으면 비활성(규칙기반 폴백)
import json
import urllib.error
import urllib.request

from app.config import AI_API_KEY, AI_BASE_URL, AI_MODEL
from app.db import get_settings


def _cfg() -> tuple[str, str, str]:
    """(api_key, base_url, model). 주소·모델은 설정값이 우선, 없으면 .env 값. 키는 .env만."""
    s = get_settings()
    base = ((s.get("ai_base_url") or AI_BASE_URL) or "").strip().rstrip("/")
    model = ((s.get("ai_model") or AI_MODEL) or "").strip()
    return AI_API_KEY, base, model


def enabled() -> bool:
    """키·주소·모델이 모두 있으면 AI 사용 가능."""
    key, base, model = _cfg()
    return bool(key and base and model)


def status() -> dict:
    """설정 화면용 상태(키는 존재 여부만 노출, 값은 노출하지 않는다)."""
    key, base, model = _cfg()
    return {"has_key": bool(key), "base": base, "model": model,
            "enabled": bool(key and base and model)}


def complete(system: str, user: str, *, max_tokens: int = 600,
             temperature: float = 0.4) -> str | None:
    """OpenAI 호환 chat/completions 1회 호출. 실패·미설정이면 None(호출측이 규칙기반으로 폴백)."""
    key, base, model = _cfg()
    if not (key and base and model):
        return None
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )
    # 이 함수의 약속은 '어떻게 실패하든 None' 이다. 부르는 쪽(주간 자동세분화·분석 요약)은
    # None 을 받으면 규칙기반으로 넘어간다. 그래서 예외 종류를 하나씩 적지 않고 통째로 잡는다.
    # 예전에는 URLError·ValueError·KeyError·IndexError 만 적어 두었는데, 응답이
    # {"choices": null} 이면 TypeError 라 그대로 새어 나가 그 화면이 500 이 됐다.
    # 설정 탭에서 AI 주소를 다른 JSON API 로 잘못 적어 두면 곧바로 겪는 모양이다.
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        return (text or "").strip() or None
    except Exception:
        return None
