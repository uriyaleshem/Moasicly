from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any

from class_balancer.ai.settings import get_provider_model, get_provider_token, provider_model_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose ClassBalancer AI provider connectivity.")
    parser.add_argument(
        "--provider",
        choices=["Anthropic", "Gemini", "OpenAI", "all"],
        default="all",
        help="Provider to test. Default: all.",
    )
    parser.add_argument("--timeout", type=int, default=25, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    providers = ["Anthropic", "Gemini", "OpenAI"] if args.provider == "all" else [args.provider]
    print("ClassBalancer AI diagnostics")
    print("API keys are never printed by this script.")
    print("")
    for provider in providers:
        diagnose_provider(provider, timeout=args.timeout)
        print("")
    return 0


def diagnose_provider(provider: str, timeout: int) -> None:
    token, source = get_provider_token(provider)
    configured_model = get_provider_model(provider)
    print(f"== {provider} ==")
    print(f"token: {'FOUND' if token else 'MISSING'}")
    print(f"token source: {source or '-'}")
    print(f"configured model: {configured_model or '-'}")
    if not token:
        print("result: cannot test, no token configured.")
        return

    if provider == "Gemini":
        list_gemini_models(token, timeout)

    for model in provider_model_candidates(provider):
        print(f"Trying model: {model}")
        ok, detail = test_provider_model(provider, token, model, timeout)
        print(detail)
        if ok:
            print(f"SUCCESS: {provider} works with model {model}")
            return
    print(f"FAILED: no tested {provider} model returned success.")


def test_provider_model(provider: str, token: str, model: str, timeout: int) -> tuple[bool, str]:
    if provider == "Anthropic":
        return post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": model,
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "Reply with OK only."}],
            },
            {"x-api-key": token, "anthropic-version": "2023-06-01"},
            timeout,
            lambda data: "".join(part.get("text", "") for part in data.get("content", [])).strip(),
        )
    if provider == "Gemini":
        return post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={token}",
            {
                "contents": [{"parts": [{"text": "Reply with OK only."}]}],
                "generationConfig": {"maxOutputTokens": 32},
            },
            {},
            timeout,
            _gemini_text,
        )
    if provider == "OpenAI":
        return post_json(
            "https://api.openai.com/v1/responses",
            {
                "model": model,
                "max_output_tokens": 32,
                "input": [{"role": "user", "content": "Reply with OK only."}],
            },
            {"Authorization": f"Bearer {token}"},
            timeout,
            lambda data: data.get("output_text", "") or json.dumps(data, ensure_ascii=False)[:300],
        )
    return False, f"Unsupported provider: {provider}"


def post_json(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    extract_text,
) -> tuple[bool, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            text = extract_text(data)
            return True, f"HTTP {response.status}: {text[:500] or 'OK'}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {detail[:1200]}"
    except urllib.error.URLError as exc:
        return False, f"NETWORK ERROR: {exc}"
    except Exception as exc:
        return False, f"ERROR: {type(exc).__name__}: {exc}"


def list_gemini_models(token: str, timeout: int) -> None:
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={token}",
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        usable = [
            item.get("name", "").replace("models/", "")
            for item in data.get("models", [])
            if "generateContent" in item.get("supportedGenerationMethods", [])
        ]
        print("Gemini generateContent models visible to this key:")
        for model in usable[:20]:
            print(f"  - {model}")
        if not usable:
            print("  none returned")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Gemini model list failed: HTTP {exc.code}: {detail[:800]}")
    except Exception as exc:
        print(f"Gemini model list failed: {type(exc).__name__}: {exc}")


def _gemini_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            parts.append(part.get("text", ""))
    if parts:
        return "\n".join(parts).strip()
    return json.dumps(data, ensure_ascii=False)[:300]


if __name__ == "__main__":
    raise SystemExit(main())
