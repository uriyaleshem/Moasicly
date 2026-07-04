from __future__ import annotations

from typing import Any

from class_balancer.db import Database
from class_balancer.models.fields import DEFAULT_RULE_SETTINGS

DEFAULT_CLASS_COUNT = 6
DEFAULT_CLASS_START = 2


class ProjectService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_project(
        self,
        name: str,
        grade_level: str,
        school_year: str,
        class_count: int,
        class_names_text: str = "",
        notes: str = "",
    ) -> int:
        class_names = parse_class_names(class_names_text)
        if not class_names:
            class_names = default_class_names(grade_level, class_count)
        return self.database.create_project(
            name=name or "פרויקט שיבוץ חדש",
            grade_level=grade_level,
            school_year=school_year,
            class_names=class_names,
            notes=notes,
            settings=DEFAULT_RULE_SETTINGS,
        )

    def update_classes(self, project_id: int, class_names_text: str) -> None:
        class_names = parse_class_names(class_names_text)
        if class_names:
            self.database.replace_classes(project_id, class_names)

    def update_settings(self, project_id: int, settings: dict[str, Any]) -> None:
        self.database.update_project_settings(project_id, settings)

    def delete_project(self, project_id: int) -> bool:
        return self.database.delete_project(project_id)


def parse_class_names(text: str) -> list[str]:
    separators = ["\n", ";", "|"]
    normalized = text or ""
    for separator in separators:
        normalized = normalized.replace(separator, ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def default_class_names(grade_level: str, class_count: int = DEFAULT_CLASS_COUNT) -> list[str]:
    count = max(1, class_count or DEFAULT_CLASS_COUNT)
    grade = (grade_level or "").strip()
    if grade:
        return [f"{grade}{index}" for index in range(DEFAULT_CLASS_START, DEFAULT_CLASS_START + count)]
    return [f"כיתה {index}" for index in range(1, count + 1)]
