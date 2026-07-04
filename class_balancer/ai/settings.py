from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROVIDER_ENV_VARS = {
    "OpenAI": "OPENAI_API_KEY",
    "Anthropic": "ANTHROPIC_API_KEY",
    "Gemini": "GEMINI_API_KEY",
}

PROVIDER_MODEL_ENV_VARS = {
    "OpenAI": "OPENAI_MODEL",
    "Anthropic": "ANTHROPIC_MODEL",
    "Gemini": "GEMINI_MODEL",
}

DEFAULT_PROVIDER_MODELS = {
    "OpenAI": "gpt-4.1-mini",
    "Anthropic": "claude-sonnet-4-5",
    "Gemini": "gemini-2.5-flash",
}

PROVIDER_MODEL_CANDIDATES = {
    "OpenAI": ["gpt-4.1-mini", "gpt-4o-mini"],
    "Anthropic": ["claude-sonnet-4-5", "claude-3-5-haiku-latest", "claude-3-5-sonnet-latest"],
    "Gemini": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-1.5-flash"],
}

KEYRING_SERVICE = "Mosaicly Class Balancer"


@dataclass(slots=True)
class ProviderToken:
    provider: str
    env_var: str
    configured: bool
    source: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "env_var": self.env_var,
            "configured": self.configured,
            "source": self.source,
            "model": self.model,
        }


def user_env_path() -> Path:
    return Path.home() / ".class_balancer" / ".env"


def project_env_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ".env"
    return Path.cwd() / ".env"


def load_ai_settings() -> dict[str, Any]:
    env_values = _merged_env()
    selected_provider = env_values.get("CLASS_BALANCER_AI_PROVIDER", "OpenAI")
    enabled = env_values.get("CLASS_BALANCER_AI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    providers = []
    for provider, env_var in PROVIDER_ENV_VARS.items():
        value, source = _lookup_token(env_var)
        providers.append(ProviderToken(provider, env_var, bool(value), source, get_provider_model(provider)).to_dict())
    configured_count = sum(1 for provider in providers if provider["configured"])
    selected_configured = any(
        provider["provider"] == selected_provider and provider["configured"] for provider in providers
    )
    return {
        "enabled": enabled,
        "selected_provider": selected_provider,
        "providers": providers,
        "configured_count": configured_count,
        "selected_configured": selected_configured,
        "ready": bool(enabled and selected_configured),
        "user_env_path": str(user_env_path()),
        "project_env_path": str(project_env_path()),
        "project_env_exists": project_env_path().exists(),
        "user_env_exists": user_env_path().exists(),
        "note": "AI לא משמש לשיבוץ עצמו. גם אם יש מפתח AI, נשלח רק סיכום נתונים אנונימי כאשר הרשאת הפרויקט פעילה.",
    }


def save_user_token(provider: str, token: str) -> Path:
    path = user_env_path()
    if _save_token_to_keyring(provider, token):
        _write_ai_provider_preference(path, provider)
    else:
        _write_provider_token(path, provider, token)
    return path


def save_project_token(provider: str, token: str) -> Path:
    path = project_env_path()
    _write_provider_token(path, provider, token)
    return path


def save_ai_token(provider: str, token: str, mirror_to_project: bool = False) -> dict[str, str]:
    paths = {"user_path": str(save_user_token(provider, token))}
    if mirror_to_project:
        try:
            paths["project_path"] = str(save_project_token(provider, token))
        except OSError as exc:
            paths["project_error"] = str(exc)
    return paths


def save_ai_model(provider: str, model: str, mirror_to_project: bool = False) -> dict[str, str]:
    if provider not in PROVIDER_MODEL_ENV_VARS:
        raise ValueError("ספק AI לא נתמך.")
    paths = {"user_path": str(_write_provider_model(user_env_path(), provider, model))}
    if mirror_to_project:
        try:
            paths["project_path"] = str(_write_provider_model(project_env_path(), provider, model))
        except OSError as exc:
            paths["project_error"] = str(exc)
    return paths


def _write_provider_token(path: Path, provider: str, token: str) -> None:
    env_var = PROVIDER_ENV_VARS.get(provider)
    if not env_var:
        raise ValueError("ספק AI לא נתמך.")
    path.parent.mkdir(parents=True, exist_ok=True)
    values = _read_env_file(path)
    values[env_var] = _clean_env_value(token, "מפתח AI")
    values["CLASS_BALANCER_AI_PROVIDER"] = provider
    _write_env_file(path, values)


def _write_ai_provider_preference(path: Path, provider: str) -> None:
    if provider not in PROVIDER_ENV_VARS:
        raise ValueError("ספק AI לא נתמך.")
    path.parent.mkdir(parents=True, exist_ok=True)
    values = _read_env_file(path)
    for env_var in PROVIDER_ENV_VARS.values():
        values.pop(env_var, None)
    values["CLASS_BALANCER_AI_PROVIDER"] = provider
    _write_env_file(path, values)


def _write_provider_model(path: Path, provider: str, model: str) -> Path:
    model_env_var = PROVIDER_MODEL_ENV_VARS.get(provider)
    if not model_env_var:
        raise ValueError("ספק AI לא נתמך.")
    clean_model = _clean_env_value(model, "שם מודל")
    if not clean_model:
        raise ValueError("שם מודל ריק.")
    path.parent.mkdir(parents=True, exist_ok=True)
    values = _read_env_file(path)
    values[model_env_var] = clean_model
    _write_env_file(path, values)
    return path


def save_ai_preferences(enabled: bool, provider: str, mirror_to_project: bool = False) -> Path:
    if provider not in PROVIDER_ENV_VARS:
        raise ValueError("ספק AI לא נתמך.")
    path = user_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    values = _read_env_file(path)
    values["CLASS_BALANCER_AI_ENABLED"] = "true" if enabled else "false"
    values["CLASS_BALANCER_AI_PROVIDER"] = provider
    _write_env_file(path, values)
    if mirror_to_project:
        project_path = project_env_path()
        project_values = _read_env_file(project_path)
        project_values["CLASS_BALANCER_AI_ENABLED"] = "true" if enabled else "false"
        project_values["CLASS_BALANCER_AI_PROVIDER"] = provider
        try:
            _write_env_file(project_path, project_values)
        except OSError:
            pass
    return path


def get_provider_token(provider: str) -> tuple[str, str]:
    env_var = PROVIDER_ENV_VARS.get(provider)
    if not env_var:
        return "", ""
    return _lookup_token(env_var)


def get_provider_model(provider: str) -> str:
    model_env_var = PROVIDER_MODEL_ENV_VARS.get(provider, "")
    default_model = DEFAULT_PROVIDER_MODELS.get(provider, "")
    if not model_env_var:
        return default_model
    env_values = _merged_env()
    return env_values.get(model_env_var, default_model).strip() or default_model


def provider_model_candidates(provider: str) -> list[str]:
    current = get_provider_model(provider)
    candidates = [current, *PROVIDER_MODEL_CANDIDATES.get(provider, []), DEFAULT_PROVIDER_MODELS.get(provider, "")]
    unique: list[str] = []
    for model in candidates:
        clean = str(model or "").strip()
        if clean and clean not in unique:
            unique.append(clean)
    return unique


def _lookup_token(env_var: str) -> tuple[str, str]:
    if os.environ.get(env_var):
        return os.environ[env_var], f"environment:{env_var}"
    provider = next((name for name, candidate in PROVIDER_ENV_VARS.items() if candidate == env_var), "")
    if provider:
        token = _load_token_from_keyring(provider)
        if token:
            return token, "os-keychain"
    for path in (user_env_path(), project_env_path()):
        values = _read_env_file(path)
        if values.get(env_var):
            return values[env_var], str(path)
    return "", ""


def _merged_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (project_env_path(), user_env_path()):
        values.update(_read_env_file(path))
    values.update({key: value for key, value in os.environ.items() if key.startswith("CLASS_BALANCER_")})
    return values


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Mosaicly local settings",
        "# AI is optional and never performs the final assignment.",
    ]
    for key, value in values.items():
        clean_key = _clean_env_key(key)
        clean_value = _clean_env_value(value, clean_key)
        lines.append(f"{clean_key}={clean_value}")
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass
    os.replace(temp_path, path)


def _save_token_to_keyring(provider: str, token: str) -> bool:
    clean_token = _clean_env_value(token, "מפתח AI")
    if not clean_token or importlib.util.find_spec("keyring") is None:
        return False
    try:
        import keyring  # type: ignore[import-not-found]

        keyring.set_password(KEYRING_SERVICE, PROVIDER_ENV_VARS[provider], clean_token)
        return True
    except Exception:
        return False


def _load_token_from_keyring(provider: str) -> str:
    if importlib.util.find_spec("keyring") is None:
        return ""
    try:
        import keyring  # type: ignore[import-not-found]

        return keyring.get_password(KEYRING_SERVICE, PROVIDER_ENV_VARS[provider]) or ""
    except Exception:
        return ""


def _clean_env_key(value: str) -> str:
    clean = str(value or "").strip()
    if not clean or not clean.replace("_", "").isalnum() or any(char in clean for char in "\r\n="):
        raise ValueError("שם הגדרת AI לא תקין.")
    return clean


def _clean_env_value(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if "\r" in clean or "\n" in clean:
        raise ValueError(f"{label} לא יכול להכיל שורה חדשה.")
    return clean
