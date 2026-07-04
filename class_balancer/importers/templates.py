from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def mapping_templates_path() -> Path:
    return Path.home() / ".class_balancer" / "mapping_templates.json"


def rule_templates_path() -> Path:
    return Path.home() / ".class_balancer" / "rule_templates.json"


def load_templates(path: Path | None = None) -> dict[str, Any]:
    template_path = path or mapping_templates_path()
    if not template_path.exists():
        return {"templates": []}
    try:
        return json.loads(template_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"templates": []}


def save_template(name: str, mapping: dict[str, str], headers: list[str], path: Path | None = None) -> Path:
    template_path = path or mapping_templates_path()
    template_path.parent.mkdir(parents=True, exist_ok=True)
    data = load_templates(template_path)
    templates = [item for item in data.get("templates", []) if item.get("name") != name]
    templates.insert(
        0,
        {
            "name": name,
            "mapping": mapping,
            "headers": headers,
        },
    )
    template_path.write_text(json.dumps({"templates": templates}, ensure_ascii=False, indent=2), encoding="utf-8")
    return template_path


def latest_template(path: Path | None = None) -> dict[str, Any] | None:
    templates = load_templates(path).get("templates", [])
    return templates[0] if templates else None


def save_rule_template(name: str, settings: dict[str, Any], path: Path | None = None) -> Path:
    template_path = path or rule_templates_path()
    template_path.parent.mkdir(parents=True, exist_ok=True)
    data = load_templates(template_path)
    templates = [item for item in data.get("templates", []) if item.get("name") != name]
    templates.insert(0, {"name": name, "settings": settings})
    template_path.write_text(json.dumps({"templates": templates}, ensure_ascii=False, indent=2), encoding="utf-8")
    return template_path


def latest_rule_template(path: Path | None = None) -> dict[str, Any] | None:
    return latest_template(path or rule_templates_path())
