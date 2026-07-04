from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class Project:
    id: int | None
    name: str
    grade_level: str = ""
    school_year: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["settings_json"] = data.pop("settings", {})
        return data


@dataclass(slots=True)
class ClassGroup:
    id: int | None
    project_id: int
    name: str
    min_students: int = 0
    max_students: int = 0
    target_students: int = 0
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Student:
    id: int | None
    project_id: int
    internal_code: str
    stable_key: str = ""
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    gender: str = ""
    source_school: str = ""
    math_grade: float | None = None
    english_grade: float | None = None
    hebrew_grade: float | None = None
    average_grade: float | None = None
    behavior_score: str = ""
    dominance_score: float | None = None
    parent_notes: str = ""
    teacher_notes: str = ""
    interview_notes: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    @property
    def display_name(self) -> str:
        if self.full_name:
            return self.full_name.strip()
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def grade_value(self) -> float | None:
        if self.average_grade is not None:
            return float(self.average_grade)
        subject_values = [
            float(value)
            for value in (self.math_grade, self.english_grade, self.hebrew_grade)
            if value is not None
        ]
        if subject_values:
            return sum(subject_values) / len(subject_values)
        return None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["raw_data_json"] = data.pop("raw_data", {})
        return data


@dataclass(slots=True)
class ValidationIssue:
    id: int | None
    project_id: int
    severity: str
    message: str
    student_id: int | None = None
    field_name: str = ""
    resolved: bool = False
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AssignmentVersion:
    id: int | None
    project_id: int
    name: str
    score_total: float
    score: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    is_active: bool = True
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["score_json"] = data.pop("score", {})
        return data


@dataclass(slots=True)
class Assignment:
    id: int | None
    version_id: int
    student_id: int
    class_id: int
    locked_manually: bool = False
    changed_manually: bool = False
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
