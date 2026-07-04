from __future__ import annotations

from typing import Any

from class_balancer.models.entities import Student


def anonymize_assignment_payload(
    students: list[Student],
    assignments: dict[int, int],
    class_names: dict[int, str],
    friends_received: dict[int, int] | None = None,
) -> dict[str, Any]:
    friends_received = friends_received or {}
    anonymized = []
    local_lookup: dict[str, str] = {}
    for index, student in enumerate(students, start=1):
        if student.id is None:
            continue
        anonymous_id = f"S{index:03d}"
        local_lookup[anonymous_id] = student.display_name
        anonymized.append(
            {
                "id": anonymous_id,
                "gender": _gender_for_ai(student.gender),
                "average_grade": student.grade_value,
                "behavior_score": _behavior_for_ai(student.behavior_score),
                "current_class": class_names.get(assignments.get(student.id, 0), ""),
                "friends_received": friends_received.get(student.id, 0),
            }
        )
    return {
        "payload": {"students": anonymized},
        "local_lookup": local_lookup,
        "privacy_note": "השמות וההערות נשארים מקומית. ל-AI נשלחים רק מזהים אנונימיים ושדות מינימליים.",
    }


def _gender_for_ai(value: str) -> str:
    if value == "בן":
        return "male"
    if value == "בת":
        return "female"
    return ""


def _behavior_for_ai(value: str) -> str:
    mapping = {"גבוהה": "high", "בינונית": "medium", "נמוכה": "low"}
    return mapping.get(value, "")

