from __future__ import annotations

import ast
import copy
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from class_balancer.ai.settings import PROVIDER_ENV_VARS, get_provider_model, get_provider_token, load_ai_settings
from class_balancer.models.fields import DEFAULT_RULE_SETTINGS


STRUCTURED_REVIEW_TOOL_NAME = "record_assignment_review"
ACTION_SELECTION_TOOL_NAME = "select_assignment_actions"
RULE_RECOMMENDATION_TOOL_NAME = "recommend_assignment_rules"

STRUCTURED_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary_he": {
            "type": "string",
            "description": "סיכום קצר בעברית של איכות השיבוץ והבעיה המרכזית.",
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "רמת סיכון תפעולית לפי חומרת הבעיות.",
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["rerun", "inspect_data", "move_or_swap", "relax_rule", "adjust_weights"],
                    },
                    "title_he": {"type": "string"},
                    "reason_he": {"type": "string"},
                    "expected_gain": {"type": "number"},
                    "privacy_safe": {"type": "boolean"},
                },
                "required": ["action", "title_he", "reason_he", "expected_gain", "privacy_safe"],
            },
        },
        "best_recommendation_index": {"type": "integer"},
    },
    "required": ["summary_he", "risk_level", "recommendations", "best_recommendation_index"],
}

ACTION_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary_he": {
            "type": "string",
            "description": "סיכום קצר בעברית של בחירת פעולות השיפור.",
        },
        "no_improvement": {
            "type": "boolean",
            "description": "true כאשר אין פעולה מוצעת שמשפרת את השיבוץ או שהשיבוץ נראה טוב כפי שהוא.",
        },
        "selected_candidate_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "מזהי מועמדים מתוך candidate_actions בלבד. אין להמציא מזהים חדשים.",
        },
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "candidate_id": {"type": "string"},
                    "reason_he": {"type": "string"},
                },
                "required": ["candidate_id", "reason_he"],
            },
        },
    },
    "required": ["summary_he", "no_improvement", "selected_candidate_ids", "notes"],
}

RULE_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "balance_class_size": {"type": "boolean"},
        "balance_gender": {"type": "boolean"},
        "balance_grades": {"type": "boolean"},
        "balance_behavior": {"type": "boolean"},
        "spread_dominant_students": {"type": "boolean"},
        "friendship": {"type": "boolean"},
        "friendship_required": {"type": "boolean"},
        "friendship_first": {"type": "boolean"},
        "friendship_priority_order": {"type": "boolean"},
        "spread_source_school": {"type": "boolean"},
        "avoid_social_isolation": {"type": "boolean"},
        "hard_class_capacity": {"type": "boolean"},
        "max_students_per_class": {"type": "integer"},
        "max_students_per_gender": {"type": "integer"},
        "ai_assisted_assignment": {"type": "boolean"},
        "ai_auto_review": {"type": "boolean"},
        "ai_provider_limit": {"type": "integer"},
        "class_size_weight": {"type": "number"},
        "gender_weight": {"type": "number"},
        "grade_weight": {"type": "number"},
        "subject_weight": {"type": "number"},
        "behavior_weight": {"type": "number"},
        "dominance_weight": {"type": "number"},
        "friendship_weight": {"type": "number"},
        "source_school_weight": {"type": "number"},
        "grade_tolerance": {"type": "number"},
        "gender_tolerance": {"type": "number"},
        "behavior_tolerance": {"type": "number"},
        "dominance_tolerance": {"type": "number"},
        "max_iterations": {"type": "integer"},
        "search_restarts": {"type": "integer"},
        "first_improvement_threshold": {"type": "integer"},
        "swap_search_min_score": {"type": "number"},
        "stop_when_score_at_least": {"type": "number"},
        "optimizer_backend": {"type": "string", "enum": ["auto", "local", "exact"]},
        "optimizer_time_limit_seconds": {"type": "integer"},
        "random_seed": {"type": "integer"},
        "allow_slow_large_search": {"type": "boolean"},
    },
    "required": [
        "balance_class_size",
        "balance_gender",
        "balance_grades",
        "balance_behavior",
        "spread_dominant_students",
        "friendship",
        "friendship_required",
        "friendship_first",
        "friendship_priority_order",
        "spread_source_school",
        "avoid_social_isolation",
        "hard_class_capacity",
        "max_students_per_class",
        "max_students_per_gender",
        "ai_assisted_assignment",
        "ai_auto_review",
        "ai_provider_limit",
        "class_size_weight",
        "gender_weight",
        "grade_weight",
        "subject_weight",
        "behavior_weight",
        "dominance_weight",
        "friendship_weight",
        "source_school_weight",
        "grade_tolerance",
        "gender_tolerance",
        "behavior_tolerance",
        "dominance_tolerance",
        "max_iterations",
        "search_restarts",
        "first_improvement_threshold",
        "swap_search_min_score",
        "stop_when_score_at_least",
        "optimizer_backend",
        "optimizer_time_limit_seconds",
        "random_seed",
        "allow_slow_large_search",
    ],
}

RULE_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary_he": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason_he": {"type": "string"},
        "expected_gain": {"type": "number"},
        "settings": RULE_SETTINGS_SCHEMA,
    },
    "required": ["summary_he", "risk_level", "reason_he", "expected_gain", "settings"],
}


class AiClient:
    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        settings = load_ai_settings()
        provider = settings.get("selected_provider", "OpenAI")
        token, _ = get_provider_token(provider)
        return bool(settings.get("enabled") and token)

    def test_connection(self, provider: str | None = None, model: str | None = None) -> dict[str, Any]:
        settings = load_ai_settings()
        provider = provider or settings.get("selected_provider", "OpenAI")
        token, _source = get_provider_token(provider)
        if not token:
            return {"ok": False, "message": f"לא נמצא מפתח AI עבור {provider}.", "source": ""}
        try:
            tested_model = model or get_provider_model(provider)
            text = self._send(provider, token, "ענה במילה OK בלבד.", {"test": True}, model=tested_model)
            return {"ok": True, "message": text[:200] or "OK", "source": provider, "model": tested_model}
        except Exception as exc:
            _log_provider_failure(provider, model or get_provider_model(provider), exc)
            return {"ok": False, "message": _sanitize_message(str(exc)), "source": provider, "model": model or get_provider_model(provider)}

    def complete(self, task: str, payload: dict[str, Any], fallback: str) -> dict[str, Any]:
        settings = load_ai_settings()
        provider = settings.get("selected_provider", "OpenAI")
        token, _source = get_provider_token(provider)
        if not settings.get("enabled"):
            return {"used_ai": False, "text": fallback, "source": "local", "payload": payload}
        if not token:
            return {"used_ai": False, "text": fallback + "\n\nלא נמצא מפתח AI, לכן נוצר הסבר מקומי.", "source": "local", "payload": payload}
        try:
            text = self._send(provider, token, task, payload, structured=False)
            return {"used_ai": True, "text": text, "source": provider, "payload": payload}
        except Exception as exc:
            _log_provider_failure(provider, get_provider_model(provider), exc)
            clean_error = _sanitize_message(str(exc))
            return {
                "used_ai": False,
                "text": fallback + f"\n\nקריאת AI נכשלה ולכן מוצג הסבר מקומי. שגיאה: {clean_error}",
                "source": "local",
                "payload": payload,
            }

    def complete_structured_all(
        self,
        task: str,
        payload: dict[str, Any],
        fallback: str,
        allow_external: bool = True,
        provider_limit: int | None = None,
    ) -> dict[str, Any]:
        settings = load_ai_settings()
        local = _local_structured_review(fallback, payload)
        if not allow_external:
            return {
                "status": "local_only",
                "used_ai": False,
                "text": fallback + "\n\nשליחה ל-AI חיצוני אינה מאושרת בפרויקט הזה, לכן נוצר ניתוח מקומי בלבד.",
                "source": "local",
                "payload": payload,
                "providers": [],
                "best": local,
            }
        if not settings.get("enabled"):
            return {
                "status": "local_only",
                "used_ai": False,
                "text": fallback,
                "source": "local",
                "payload": payload,
                "providers": [],
                "best": local,
            }

        def call_provider(provider: str) -> dict[str, Any]:
            token, _source = get_provider_token(provider)
            if not token:
                return {
                    "provider": provider,
                    "used": False,
                    "ok": False,
                    "source": "",
                    "error": "לא נמצא מפתח AI לספק.",
                }
            try:
                model = get_provider_model(provider)
                text = self._send(provider, token, _structured_task(task), payload, structured=True)
                parsed, parse_warning = _parse_structured_response(text)
                parse_failed = bool(parsed.get("parse_failed"))
                return {
                    "provider": provider,
                    "used": True,
                    "ok": not parse_failed,
                    "source": provider,
                    "model": model,
                    "text": text[:1000],
                    "parsed": parsed,
                    "warning": parse_warning,
                    "error": parse_warning if parse_failed else "",
                }
            except Exception as exc:
                _log_provider_failure(provider, get_provider_model(provider), exc)
                return {
                    "provider": provider,
                    "used": True,
                    "ok": False,
                    "source": provider,
                    "model": get_provider_model(provider),
                    "error": _sanitize_message(str(exc))[:500],
                }

        provider_results = _run_provider_calls(_ordered_providers(settings, provider_limit), call_provider)

        successful = [
            item
            for item in provider_results
            if item.get("ok") and item.get("parsed") and not item.get("parsed", {}).get("parse_failed")
        ]
        best = _choose_best_structured(successful) if successful else local
        any_provider_called = any(item.get("used") for item in provider_results)
        status = "ai_completed" if successful else ("ai_failed" if any_provider_called else "local_only")
        return {
            "status": status,
            "used_ai": bool(successful),
            "text": best.get("summary_he", fallback),
            "source": "multi-provider" if successful else "local",
            "payload": payload,
            "providers": provider_results,
            "best": best,
        }

    def complete_action_selection(
        self,
        task: str,
        payload: dict[str, Any],
        fallback: str,
        allow_external: bool = True,
        provider_limit: int | None = None,
    ) -> dict[str, Any]:
        settings = load_ai_settings()
        if not allow_external:
            return {
                "status": "local_only",
                "used_ai": False,
                "text": fallback + "\n\nשליחה ל-AI חיצוני אינה מאושרת בפרויקט הזה.",
                "source": "local",
                "payload": payload,
                "providers": [],
                "selection": {"summary_he": fallback, "no_improvement": True, "selected_candidate_ids": [], "notes": []},
            }
        if not settings.get("enabled"):
            return {
                "status": "local_only",
                "used_ai": False,
                "text": fallback,
                "source": "local",
                "payload": payload,
                "providers": [],
                "selection": {"summary_he": fallback, "no_improvement": True, "selected_candidate_ids": [], "notes": []},
            }

        def call_provider(provider: str) -> dict[str, Any]:
            token, _source = get_provider_token(provider)
            if not token:
                return {"provider": provider, "used": False, "ok": False, "source": "", "error": "לא נמצא מפתח AI לספק."}
            try:
                model = get_provider_model(provider)
                text = self._send(
                    provider,
                    token,
                    _action_selection_task(task),
                    payload,
                    structured=True,
                    schema=ACTION_SELECTION_SCHEMA,
                    tool_name=ACTION_SELECTION_TOOL_NAME,
                )
                parsed, parse_warning = _parse_action_selection_response(text)
                parse_failed = bool(parsed.get("parse_failed"))
                provider_result = {
                    "provider": provider,
                    "used": True,
                    "ok": not parse_failed,
                    "source": provider,
                    "model": model,
                    "text": text[:1000],
                    "parsed": parsed,
                    "warning": parse_warning,
                    "error": parse_warning if parse_failed else "",
                }
                return provider_result
            except Exception as exc:
                _log_provider_failure(provider, get_provider_model(provider), exc)
                return {
                    "provider": provider,
                    "used": True,
                    "ok": False,
                    "source": provider,
                    "model": get_provider_model(provider),
                    "error": _sanitize_message(str(exc))[:500],
                }

        provider_results = _run_provider_calls(_ordered_providers(settings, provider_limit), call_provider)

        successful = [
            item
            for item in provider_results
            if item.get("ok") and item.get("parsed") and not item.get("parsed", {}).get("parse_failed")
        ]
        if successful:
            selection = _choose_best_action_selection(successful, payload)
            return {
                "status": "ai_completed",
                "used_ai": True,
                "text": selection.get("summary_he", ""),
                "source": "multi-provider",
                "payload": payload,
                "providers": provider_results,
                "selection": selection,
            }

        any_provider_called = any(item.get("used") for item in provider_results)
        return {
            "status": "ai_failed" if any_provider_called else "local_only",
            "used_ai": False,
            "text": fallback,
            "source": "local",
            "payload": payload,
            "providers": provider_results,
            "selection": {"summary_he": fallback, "no_improvement": True, "selected_candidate_ids": [], "notes": []},
        }

    def complete_rule_recommendation(
        self,
        task: str,
        payload: dict[str, Any],
        fallback: str,
        allow_external: bool = True,
        provider_limit: int | None = None,
    ) -> dict[str, Any]:
        settings = load_ai_settings()
        local = _local_rule_recommendation(fallback, payload)
        if not allow_external:
            return {
                "status": "local_only",
                "used_ai": False,
                "text": fallback + "\n\nשליחה ל-AI חיצוני אינה מאושרת בפרויקט הזה, לכן נוצרה המלצת כללים מקומית.",
                "source": "local",
                "payload": payload,
                "providers": [],
                "recommendation": local,
            }
        if not settings.get("enabled"):
            return {
                "status": "local_only",
                "used_ai": False,
                "text": fallback,
                "source": "local",
                "payload": payload,
                "providers": [],
                "recommendation": local,
            }

        def call_provider(provider: str) -> dict[str, Any]:
            token, _source = get_provider_token(provider)
            if not token:
                return {"provider": provider, "used": False, "ok": False, "source": "", "error": "לא נמצא מפתח AI לספק."}
            try:
                model = get_provider_model(provider)
                text = self._send(
                    provider,
                    token,
                    _rule_recommendation_task(task),
                    payload,
                    structured=True,
                    schema=RULE_RECOMMENDATION_SCHEMA,
                    tool_name=RULE_RECOMMENDATION_TOOL_NAME,
                )
                parsed, parse_warning = _parse_rule_recommendation_response(text, payload)
                parse_failed = bool(parsed.get("parse_failed"))
                return {
                    "provider": provider,
                    "used": True,
                    "ok": not parse_failed,
                    "source": provider,
                    "model": model,
                    "text": text[:1000],
                    "parsed": parsed,
                    "warning": parse_warning,
                    "error": parse_warning if parse_failed else "",
                }
            except Exception as exc:
                _log_provider_failure(provider, get_provider_model(provider), exc)
                return {
                    "provider": provider,
                    "used": True,
                    "ok": False,
                    "source": provider,
                    "model": get_provider_model(provider),
                    "error": _sanitize_message(str(exc))[:500],
                }

        provider_results = _run_provider_calls(_ordered_providers(settings, provider_limit), call_provider)

        successful = [
            item
            for item in provider_results
            if item.get("ok") and item.get("parsed") and not item.get("parsed", {}).get("parse_failed")
        ]
        recommendation = _choose_best_rule_recommendation(successful, payload) if successful else local
        any_provider_called = any(item.get("used") for item in provider_results)
        status = "ai_completed" if successful else ("ai_failed" if any_provider_called else "local_only")
        return {
            "status": status,
            "used_ai": bool(successful),
            "text": recommendation.get("summary_he", fallback),
            "source": "multi-provider" if successful else "local",
            "payload": payload,
            "providers": provider_results,
            "recommendation": recommendation,
        }

    def _send(
        self,
        provider: str,
        token: str,
        task: str,
        payload: dict[str, Any],
        model: str | None = None,
        structured: bool = False,
        schema: dict[str, Any] | None = None,
        tool_name: str = STRUCTURED_REVIEW_TOOL_NAME,
    ) -> str:
        if provider == "OpenAI":
            return self._send_openai(token, task, payload, model, structured, schema, tool_name)
        if provider == "Anthropic":
            return self._send_anthropic(token, task, payload, model, structured, schema, tool_name)
        if provider == "Gemini":
            return self._send_gemini(token, task, payload, model, structured, schema, tool_name)
        raise ValueError("שירות AI לא נתמך.")

    def _send_openai(
        self,
        token: str,
        task: str,
        payload: dict[str, Any],
        model: str | None = None,
        structured: bool = False,
        schema: dict[str, Any] | None = None,
        tool_name: str = STRUCTURED_REVIEW_TOOL_NAME,
    ) -> str:
        body = {
            "model": model or get_provider_model("OpenAI"),
            "max_output_tokens": 900,
            "input": [
                {
                    "role": "system",
                    "content": _system_prompt(structured),
                },
                {
                    "role": "user",
                    "content": task + "\n\nנתונים אנונימיים ומוגבלים:\n" + _limited_payload_text(payload),
                },
            ],
        }
        if structured:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": tool_name,
                    "strict": True,
                    "schema": _schema_for_provider("OpenAI", schema),
                }
            }
        data = self._post_json(
            "https://api.openai.com/v1/responses",
            body,
            {"Authorization": f"Bearer {token}"},
        )
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        chunks: list[str] = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    chunks.append(content.get("text", ""))
        return "\n".join(chunks).strip()

    def _send_anthropic(
        self,
        token: str,
        task: str,
        payload: dict[str, Any],
        model: str | None = None,
        structured: bool = False,
        schema: dict[str, Any] | None = None,
        tool_name: str = STRUCTURED_REVIEW_TOOL_NAME,
    ) -> str:
        body = {
            "model": model or get_provider_model("Anthropic"),
            "max_tokens": 900,
            "system": _system_prompt(structured),
            "messages": [
                {
                    "role": "user",
                    "content": task + "\n\nנתונים אנונימיים ומוגבלים:\n" + _limited_payload_text(payload),
                }
            ],
        }
        if structured:
            body["tools"] = [
                {
                    "name": tool_name,
                    "description": (
                        "Record one privacy-safe structured response for an anonymous class assignment. "
                        "Use only the provided aggregate, anonymized fields, and candidate ids. Do not ask for student names."
                    ),
                    "input_schema": _schema_for_provider("Anthropic", schema),
                    "strict": True,
                }
            ]
            body["tool_choice"] = {"type": "tool", "name": tool_name}
        data = self._post_json(
            "https://api.anthropic.com/v1/messages",
            body,
            {"x-api-key": token, "anthropic-version": "2023-06-01"},
        )
        if structured:
            for part in data.get("content", []):
                if part.get("type") == "tool_use" and part.get("name") == tool_name:
                    return json.dumps(part.get("input", {}), ensure_ascii=False)
        return "\n".join(part.get("text", "") for part in data.get("content", [])).strip()

    def _send_gemini(
        self,
        token: str,
        task: str,
        payload: dict[str, Any],
        model: str | None = None,
        structured: bool = False,
        schema: dict[str, Any] | None = None,
        tool_name: str = STRUCTURED_REVIEW_TOOL_NAME,
    ) -> str:
        generation_config: dict[str, Any] = {"maxOutputTokens": 1600 if structured else 900}
        if structured:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseJsonSchema"] = _schema_for_provider("Gemini", schema)
        body = {
            "systemInstruction": {"parts": [{"text": _system_prompt(structured)}]},
            "contents": [
                {
                    "parts": [
                        {
                            "text": task + "\n\nנתונים אנונימיים ומוגבלים:\n" + _limited_payload_text(payload)
                        }
                    ]
                }
            ],
            "generationConfig": generation_config,
        }
        data = self._post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model or get_provider_model('Gemini')}:generateContent",
            body,
            {"x-goog-api-key": token},
        )
        parts = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                parts.append(part.get("text", ""))
        return "\n".join(parts).strip()

    def _post_json(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **headers,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {_sanitize_message(detail)[:500]}") from exc


def _ordered_providers(settings: dict[str, Any], provider_limit: int | None) -> list[str]:
    selected = str(settings.get("selected_provider", "OpenAI"))
    if selected not in PROVIDER_ENV_VARS:
        selected = "OpenAI"
    limit = max(1, min(len(PROVIDER_ENV_VARS), int(provider_limit or len(PROVIDER_ENV_VARS))))
    ordered = [selected] + [provider for provider in PROVIDER_ENV_VARS if provider != selected]
    return ordered[:limit]


def _run_provider_calls(
    ordered_providers: list[str],
    call_provider: Any,
) -> list[dict[str, Any]]:
    if not ordered_providers:
        return []
    results: list[dict[str, Any] | None] = [None] * len(ordered_providers)
    with ThreadPoolExecutor(max_workers=min(len(ordered_providers), len(PROVIDER_ENV_VARS))) as executor:
        future_to_index = {
            executor.submit(call_provider, provider): index
            for index, provider in enumerate(ordered_providers)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            provider = ordered_providers[index]
            try:
                results[index] = future.result()
            except Exception as exc:
                _log_provider_failure(provider, get_provider_model(provider), exc)
                results[index] = {
                    "provider": provider,
                    "used": True,
                    "ok": False,
                    "source": provider,
                    "model": get_provider_model(provider),
                    "error": _sanitize_message(str(exc))[:500],
                }
    return [item for item in results if item is not None]


def _structured_task(task: str) -> str:
    return (
        task
        + "\n\nהחזר JSON בלבד, ללא Markdown וללא טקסט מחוץ ל-JSON. "
        "אל תכלול שמות תלמידים, הערות אישיות או מידע מזהה. "
        "מותר להחזיר עד 5 המלצות. הסכמה:\n"
        "{"
        '"summary_he":"סיכום קצר בעברית",'
        '"risk_level":"low|medium|high",'
        '"recommendations":[{"action":"rerun|inspect_data|move_or_swap|relax_rule|adjust_weights","title_he":"כותרת","reason_he":"נימוק קצר","expected_gain":0,"privacy_safe":true}],'
        '"best_recommendation_index":0'
        "}"
    )


def _action_selection_task(task: str) -> str:
    return (
        task
        + "\n\nבחר רק מתוך candidate_actions שסופקו. "
        "אל תמציא תלמידים, כיתות או פעולות חדשות. "
        "אם אין פעולה שמשפרת את הציון או מפחיתה כללים שנשברו, החזר no_improvement=true ורשימת מזהים ריקה."
    )


def _rule_recommendation_task(task: str) -> str:
    return (
        task
        + "\n\nUse the anonymized detailed payload, including students_anonymized, source_school_distribution, class_names, system_explanation, current_settings, assignment_summary, data_summary, and preflight_summary. "
        "Do not include real names or identifying notes in the response. max_students_per_class and max_students_per_gender are hard laws; keep hard_class_capacity=true and do not recommend disabling them. "
        "For source schools, prefer floor/ceil distribution for each source school across all classes, not only avoiding a single isolated student. "
        "Set friendship_required=true when the user expects every student with friend requests to receive at least one requested friend. Prefer raising friendship_weight before enabling friendship_first; enable friendship_first only if class size, gender caps, pair separations, and source-school distribution can remain balanced. "
        + "\n\nהחזר סט כללים מלא לפי הסכמה בלבד. "
        "השתמש רק בשדות האנונימיים שסופקו בבקשה, כולל פירוט התלמידים האנונימי והתפלגות בתי ספר מקור. "
        "אל תכלול שמות תלמידים, הערות אישיות או מידע מזהה. "
        "אם יש הרבה בקשות חברים חסרות, השאר friendship_required=true ושקול להעלות friendship_weight לפני הפעלת friendship_first. "
        "אם הנתונים חסרים, אל תפעיל איזונים שנשענים על שדות חסרים בצורה אגרסיבית."
    )


def _schema_for_provider(provider: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = copy.deepcopy(schema or STRUCTURED_REVIEW_SCHEMA)
    if provider == "Gemini":
        _strip_schema_keys(schema, {"additionalProperties"})
    return schema


def _strip_schema_keys(value: Any, keys: set[str]) -> None:
    if isinstance(value, dict):
        for key in keys:
            value.pop(key, None)
        for item in value.values():
            _strip_schema_keys(item, keys)
    elif isinstance(value, list):
        for item in value:
            _strip_schema_keys(item, keys)


def _system_prompt(structured: bool) -> str:
    base = (
        "אתה כלי עזר למערכת שיבוץ תלמידים. השיבוץ הסופי נקבע רק במערכת המקומית ובידי המשתמש. "
        "אל תבקש שמות תלמידים, הערות רגישות או מידע מזהה. השתמש רק בסיכום האנונימי שסופק."
    )
    if structured:
        return (
            base
            + " החזר רק אובייקט במבנה הסכמה שסופקה. ההמלצות הן לבדיקה ידנית בלבד ולא ליישום אוטומטי."
        )
    return (
        base
        + " ענה בעברית ברורה וקצרה. אם אתה מציע פעולה, ציין שהיא הצעה לבדיקה ידנית ולא החלטת שיבוץ סופית."
    )


def _log_provider_failure(provider: str, model: str, exc: Exception | str) -> None:
    print(f"[Mosaicly AI] {provider} failed. model={model or '-'}. reason={_sanitize_message(str(exc))}", flush=True)


def _sanitize_message(message: str) -> str:
    clean = str(message or "")
    clean = re.sub(r"([?&](?:key|api_key|token|access_token)=)[^&\s]+", r"\1[redacted]", clean, flags=re.I)
    clean = re.sub(r"(Authorization:\s*Bearer\s+)[^\s,}]+", r"\1[redacted]", clean, flags=re.I)
    clean = re.sub(r"(x-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", r"\1[redacted]", clean, flags=re.I)
    clean = re.sub(r"(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", r"\1[redacted]", clean, flags=re.I)
    for env_var in PROVIDER_ENV_VARS.values():
        token = ""
        try:
            token = get_provider_token(next(provider for provider, item in PROVIDER_ENV_VARS.items() if item == env_var))[0]
        except StopIteration:
            token = ""
        if token:
            clean = clean.replace(token, "[redacted]")
    return clean


def _limited_payload_text(payload: dict[str, Any], limit: int = 12000) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... payload truncated by Mosaicly privacy/size guard ..."


def _parse_structured_response(text: str) -> tuple[dict[str, Any], str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, flags=re.S)
        raw = match.group(0) if match else raw
    try:
        parsed, warning = _loads_structured_json(raw)
    except (json.JSONDecodeError, ValueError, SyntaxError) as exc:
        return _parsed_from_unstructured_text(text, f"תשובת הספק לא הייתה JSON תקין: {exc}"), str(exc)
    recommendations = parsed.get("recommendations", [])
    clean_recommendations = []
    for item in recommendations[:5]:
        clean_recommendations.append(
            {
                "action": str(item.get("action", "inspect_data"))[:40],
                "title_he": str(item.get("title_he", ""))[:120],
                "reason_he": str(item.get("reason_he", ""))[:260],
                "expected_gain": max(0, min(100, int(float(item.get("expected_gain", 0) or 0)))),
                "privacy_safe": bool(item.get("privacy_safe", True)),
            }
        )
    best_index = int(parsed.get("best_recommendation_index", 0) or 0)
    if clean_recommendations:
        best_index = max(0, min(best_index, len(clean_recommendations) - 1))
    return {
        "summary_he": str(parsed.get("summary_he", ""))[:500],
        "risk_level": str(parsed.get("risk_level", "medium"))[:20],
        "recommendations": clean_recommendations,
        "best_recommendation_index": best_index,
        "parse_failed": bool(parsed.get("parse_failed", False)),
    }, warning


def _parse_action_selection_response(text: str) -> tuple[dict[str, Any], str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, flags=re.S)
        raw = match.group(0) if match else raw
    try:
        parsed, warning = _loads_structured_json(raw)
    except (json.JSONDecodeError, ValueError, SyntaxError) as exc:
        return {
            "summary_he": f"תשובת הספק לא הייתה JSON תקין: {exc}",
            "no_improvement": True,
            "selected_candidate_ids": [],
            "notes": [],
            "parse_failed": True,
        }, str(exc)

    notes = []
    for item in parsed.get("notes", []):
        if not isinstance(item, dict):
            continue
        notes.append(
            {
                "candidate_id": str(item.get("candidate_id", ""))[:40],
                "reason_he": str(item.get("reason_he", ""))[:260],
            }
        )
    candidate_ids = [str(value)[:40] for value in parsed.get("selected_candidate_ids", []) if str(value).strip()]
    return {
        "summary_he": str(parsed.get("summary_he", ""))[:600],
        "no_improvement": bool(parsed.get("no_improvement", False)),
        "selected_candidate_ids": candidate_ids[:8],
        "notes": notes[:8],
        "parse_failed": False,
    }, warning


def _parse_rule_recommendation_response(text: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, flags=re.S)
        raw = match.group(0) if match else raw
    try:
        parsed, warning = _loads_structured_json(raw)
    except (json.JSONDecodeError, ValueError, SyntaxError) as exc:
        return {
            "summary_he": f"תשובת הספק לא הייתה JSON תקין: {exc}",
            "risk_level": "medium",
            "reason_he": "לא ניתן היה לקרוא את המלצת הכללים.",
            "expected_gain": 0,
            "settings": _coerce_rule_settings({}, payload),
            "parse_failed": True,
        }, str(exc)

    return {
        "summary_he": str(parsed.get("summary_he", ""))[:600],
        "risk_level": str(parsed.get("risk_level", "medium"))[:20],
        "reason_he": str(parsed.get("reason_he", ""))[:600],
        "expected_gain": max(0, min(100, int(float(parsed.get("expected_gain", 0) or 0)))),
        "settings": _coerce_rule_settings(parsed.get("settings", {}), payload),
        "parse_failed": False,
    }, warning


def _loads_structured_json(raw: str) -> tuple[dict[str, Any], str]:
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed, ""
        raise ValueError("Structured response root must be an object.")
    except json.JSONDecodeError as original_error:
        repaired = _repair_json_text(raw)
        if repaired != raw:
            try:
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed, f"JSON תוקן אוטומטית אחרי שגיאת ספק: {original_error}"
            except json.JSONDecodeError:
                pass
            try:
                literal = ast.literal_eval(repaired)
                if isinstance(literal, dict):
                    return literal, f"תשובת הספק תוקנה ממבנה דמוי-Python ל-JSON תקין: {original_error}"
            except (ValueError, SyntaxError):
                pass
        literal = ast.literal_eval(raw)
        if isinstance(literal, dict):
            return literal, f"תשובת הספק הומרה ממבנה דמוי-Python ל-JSON תקין: {original_error}"
        raise ValueError("Structured response root must be an object.") from original_error


def _repair_json_text(raw: str) -> str:
    repaired = raw.strip().replace("“", '"').replace("”", '"').replace("’", "'")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', repaired)
    repaired = repaired.replace(": True", ": true").replace(": False", ": false").replace(": None", ": null")
    return repaired


def _parsed_from_unstructured_text(text: str, reason: str) -> dict[str, Any]:
    clean = re.sub(r"\s+", " ", text.strip())
    summary = clean[:500] if clean else "התקבלה תשובה מהספק, אך היא לא הייתה במבנה JSON תקין."
    recommendation = {
        "action": "inspect_data",
        "title_he": "בדקו את הסיכום שהתקבל",
        "reason_he": reason[:260],
        "expected_gain": 5,
        "privacy_safe": True,
    }
    return {
        "summary_he": summary,
        "risk_level": "medium",
        "recommendations": [recommendation],
        "best_recommendation_index": 0,
        "parse_failed": True,
    }


def _choose_best_action_selection(successful: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    candidate_by_id = {
        str(item.get("candidate_id")): item
        for item in payload.get("candidate_actions", [])
        if str(item.get("candidate_id", "")).strip()
    }
    notes_by_candidate: dict[str, list[str]] = {}
    provider_votes: dict[str, int] = {}
    for result in successful:
        parsed = result.get("parsed", {}) or {}
        note_lookup = {
            str(item.get("candidate_id")): str(item.get("reason_he", ""))
            for item in parsed.get("notes", [])
            if isinstance(item, dict)
        }
        for candidate_id in parsed.get("selected_candidate_ids", []):
            candidate_key = str(candidate_id)
            if candidate_key not in candidate_by_id:
                continue
            provider_votes[candidate_key] = provider_votes.get(candidate_key, 0) + 1
            if note_lookup.get(candidate_key):
                notes_by_candidate.setdefault(candidate_key, []).append(note_lookup[candidate_key])

    ranked_ids = sorted(
        provider_votes,
        key=lambda candidate_id: (
            provider_votes[candidate_id],
            _candidate_action_score(candidate_by_id[candidate_id]),
        ),
        reverse=True,
    )[:5]
    notes = [
        {
            "candidate_id": candidate_id,
            "reason_he": (notes_by_candidate.get(candidate_id, [""])[0])[:260],
        }
        for candidate_id in ranked_ids
    ]
    if ranked_ids:
        return {
            "summary_he": f"נבחרו {len(ranked_ids)} פעולות לאחר השוואת {len(successful)} ספקי AI.",
            "no_improvement": False,
            "selected_candidate_ids": ranked_ids,
            "notes": notes,
        }
    first = successful[0].get("parsed", {}) or {}
    return {
        "summary_he": first.get("summary_he", "ספקי ה-AI לא מצאו פעולה משפרת מתוך המועמדים שנבדקו."),
        "no_improvement": True,
        "selected_candidate_ids": [],
        "notes": [],
    }


def _candidate_action_score(item: dict[str, Any]) -> float:
    hard_gain = float(item.get("hard_before", 0) or 0) - float(item.get("hard_after", 0) or 0)
    friendship_gain = float(item.get("friendship_missing_before", 0) or 0) - float(
        item.get("friendship_missing_after", 0) or 0
    )
    delta = float(item.get("delta", 0) or 0)
    score_after = float(item.get("score_after", 0) or 0)
    return (hard_gain * 1000.0) + (friendship_gain * 120.0) + (delta * 10.0) + (score_after / 100.0)


def _choose_best_structured(successful: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for result in successful:
        parsed = result.get("parsed", {})
        for item in parsed.get("recommendations", []):
            if item.get("privacy_safe", True):
                candidates.append({**item, "provider": result.get("provider", "")})
    best_recommendation = max(candidates, key=lambda item: int(item.get("expected_gain", 0)), default={})
    first = successful[0].get("parsed", {})
    recommendations = sorted(candidates, key=lambda item: int(item.get("expected_gain", 0)), reverse=True)[:5]
    best_index = 0
    return {
        "summary_he": first.get("summary_he", "התקבל ניתוח AI מובנה."),
        "risk_level": first.get("risk_level", "medium"),
        "recommendations": recommendations,
        "best_recommendation_index": best_index,
        "best_recommendation": best_recommendation,
    }


def _local_structured_review(fallback: str, payload: dict[str, Any]) -> dict[str, Any]:
    penalties = payload.get("penalties", {})
    largest_penalty = max(penalties, key=lambda key: float(penalties.get(key, 0)), default="class_size")
    labels = {
        "class_size": "בדקו חלוקת גודל כיתות",
        "gender_balance": "בדקו איזון מגדר",
        "academic_balance": "בדקו איזון ציונים",
        "behavior_balance": "בדקו איזון התנהגות",
        "friendship": "בדקו בקשות חברים",
        "source_school": "בדקו פיזור בתי ספר",
        "hard_constraints": "טפלו בכללים המחייבים שנשברו",
    }
    recommendation = {
        "action": "inspect_data" if largest_penalty == "hard_constraints" else "move_or_swap",
        "title_he": labels.get(largest_penalty, "בדקו את דוח האיכות"),
        "reason_he": f"מדד האיכות שהכי דורש בדיקה כרגע הוא {largest_penalty}.",
        "expected_gain": 10,
        "privacy_safe": True,
        "provider": "local",
    }
    return {
        "summary_he": fallback,
        "risk_level": "medium",
        "recommendations": [recommendation],
        "best_recommendation_index": 0,
        "best_recommendation": recommendation,
    }


def _choose_best_rule_recommendation(successful: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    recommendations = [
        {**(item.get("parsed", {}) or {}), "provider": item.get("provider", "")}
        for item in successful
        if item.get("parsed")
    ]
    if not recommendations:
        return _local_rule_recommendation("נוצרה המלצת כללים מקומית.", payload)
    recommendations.sort(
        key=lambda item: (
            int(item.get("expected_gain", 0) or 0),
            _rule_consensus_score(item.get("settings", {}), recommendations),
        ),
        reverse=True,
    )
    best = dict(recommendations[0])
    best["settings"] = _coerce_rule_settings(best.get("settings", {}), payload)
    best["compared_recommendations"] = [
        {
            "provider": item.get("provider", ""),
            "expected_gain": item.get("expected_gain", 0),
            "reason_he": item.get("reason_he", ""),
        }
        for item in recommendations[:3]
    ]
    return best


def _rule_consensus_score(settings: dict[str, Any], recommendations: list[dict[str, Any]]) -> int:
    score = 0
    for other in recommendations:
        other_settings = other.get("settings", {}) or {}
        for key, value in settings.items():
            if key in other_settings and other_settings.get(key) == value:
                score += 1
    return score


def _local_rule_recommendation(fallback: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = _coerce_rule_settings(payload.get("current_settings", {}), payload)
    settings = dict(current)
    summary = payload.get("assignment_summary", {}) or {}
    penalties = summary.get("penalties", {}) or {}
    missing_friends = int(summary.get("missing_friends_count", 0) or 0)
    hard_violations = int(summary.get("hard_violations_count", 0) or 0)
    if missing_friends > 0:
        settings["friendship"] = True
        settings["friendship_required"] = True
        settings["friendship_first"] = False
        settings["friendship_weight"] = max(float(settings.get("friendship_weight", 2.2) or 2.2), 2.2)
        settings["search_restarts"] = max(int(settings.get("search_restarts", 6) or 6), 6)
        settings["max_iterations"] = max(int(settings.get("max_iterations", 220) or 220), 360)
        settings["stop_when_score_at_least"] = max(float(settings.get("stop_when_score_at_least", 92) or 92), 94)
    largest_penalty = max(penalties, key=lambda key: float(penalties.get(key, 0) or 0), default="")
    if largest_penalty == "gender_balance":
        settings["gender_weight"] = min(3.0, max(float(settings.get("gender_weight", 1.0) or 1.0), 1.4))
        settings["gender_tolerance"] = max(0, min(float(settings.get("gender_tolerance", 10) or 10), 8))
    elif largest_penalty in {"academic_balance", "subject_balance"}:
        settings["grade_weight"] = min(3.0, max(float(settings.get("grade_weight", 1.1) or 1.1), 1.45))
        settings["subject_weight"] = min(3.0, max(float(settings.get("subject_weight", 0.6) or 0.6), 0.85))
        settings["grade_tolerance"] = max(1, min(float(settings.get("grade_tolerance", 4) or 4), 3))
    elif largest_penalty == "source_school":
        settings["source_school_weight"] = min(3.0, max(float(settings.get("source_school_weight", 1.1) or 1.1), 1.2))
        settings["avoid_social_isolation"] = True
    if hard_violations:
        settings["hard_class_capacity"] = True
        settings["max_students_per_class"] = max(
            1,
            int(current.get("max_students_per_class", DEFAULT_RULE_SETTINGS["max_students_per_class"]) or 40),
        )
        settings["max_students_per_gender"] = max(
            1,
            int(current.get("max_students_per_gender", DEFAULT_RULE_SETTINGS["max_students_per_gender"]) or 20),
        )
    return {
        "summary_he": fallback,
        "risk_level": "medium" if hard_violations or missing_friends else "low",
        "reason_he": "המלצה מקומית לפי מדדי האיכות הבולטים בשיבוץ הנוכחי.",
        "expected_gain": 12 if missing_friends or penalties else 4,
        "settings": _coerce_rule_settings(settings, payload),
        "provider": "local",
    }


def _coerce_rule_settings(raw_settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    current = {
        **DEFAULT_RULE_SETTINGS,
        **(payload.get("current_settings", {}) if isinstance(payload.get("current_settings", {}), dict) else {}),
    }
    raw = raw_settings if isinstance(raw_settings, dict) else {}
    merged = {**current, **raw}
    bool_keys = {
        "balance_class_size",
        "balance_gender",
        "balance_grades",
        "balance_behavior",
        "spread_dominant_students",
        "friendship",
        "friendship_required",
        "friendship_first",
        "friendship_priority_order",
        "spread_source_school",
        "avoid_social_isolation",
        "hard_class_capacity",
        "allow_slow_large_search",
        "ai_assisted_assignment",
        "ai_auto_review",
    }
    float_ranges = {
        "class_size_weight": (0.0, 3.0),
        "gender_weight": (0.0, 3.0),
        "grade_weight": (0.0, 3.0),
        "subject_weight": (0.0, 3.0),
        "behavior_weight": (0.0, 3.0),
        "dominance_weight": (0.0, 3.0),
        "friendship_weight": (0.0, 3.0),
        "source_school_weight": (0.0, 3.0),
        "grade_tolerance": (0.0, 15.0),
        "gender_tolerance": (0.0, 40.0),
        "behavior_tolerance": (0.0, 1.0),
        "dominance_tolerance": (0.0, 20.0),
        "swap_search_min_score": (40.0, 100.0),
        "stop_when_score_at_least": (60.0, 99.0),
    }
    int_ranges = {
        "max_iterations": (80, 2000),
        "search_restarts": (1, 10),
        "max_students_per_class": (1, 60),
        "max_students_per_gender": (1, 40),
        "optimizer_time_limit_seconds": (1, 30),
        "random_seed": (1, 999999),
        "ai_provider_limit": (1, 3),
    }
    clean: dict[str, Any] = {}
    for key in RULE_SETTINGS_SCHEMA["properties"]:
        value = merged.get(key, DEFAULT_RULE_SETTINGS.get(key))
        if key in bool_keys:
            clean[key] = bool(value)
        elif key in float_ranges:
            clean[key] = _bounded_float(value, *float_ranges[key])
        elif key in int_ranges:
            clean[key] = int(round(_bounded_float(value, *int_ranges[key])))
        elif key == "optimizer_backend":
            text = str(value or "auto")
            clean[key] = text if text in {"auto", "local", "exact"} else "auto"
    clean["balance_class_size"] = True
    clean["hard_class_capacity"] = True
    if int(clean.get("max_students_per_class", 0) or 0) <= 0:
        clean["max_students_per_class"] = int(DEFAULT_RULE_SETTINGS["max_students_per_class"])
    if int(clean.get("max_students_per_gender", 0) or 0) <= 0:
        clean["max_students_per_gender"] = int(DEFAULT_RULE_SETTINGS["max_students_per_gender"])
    return clean


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))
