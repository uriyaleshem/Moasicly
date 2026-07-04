from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from class_balancer.models.entities import Student, ValidationIssue
from class_balancer.validation.normalization import normalize_name_key, relationship_name_keys, split_multi_value


def validate_students(
    project_id: int,
    students: list[Student],
    class_names: list[str],
    settings: dict[str, Any] | None = None,
) -> list[ValidationIssue]:
    settings = settings or {}
    issues: list[ValidationIssue] = []
    names: dict[str, Student | None] = {}
    class_keys = {normalize_name_key(name): name for name in class_names}

    for student in students:
        display_name = student.display_name
        name_key = normalize_name_key(display_name)
        if not name_key:
            issues.append(
                ValidationIssue(
                    id=None,
                    project_id=project_id,
                    student_id=student.id,
                    field_name="full_name",
                    severity="critical",
                    message=f"{student.internal_code}: חסר שם תלמיד.",
                )
            )
            continue
        if name_key in names:
            issues.append(
                ValidationIssue(
                    id=None,
                    project_id=project_id,
                    student_id=student.id,
                    field_name="full_name",
                    severity="critical",
                    message=f"כפילות תלמידים: {display_name}.",
                )
            )
        _add_name_keys(names, student)

    for student in students:
        mapped = student.raw_data.get("_mapped_values", {})
        if settings.get("balance_gender", True) and not student.gender:
            issues.append(_warning(project_id, student, "gender", "חסר מגדר. איזון מגדר יהיה פחות מדויק."))
        if settings.get("balance_grades", True) and student.grade_value is None:
            issues.append(_warning(project_id, student, "average_grade", "חסר ציון. איזון ציונים יתעלם מהתלמיד/ה."))
        if settings.get("friendship", True) and not any(split_multi_value(mapped.get(key, "")) for key in ("friend_1", "friend_2", "friend_3")):
            issues.append(_warning(project_id, student, "friend_1", "לא הוגדרו חברים מועדפים."))
        if settings.get("spread_source_school", True) and not student.source_school:
            issues.append(_warning(project_id, student, "source_school", "חסר בית ספר מקור."))

        for field_name in ("math_grade", "english_grade", "hebrew_grade", "average_grade"):
            value = getattr(student, field_name)
            if value is not None and not 0 <= float(value) <= 100:
                issues.append(
                    ValidationIssue(
                        id=None,
                        project_id=project_id,
                        student_id=student.id,
                        field_name=field_name,
                        severity="warning",
                        message=f"{student.display_name}: ציון חריג ({value}). בדקו אם הערך אמור להיות בין 0 ל-100.",
                    )
                )

        if student.dominance_score is not None and not 0 <= float(student.dominance_score) <= 100:
            issues.append(
                ValidationIssue(
                    id=None,
                    project_id=project_id,
                    student_id=student.id,
                    field_name="dominance_score",
                    severity="warning",
                    message=f"{student.display_name}: ערך דומיננטיות חריג ({student.dominance_score}). בדקו אם הערך אמור להיות בין 0 ל-100.",
                )
            )

        for friend_field in ("friend_1", "friend_2", "friend_3"):
            for friend_name in split_multi_value(mapped.get(friend_field, "")):
                if _relationship_checked_by_import(student, friend_field, friend_name):
                    continue
                friend_keys = relationship_name_keys(friend_name)
                if not friend_keys:
                    continue
                if any(key == normalize_name_key(student.display_name) for key in friend_keys):
                    issues.append(
                        ValidationIssue(
                            id=None,
                            project_id=project_id,
                            student_id=student.id,
                            field_name=friend_field,
                            severity="critical",
                            message=f"{student.display_name}: תלמיד/ה בחר/ה את עצמו/ה כחבר.",
                        )
                    )
                elif not _has_unambiguous_name(names, friend_keys):
                    issues.append(
                        ValidationIssue(
                            id=None,
                            project_id=project_id,
                            student_id=student.id,
                            field_name=friend_field,
                            severity="warning",
                            message=f"{student.display_name}: החבר/ה '{friend_name}' לא נמצא/ה ברשימת התלמידים.",
                        )
                    )

        for field_name, label in (("allowed_classes", "כיתה מותרת"), ("forbidden_classes", "כיתה אסורה")):
            for class_name in split_multi_value(mapped.get(field_name, "")):
                if normalize_name_key(class_name) not in class_keys:
                    issues.append(
                        ValidationIssue(
                            id=None,
                            project_id=project_id,
                            student_id=student.id,
                            field_name=field_name,
                            severity="critical",
                            message=f"{student.display_name}: {label} '{class_name}' לא קיימת בפרויקט.",
                        )
                    )

    issues.extend(_similar_name_infos(project_id, students))
    return issues


def _add_name_keys(names: dict[str, Student | None], student: Student) -> None:
    for value in (student.display_name, student.full_name, f"{student.first_name} {student.last_name}".strip()):
        for key in relationship_name_keys(value):
            current = names.get(key)
            if current is None and key in names:
                continue
            if current is not None and current is not student:
                names[key] = None
            else:
                names[key] = student


def _has_unambiguous_name(names: dict[str, Student | None], keys: list[str]) -> bool:
    return any(names.get(key) is not None for key in keys)


def _relationship_checked_by_import(student: Student, field_name: str, value: str) -> bool:
    checked = student.raw_data.get("_relationship_checked", {})
    field_values = checked.get(field_name, []) if isinstance(checked, dict) else []
    return normalize_name_key(value) in set(str(item) for item in field_values)


def _warning(project_id: int, student: Student, field_name: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        id=None,
        project_id=project_id,
        student_id=student.id,
        field_name=field_name,
        severity="warning",
        message=f"{student.display_name}: {message}",
    )


def _similar_name_infos(project_id: int, students: list[Student]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, left in enumerate(students):
        left_name = left.display_name
        left_key = normalize_name_key(left_name)
        if not left_key:
            continue
        for right in students[index + 1 :]:
            right_name = right.display_name
            right_key = normalize_name_key(right_name)
            if not right_key or left_key == right_key:
                continue
            score = SequenceMatcher(None, left_key, right_key).ratio()
            if score >= 0.9:
                issues.append(
                    ValidationIssue(
                        id=None,
                        project_id=project_id,
                        student_id=left.id,
                        field_name="full_name",
                        severity="info",
                        message=f"נמצאו שמות דומים: {left_name} / {right_name}. כדאי לוודא שאין כפילות.",
                    )
                )
    return issues
