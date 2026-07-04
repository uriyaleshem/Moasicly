from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from class_balancer.db import Database
from class_balancer.importers import ImportedTable, apply_mapping, load_table, suggest_mapping
from class_balancer.models.entities import Student, ValidationIssue
from class_balancer.validation import (
    build_students_from_mapping,
    normalize_name_key,
    relationship_name_keys,
    split_multi_value,
    validate_students,
)


class ImportService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.current_table: ImportedTable | None = None
        self.current_mapping: dict[str, str] = {}

    def load_preview(self, path: str | Path, sheet_name: str | None = None) -> ImportedTable:
        self.current_table = load_table(path, sheet_name=sheet_name, preview_limit=20)
        self.current_mapping = suggest_mapping(self.current_table.headers, self.current_table.rows)
        return self.current_table

    def load_full_current_table(self, sheet_name: str | None = None) -> ImportedTable:
        if not self.current_table:
            raise ValueError("לא נבחר קובץ ייבוא.")
        self.current_table = load_table(self.current_table.path, sheet_name=sheet_name or self.current_table.selected_sheet or None)
        return self.current_table

    def save_imported_students(self, project_id: int, mapping: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.current_table:
            raise ValueError("לא נטען קובץ.")
        full_table = self.load_full_current_table()
        mapping = mapping or self.current_mapping
        mapped_rows = apply_mapping(full_table.rows, mapping)
        included_rows, import_issues = _partition_import_rows(project_id, mapped_rows)
        students = build_students_from_mapping(project_id, included_rows)
        saved_students = self.database.replace_students(project_id, students)
        self.database.save_import_mappings(project_id, mapping)
        classes = self.database.get_classes(project_id)
        relationship_issues = self._save_relationships(project_id, saved_students, classes)

        project = self.database.get_project(project_id)
        issues = validate_students(
            project_id=project_id,
            students=saved_students,
            class_names=[group.name for group in classes],
            settings=project.settings if project else {},
        )
        issues.extend(import_issues)
        issues.extend(relationship_issues)
        self.database.replace_validation_issues(project_id, issues)
        return {
            "students_count": len(saved_students),
            "excluded_count": len(mapped_rows) - len(included_rows),
            "critical_count": sum(1 for issue in issues if issue.severity == "critical"),
            "warning_count": sum(1 for issue in issues if issue.severity == "warning"),
            "info_count": sum(1 for issue in issues if issue.severity == "info"),
        }

    def _save_relationships(self, project_id: int, students: list[Student], classes: list[Any]) -> list[ValidationIssue]:
        name_index = _name_index(students)
        class_index = {normalize_name_key(group.name): int(group.id) for group in classes if group.id is not None}
        issues: list[ValidationIssue] = []
        friendships: list[tuple[int, int, int]] = []
        class_constraints: list[dict[str, Any]] = []
        together: list[tuple[int, int, str]] = []
        separation: list[tuple[int, int, str]] = []

        for student in students:
            if student.id is None:
                continue
            mapped = student.raw_data.get("_mapped_values", {})
            for priority, field_name in enumerate(("friend_1", "friend_2", "friend_3"), start=1):
                for raw_friend_name in split_multi_value(mapped.get(field_name, "")):
                    for friend_name in _split_conjunction_if_resolvable(raw_friend_name, name_index):
                        friend = _resolve_student(friend_name, name_index)
                        if friend and friend.id and friend.id != student.id:
                            friendships.append((student.id, friend.id, priority))
                        else:
                            issues.append(
                                _student_issue(
                                    project_id,
                                    student,
                                    field_name,
                                    "warning",
                                    f"{student.display_name}: בקשת החברות '{friend_name}' לא נפתרה לתלמיד/ה קיים/ת ולכן לא נשמרה אוטומטית.",
                                )
                            )
                    _mark_relationship_checked(student, field_name, raw_friend_name)

            allowed = split_multi_value(mapped.get("allowed_classes", ""))
            forbidden = split_multi_value(mapped.get("forbidden_classes", ""))
            if allowed or forbidden:
                class_constraints.append(
                    {
                        "student_id": student.id,
                        "allowed_classes": allowed,
                        "forbidden_classes": forbidden,
                        "locked_class_id": _locked_class_id_for_allowed(allowed, class_index),
                    }
                )

            for other_name in _relationship_values(mapped.get("must_be_with", ""), name_index):
                other = _resolve_student(other_name, name_index)
                if other and other.id and other.id != student.id:
                    together.append((student.id, other.id, "ייבוא קובץ"))
                else:
                    issues.append(
                        _student_issue(
                            project_id,
                            student,
                            "must_be_with",
                            "critical",
                            f"{student.display_name}: אילוץ 'חייב להיות עם' עבור '{other_name}' לא נפתר ולכן השיבוץ חסום עד טיפול.",
                        )
                    )
            for other_name in _relationship_values(mapped.get("must_not_be_with", ""), name_index):
                other = _resolve_student(other_name, name_index)
                if other and other.id and other.id != student.id:
                    separation.append((student.id, other.id, "ייבוא קובץ"))
                else:
                    issues.append(
                        _student_issue(
                            project_id,
                            student,
                            "must_not_be_with",
                            "critical",
                            f"{student.display_name}: אילוץ 'לא לשבץ יחד עם' עבור '{other_name}' לא נפתר ולכן השיבוץ חסום עד טיפול.",
                        )
                    )

        self.database.replace_friendships(project_id, friendships)
        self.database.replace_class_constraints(project_id, class_constraints)
        self.database.replace_pair_constraints(project_id, together, separation)
        return issues


def _partition_import_rows(
    project_id: int,
    mapped_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[ValidationIssue]]:
    included: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []
    for row_number, row in enumerate(mapped_rows, start=2):
        raw_data = row.get("raw_data", {})
        if _is_questionnaire_only_row(row):
            display = str(row.get("full_name") or _raw_value(raw_data, "שם") or f"שורה {row_number}").strip()
            issues.append(
                ValidationIssue(
                    id=None,
                    project_id=project_id,
                    student_id=None,
                    field_name="include_in_assignment",
                    severity="warning",
                    message=f"{display}: הרשומה סומנה כ'שאלון בלבד' והוחרגה מהשיבוץ עד אימות ידני.",
                )
            )
            continue
        row.setdefault("raw_data", {})["_include_in_assignment"] = True
        included.append(row)
    return included, issues


def _is_questionnaire_only_row(row: dict[str, Any]) -> bool:
    raw_data = row.get("raw_data", {})
    source_value = _source_record_value(raw_data)
    if "שאלוןבלבד" not in normalize_name_key(source_value):
        return False
    return not str(row.get("internal_code") or "").strip()


def _source_record_value(raw_data: dict[str, Any]) -> str:
    for header, value in raw_data.items():
        header_key = normalize_name_key(header)
        if header_key in {"מקורהרשומה", "סוגהרשומה"}:
            return str(value or "")
    return ""


def _raw_value(raw_data: dict[str, Any], header: str) -> str:
    target = normalize_name_key(header)
    for key, value in raw_data.items():
        if normalize_name_key(key) == target:
            return str(value or "")
    return ""


def _name_index(students: list[Student]) -> dict[str, Student | None]:
    index: dict[str, Student | None] = {}
    for student in students:
        for value in (student.display_name, student.full_name, f"{student.first_name} {student.last_name}".strip()):
            for key in relationship_name_keys(value):
                if key not in index:
                    index[key] = student
                elif index[key] is not student:
                    index[key] = None
    return index


def _relationship_values(value: Any, index: dict[str, Student | None]) -> list[str]:
    values: list[str] = []
    for part in split_multi_value(value):
        resolved_parts = _split_conjunction_if_resolvable(part, index)
        for item in resolved_parts:
            if item and item not in values:
                values.append(item)
    return values


def _split_conjunction_if_resolvable(value: str, index: dict[str, Student | None]) -> list[str]:
    if _resolve_student(value, index):
        return [value]
    candidates = list(re.finditer(r"\s+ו(?=\S)", value))
    for match in candidates:
        left = _clean_relationship_fragment(value[: match.start()])
        right = _clean_relationship_fragment(value[match.end() :])
        if not left or not right:
            continue
        left_student = _resolve_student(left, index)
        right_student = _resolve_student(right, index)
        if left_student and right_student and left_student is not right_student:
            return [left, right]
    return [value]


def _clean_relationship_fragment(value: Any) -> str:
    return split_multi_value(value)[0] if split_multi_value(value) else ""


def _resolve_student(name: str, index: dict[str, Student | None]) -> Student | None:
    for key in relationship_name_keys(name):
        student = index.get(key)
        if student is not None:
            return student
    return _resolve_fuzzy_student(name, index)


def _resolve_fuzzy_student(name: str, index: dict[str, Student | None]) -> Student | None:
    scores: dict[int, tuple[float, Student]] = {}
    for source_key in relationship_name_keys(name):
        if not source_key:
            continue
        for candidate_key, student in index.items():
            if student is None:
                continue
            score = _relationship_similarity(source_key, candidate_key)
            if score < 0.84:
                continue
            student_id = int(student.id or 0)
            current = scores.get(student_id)
            if current is None or score > current[0]:
                scores[student_id] = (score, student)
    if not scores:
        return None
    ranked = sorted(scores.values(), key=lambda item: item[0], reverse=True)
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.035:
        return None
    return ranked[0][1]


def _relationship_similarity(left_key: str, right_key: str) -> float:
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    if left_key in right_key or right_key in left_key:
        short = min(len(left_key), len(right_key))
        long = max(len(left_key), len(right_key))
        if short >= 6:
            return max(SequenceMatcher(None, left_key, right_key).ratio(), short / long)
    return SequenceMatcher(None, left_key, right_key).ratio()


def _locked_class_id_for_allowed(allowed: list[str], class_index: dict[str, int]) -> int | None:
    if len(allowed) != 1:
        return None
    return class_index.get(normalize_name_key(allowed[0]))


def _student_issue(
    project_id: int,
    student: Student,
    field_name: str,
    severity: str,
    message: str,
) -> ValidationIssue:
    return ValidationIssue(
        id=None,
        project_id=project_id,
        student_id=student.id,
        field_name=field_name,
        severity=severity,
        message=message,
    )


def _mark_relationship_checked(student: Student, field_name: str, value: str) -> None:
    checked = student.raw_data.setdefault("_relationship_checked", {})
    field_values = checked.setdefault(field_name, [])
    key = normalize_name_key(value)
    if key and key not in field_values:
        field_values.append(key)
