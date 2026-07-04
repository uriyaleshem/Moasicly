from __future__ import annotations

from collections import Counter, defaultdict
from math import ceil, floor
from statistics import mean
from typing import Any

from class_balancer.models.entities import ClassGroup, Student
from class_balancer.validation.normalization import (
    CANONICAL_GENDERS,
    GENDER_FEMALE,
    GENDER_MALE,
    behavior_to_number,
    normalize_name_key,
)


def evaluate_assignment(
    students: list[Student],
    classes: list[ClassGroup],
    assignments: dict[int, int],
    friendships: list[dict[str, Any]] | None = None,
    class_constraints: list[dict[str, Any]] | None = None,
    pair_constraints: dict[str, list[dict[str, Any]]] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    friendships = friendships or []
    class_constraints = class_constraints or []
    pair_constraints = pair_constraints or {"together": [], "separation": []}
    settings = settings or {}
    student_by_id = {int(student.id): student for student in students if student.id is not None}
    class_by_id = {int(group.id): group for group in classes if group.id is not None}
    class_name_to_id = {normalize_name_key(group.name): int(group.id) for group in classes if group.id is not None}

    class_students: dict[int, list[Student]] = {class_id: [] for class_id in class_by_id}
    hard_violations: list[str] = []
    for student in students:
        if student.id is None:
            continue
        class_id = assignments.get(int(student.id))
        if class_id is None or class_id not in class_by_id:
            hard_violations.append(f"{student.internal_code}: לא שובץ/ה לכיתה קיימת.")
            continue
        class_students.setdefault(class_id, []).append(student)

    constraints_by_student = {
        int(item["student_id"]): item for item in class_constraints if item.get("student_id") is not None
    }
    for student_id, constraint in constraints_by_student.items():
        student = student_by_id.get(student_id)
        if not student:
            continue
        class_id = assignments.get(student_id)
        if class_id is None:
            continue
        locked_class_id = constraint.get("locked_class_id")
        if locked_class_id and int(locked_class_id) != class_id:
            hard_violations.append(
                f"{student.display_name}: נעול/ה לכיתה אחרת ולא שובץ/ה בה."
            )
        allowed = _resolve_class_refs(constraint.get("allowed_classes", []), class_name_to_id)
        forbidden = _resolve_class_refs(constraint.get("forbidden_classes", []), class_name_to_id)
        if allowed and class_id not in allowed:
            hard_violations.append(f"{student.display_name}: שובץ/ה לכיתה שאינה ברשימת הכיתות המותרות.")
        if forbidden and class_id in forbidden:
            hard_violations.append(f"{student.display_name}: שובץ/ה לכיתה אסורה.")

    for item in pair_constraints.get("together", []):
        left = int(item["student_id"])
        right = int(item["other_student_id"])
        if assignments.get(left) != assignments.get(right):
            left_name = student_by_id.get(left).display_name if student_by_id.get(left) else str(left)
            right_name = student_by_id.get(right).display_name if student_by_id.get(right) else str(right)
            hard_violations.append(f"{left_name} ו-{right_name}: הוגדרו ביחד אך שובצו בכיתות שונות.")
    for item in pair_constraints.get("separation", []):
        left = int(item["student_id"])
        right = int(item["other_student_id"])
        if assignments.get(left) and assignments.get(left) == assignments.get(right):
            left_name = student_by_id.get(left).display_name if student_by_id.get(left) else str(left)
            right_name = student_by_id.get(right).display_name if student_by_id.get(right) else str(right)
            hard_violations.append(f"{left_name} ו-{right_name}: הוגדרו בנפרד אך שובצו יחד.")

    target = _target_size(students, classes)
    ideal_min = floor(len(students) / len(classes)) if classes else 0
    ideal_max = ceil(len(students) / len(classes)) if classes else 0
    size_penalty = 0.0
    class_size_guard_issues = 0
    for group in classes:
        if group.id is None:
            continue
        size = len(class_students.get(int(group.id), []))
        group_target = group.target_students or target
        allowed_min = min(group_target, ideal_min) if group.target_students else ideal_min
        allowed_max = max(group_target, ideal_max) if group.target_students else ideal_max
        guard_min, guard_max = _class_size_guard_bounds(group, allowed_min, allowed_max, settings)
        if size < guard_min:
            class_size_guard_issues += guard_min - size
        elif size > guard_max:
            class_size_guard_issues += size - guard_max
        if size < allowed_min:
            size_penalty += (allowed_min - size) * 5.0
        elif size > allowed_max:
            size_penalty += (size - allowed_max) * 5.0
        if group.min_students and size < group.min_students:
            size_penalty += (group.min_students - size) * 3
        effective_max = _effective_class_max(group, settings)
        if effective_max and size > effective_max:
            hard_violations.append(f"{group.name}: הכיתה מעל המקסימום שהוגדר ({effective_max}).")

    max_gender = _setting_int(settings, "max_students_per_gender", 0)
    gender_cap_penalty = 0.0
    if max_gender > 0:
        for group in classes:
            if group.id is None:
                continue
            students_in_class = class_students.get(int(group.id), [])
            boys = sum(1 for student in students_in_class if student.gender == GENDER_MALE)
            girls = sum(1 for student in students_in_class if student.gender == GENDER_FEMALE)
            for label, count in ((GENDER_MALE, boys), (GENDER_FEMALE, girls)):
                excess = max(0, count - max_gender)
                if excess <= 0:
                    continue
                hard_violations.append(f"{group.name}: יש {count} תלמידים ממגדר {label}, מעל המקסימום {max_gender}.")
                gender_cap_penalty += excess * 4.0

    size_penalty *= _setting_float(settings, "class_size_weight", 1.0)
    gender_penalty = (_gender_penalty(class_students, settings) if settings.get("balance_gender", True) else 0.0) + gender_cap_penalty
    grade_penalty = _grade_penalty(class_students, settings) if settings.get("balance_grades", True) else 0.0
    subject_penalty = _subject_penalty(class_students, settings) if settings.get("balance_grades", True) else 0.0
    behavior_penalty = _behavior_penalty(class_students, settings) if settings.get("balance_behavior", True) else 0.0
    dominance_penalty = _dominance_penalty(class_students, settings) if settings.get("spread_dominant_students", True) else 0.0
    friendship_penalty, friendship_report = _friendship_penalty(
        assignments, student_by_id, friendships, settings
    ) if settings.get("friendship", True) else (0.0, {"satisfied": [], "missing": [], "total_with_requests": 0})
    weighted_friendship_penalty = friendship_penalty * _setting_float(settings, "friendship_weight", 2.2)
    friendship_missing_count = len(friendship_report.get("missing", []))
    friendship_report["full_coverage"] = bool(
        not friendship_report.get("total_with_requests", 0) or friendship_missing_count == 0
    )
    friendship_priority_active = _friendship_priority_active(settings)
    friendship_report["priority_missing"] = friendship_missing_count if friendship_priority_active else 0
    friendship_report["satisfied_percent"] = _percent(
        len(friendship_report.get("satisfied", [])),
        int(friendship_report.get("total_with_requests", 0) or 0),
    )
    if _friendship_required(settings):
        for item in friendship_report.get("missing", []) or []:
            student_name = str(item.get("student_name") or item.get("student_id") or "").strip()
            hard_violations.append(f"{student_name}: לא קיבל/ה אף חבר מבוקש למרות שחברים מוגדרים כחובה.")
    source_penalty, isolation_report = _source_school_penalty(
        class_students, settings
    ) if settings.get("spread_source_school", True) else (0.0, {"isolated": []})
    weighted_source_penalty = source_penalty * _setting_float(settings, "source_school_weight", 1.1)
    source_imbalance = float(isolation_report.get("imbalance_score", 0) or 0)
    isolated_count = len(isolation_report.get("isolated", []) or [])

    hard_penalty = len(hard_violations) * 25.0
    penalties = {
        "class_size": round(size_penalty, 2),
        "gender_balance": round(gender_penalty, 2),
        "academic_balance": round(grade_penalty, 2),
        "subject_balance": round(subject_penalty, 2),
        "behavior_balance": round(behavior_penalty, 2),
        "dominance_spread": round(dominance_penalty, 2),
        "friendship": round(friendship_penalty, 2),
        "source_school": round(source_penalty, 2),
        "hard_constraints": round(hard_penalty, 2),
    }
    weighted_penalties = {
        **penalties,
        "friendship": round(weighted_friendship_penalty, 2),
        "source_school": round(weighted_source_penalty, 2),
    }
    total_penalty = sum(penalties.values())
    weighted_total_penalty = sum(weighted_penalties.values())
    soft_penalty = weighted_total_penalty - hard_penalty
    display_penalty = _display_penalty(total_penalty, len(students))
    total_score = max(0.0, min(100.0, 100.0 - display_penalty))
    normalized_soft_penalty = _normalized_soft_penalty(soft_penalty, len(students))
    objective = {
        "hard_violation_count": len(hard_violations),
        "class_size_guard_issues": int(class_size_guard_issues),
        "students_without_any_requested_friend": friendship_missing_count,
        "priority_friendship_misses": int(friendship_report.get("priority_missing", 0) or 0),
        "source_school_imbalance": round(source_imbalance, 4),
        "isolated_source_school_students": isolated_count,
        "raw_soft_penalty": round(soft_penalty, 4),
        "raw_total_penalty": round(weighted_total_penalty, 4),
        "display_total_penalty": round(total_penalty, 4),
        "normalized_soft_penalty": round(normalized_soft_penalty, 6),
        "display_penalty": round(display_penalty, 4),
    }

    source_school_priority_active = _source_school_priority_active(settings)
    class_stats = _class_stats(class_students, classes, assignments, friendships, settings)
    return {
        "total_score": round(total_score, 2),
        "display_score": round(total_score, 2),
        "raw_objective": round(weighted_total_penalty, 4),
        "objective": objective,
        "penalties": penalties,
        "hard_violations": hard_violations,
        "friendship": friendship_report,
        "social_isolation": isolation_report,
        "class_stats": class_stats,
        "friendship_first_active": friendship_priority_active,
        "source_school_priority_active": source_school_priority_active,
        "student_reasons": _student_reasons(students, classes, assignments, friendships, class_stats, settings),
        "summary": _summary_text(total_score, hard_violations, friendship_report),
    }


def score_rank(score: dict[str, Any]) -> tuple[int, int, int, int, int, float, float, float]:
    objective = score.get("objective", {}) or {}
    friendship = score.get("friendship", {}) or {}
    hard_count = _int_metric(objective.get("hard_violation_count"), len(score.get("hard_violations", [])))
    class_size_guard_issues = _int_metric(objective.get("class_size_guard_issues"), 0)
    friendship_first = bool(score.get("friendship_first_active"))
    students_without_friend = (
        _int_metric(
            objective.get("students_without_any_requested_friend"),
            len(friendship.get("missing", []) or []),
        )
        if friendship_first
        else 0
    )
    priority_missing = _int_metric(objective.get("priority_friendship_misses"), _friendship_priority_missing(score))
    source_school_active = bool(score.get("source_school_priority_active"))
    source_school_imbalance = (
        _float_metric(objective.get("source_school_imbalance"), (score.get("penalties", {}) or {}).get("source_school", 0))
        if source_school_active
        else 0.0
    )
    isolated_source_school_students = (
        _int_metric(objective.get("isolated_source_school_students"), len((score.get("social_isolation", {}) or {}).get("isolated", []) or []))
        if source_school_active
        else 0
    )
    normalized_soft = _float_metric(
        objective.get("normalized_soft_penalty"),
        _float_metric(score.get("raw_objective"), 100.0 - float(score.get("total_score", 0) or 0)),
    )
    return (
        -hard_count,
        -students_without_friend,
        -class_size_guard_issues,
        -priority_missing,
        -isolated_source_school_students,
        -source_school_imbalance,
        -normalized_soft,
        float(score.get("total_score", 0) or 0),
    )


def _friendship_priority_missing(score: dict[str, Any]) -> int:
    friendship = score.get("friendship", {}) or {}
    try:
        return max(0, int(friendship.get("priority_missing", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _int_metric(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _float_metric(value: Any, default: float) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return max(0.0, float(default))


def _target_size(students: list[Student], classes: list[ClassGroup]) -> int:
    if not classes:
        return 0
    return round(len(students) / len(classes))


def _class_size_guard_bounds(
    group: ClassGroup,
    allowed_min: int,
    allowed_max: int,
    settings: dict[str, Any],
) -> tuple[int, int]:
    if not settings.get("balance_class_size", True) or _setting_float(settings, "class_size_weight", 1.0) <= 0:
        return 0, 1_000_000
    min_bound = int(group.min_students) if group.min_students else int(max(0, allowed_min))
    effective_max = _effective_class_max(group, settings)
    max_bound = min(effective_max, int(allowed_max)) if effective_max else int(allowed_max)
    if max_bound < min_bound:
        max_bound = min_bound
    return min_bound, max_bound


def _display_penalty(raw_penalty: float, student_count: int) -> float:
    if raw_penalty <= 0:
        return 0.0
    scale = max(500.0, 42.0 * max(1.0, float(student_count)) ** 0.5)
    return 100.0 * (raw_penalty / (raw_penalty + scale))


def _normalized_soft_penalty(raw_soft_penalty: float, student_count: int) -> float:
    if raw_soft_penalty <= 0:
        return 0.0
    return raw_soft_penalty / max(1.0, float(student_count))


def _resolve_class_refs(values: list[Any], class_name_to_id: dict[str, int]) -> set[int]:
    resolved: set[int] = set()
    for value in values or []:
        if isinstance(value, int):
            resolved.add(value)
            continue
        text = str(value).strip()
        if text.isdigit():
            resolved.add(int(text))
            continue
        class_id = class_name_to_id.get(normalize_name_key(text))
        if class_id:
            resolved.add(class_id)
    return resolved


def _gender_penalty(class_students: dict[int, list[Student]], settings: dict[str, Any]) -> float:
    ratios: list[float] = []
    for students in class_students.values():
        known = [student.gender for student in students if student.gender in CANONICAL_GENDERS]
        if not known:
            continue
        boys = sum(1 for gender in known if gender == GENDER_MALE)
        ratios.append(boys / len(known))
    if len(ratios) <= 1:
        return 0.0
    tolerance = _setting_float(settings, "gender_tolerance", 10) / 100.0
    return max(0.0, (max(ratios) - min(ratios)) - tolerance) * 18.0 * _setting_float(settings, "gender_weight", 1.0)


def _grade_penalty(class_students: dict[int, list[Student]], settings: dict[str, Any]) -> float:
    averages: list[float] = []
    for students in class_students.values():
        values = [student.grade_value for student in students if student.grade_value is not None]
        if values:
            averages.append(mean(values))
    if len(averages) <= 1:
        return 0.0
    tolerance = _setting_float(settings, "grade_tolerance", 4)
    return max(0.0, (max(averages) - min(averages)) - tolerance) * 0.42 * _setting_float(settings, "grade_weight", 1.0)


def _subject_penalty(class_students: dict[int, list[Student]], settings: dict[str, Any]) -> float:
    penalty = 0.0
    for attr in ("math_grade", "english_grade", "hebrew_grade"):
        averages: list[float] = []
        for students in class_students.values():
            values = [getattr(student, attr) for student in students if getattr(student, attr) is not None]
            if values:
                averages.append(mean(values))
        if len(averages) > 1:
            tolerance = _setting_float(settings, "grade_tolerance", 4) + 1.0
            penalty += max(0.0, (max(averages) - min(averages)) - tolerance) * 0.22
    return penalty * _setting_float(settings, "subject_weight", 0.6)


def _behavior_penalty(class_students: dict[int, list[Student]], settings: dict[str, Any]) -> float:
    averages: list[float] = []
    for students in class_students.values():
        values = [behavior_to_number(student.behavior_score) for student in students]
        values = [value for value in values if value is not None]
        if values:
            averages.append(mean(values))
    if len(averages) <= 1:
        return 0.0
    tolerance = _setting_float(settings, "behavior_tolerance", 0.35)
    return max(0.0, (max(averages) - min(averages)) - tolerance) * 5.2 * _setting_float(settings, "behavior_weight", 1.0)


def _dominance_penalty(class_students: dict[int, list[Student]], settings: dict[str, Any]) -> float:
    averages: list[float] = []
    for students in class_students.values():
        values = [student.dominance_score for student in students if student.dominance_score is not None]
        if values:
            averages.append(mean(float(value) for value in values))
    if len(averages) <= 1:
        return 0.0
    tolerance = _setting_float(settings, "dominance_tolerance", 5.0)
    return max(0.0, (max(averages) - min(averages)) - tolerance) * 1.15 * _setting_float(settings, "dominance_weight", 0.8)


def _friendship_penalty(
    assignments: dict[int, int],
    student_by_id: dict[int, Student],
    friendships: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    priority_mode = bool(settings.get("friendship_priority_order", False))
    requested_by_student: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for request in friendships:
        requested_by_student[int(request["student_id"])].append(
            (int(request["requested_friend_id"]), int(request.get("priority", 1) or 1))
        )

    missing: list[dict[str, Any]] = []
    satisfied: list[int] = []
    total_missing_weight = 0.0
    total_received = 0
    total_requested = 0
    students_without_any_friend = 0
    for student_id, requested in requested_by_student.items():
        class_id = assignments.get(student_id)
        if class_id is None:
            continue
        slot_results = []
        for friend_id, priority in requested:
            received = assignments.get(friend_id) == class_id
            weight = _friend_priority_weight(priority) if priority_mode else 1.0
            total_requested += 1
            if received:
                total_received += 1
            else:
                total_missing_weight += weight
            slot_results.append({"friend_id": friend_id, "priority": priority, "received": received})
        got_friend = any(item["received"] for item in slot_results)
        if got_friend:
            satisfied.append(student_id)
        if not got_friend:
            students_without_any_friend += 1
            student = student_by_id.get(student_id)
            missing.append(
                {
                    "student_id": student_id,
                    "student_name": student.display_name if student else str(student_id),
                    "reason": "אף אחד מהחברים המבוקשים לא שובץ באותה כיתה.",
                    "slots": slot_results,
                }
            )
    if priority_mode:
        penalty = total_missing_weight * 1.15 + students_without_any_friend * 1.25
    else:
        extra_missing_requests = max(0, (total_requested - total_received) - students_without_any_friend)
        penalty = students_without_any_friend * 2.6 + extra_missing_requests * 0.22
    return penalty, {
        "satisfied": satisfied,
        "missing": missing,
        "total_with_requests": len(requested_by_student),
        "total_requested": total_requested,
        "total_received": total_received,
        "priority_mode": priority_mode,
    }


def _source_school_penalty(class_students: dict[int, list[Student]], settings: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    schools = sorted(
        {
            student.source_school
            for students in class_students.values()
            for student in students
            if student.source_school
        }
    )
    penalty = 0.0
    imbalance_score = 0.0
    school_reports: list[dict[str, Any]] = []
    isolated: list[dict[str, Any]] = []
    priority_mode = _source_school_priority_active(settings)
    for school in schools:
        counts: list[int] = []
        total = 0
        for class_id, students in class_students.items():
            count = sum(1 for student in students if student.source_school == school)
            counts.append(count)
            total += count
            if count == 1 and total_students_from_school(class_students, school) > 1:
                lone = next(student for student in students if student.source_school == school)
                isolated.append(
                    {
                        "student_id": lone.id,
                        "student_name": lone.display_name,
                        "school": school,
                        "class_id": class_id,
                    }
                )
        if total > 1 and counts:
            class_count = max(1, len(counts))
            ideal = total / class_count
            low_target = floor(ideal)
            high_target = ceil(ideal)
            excess = sum(max(0, count - high_target) for count in counts)
            shortage = sum(max(0, low_target - count) for count in counts)
            spread = max(0, (max(counts) - min(counts)) - (1 if high_target > low_target else 0))
            deviation = sum(abs(count - ideal) for count in counts)
            if priority_mode:
                school_imbalance = excess * 5.0 + shortage * 3.0 + spread * 4.0 + deviation * 1.0
            else:
                school_imbalance = excess * 2.4 + shortage * 1.8 + spread * 2.0 + deviation * 0.45
            imbalance_score += school_imbalance
            penalty += school_imbalance
            comfortable_cap = max(1, high_target)
            penalty += sum(max(0, count - comfortable_cap) for count in counts) * (2.0 if priority_mode else 1.0)
            school_reports.append(
                {
                    "school": school,
                    "total": total,
                    "counts": counts,
                    "max": max(counts),
                    "min": min(counts),
                    "imbalance": round(school_imbalance, 3),
                }
            )
    if settings.get("avoid_social_isolation", True):
        isolation_penalty = 2.0 if priority_mode else 0.5
        penalty += len(isolated) * isolation_penalty
        imbalance_score += len(isolated) * isolation_penalty
    return penalty, {
        "isolated": isolated,
        "imbalance_score": round(imbalance_score, 4),
        "schools": school_reports,
    }


def _source_school_priority_active(settings: dict[str, Any]) -> bool:
    return bool(
        settings.get("spread_source_school", True)
        and (
            settings.get("source_school_first", False)
            or _setting_float(settings, "source_school_weight", 1.1) >= 2.0
        )
    )


def _friendship_required(settings: dict[str, Any]) -> bool:
    return bool(
        settings.get("friendship", True)
        and (
            settings.get("friendship_required", False)
            or settings.get("friendship_hard", False)
            or settings.get("hard_friendship", False)
        )
    )


def _friendship_priority_active(settings: dict[str, Any]) -> bool:
    return bool(settings.get("friendship", True) and settings.get("friendship_first", False))


def total_students_from_school(class_students: dict[int, list[Student]], school: str) -> int:
    return sum(1 for students in class_students.values() for student in students if student.source_school == school)


def _class_stats(
    class_students: dict[int, list[Student]],
    classes: list[ClassGroup],
    assignments: dict[int, int],
    friendships: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    requested_by_student: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for request in friendships:
        requested_by_student[int(request["student_id"])].append(
            (int(request["requested_friend_id"]), int(request.get("priority", 1) or 1))
        )

    all_students = [student for students in class_students.values() for student in students]
    target_size = round(len(all_students) / max(1, len(classes))) if classes else 0
    global_grade = _mean_or_none([student.grade_value for student in all_students])
    global_behavior = _mean_or_none([behavior_to_number(student.behavior_score) for student in all_students])
    known_gender = [student.gender for student in all_students if student.gender in CANONICAL_GENDERS]
    global_boys_ratio = (sum(1 for gender in known_gender if gender == GENDER_MALE) / len(known_gender)) if known_gender else None

    stats: list[dict[str, Any]] = []
    for group in classes:
        if group.id is None:
            continue
        class_id = int(group.id)
        students = class_students.get(class_id, [])
        grades = [student.grade_value for student in students if student.grade_value is not None]
        math_grades = [student.math_grade for student in students if student.math_grade is not None]
        english_grades = [student.english_grade for student in students if student.english_grade is not None]
        hebrew_grades = [student.hebrew_grade for student in students if student.hebrew_grade is not None]
        behaviors = [behavior_to_number(student.behavior_score) for student in students]
        behaviors = [value for value in behaviors if value is not None]
        dominance_values = [student.dominance_score for student in students if student.dominance_score is not None]
        gender_counts = Counter(student.gender for student in students if student.gender)
        behavior_counts = Counter(student.behavior_score for student in students if student.behavior_score)
        schools = Counter(student.source_school for student in students if student.source_school)
        friends_satisfied = 0
        friends_missing = 0
        total_with_friend_requests = 0
        for student in students:
            if student.id is None or student.id not in requested_by_student:
                continue
            total_with_friend_requests += 1
            if any(assignments.get(friend_id) == class_id for friend_id, _priority in requested_by_student[student.id]):
                friends_satisfied += 1
            else:
                friends_missing += 1
        class_quality = _class_quality_score(
            size=len(students),
            target_size=target_size,
            boys=sum(1 for student in students if student.gender == GENDER_MALE),
            girls=sum(1 for student in students if student.gender == GENDER_FEMALE),
            avg_grade=round(mean(grades), 1) if grades else None,
            avg_behavior=round(mean(behaviors), 2) if behaviors else None,
            friends_missing=friends_missing,
            missing_data=sum(
                1
                for student in students
                if not student.gender or student.grade_value is None or not student.behavior_score
            ),
            global_grade=global_grade,
            global_behavior=global_behavior,
            global_boys_ratio=global_boys_ratio,
            max_students_per_class=_effective_class_max(group, settings),
            max_students_per_gender=_setting_int(settings, "max_students_per_gender", 0),
        )
        stats.append(
            {
                "class_id": class_id,
                "name": group.name,
                "size": len(students),
                "boys": sum(1 for student in students if student.gender == GENDER_MALE),
                "girls": sum(1 for student in students if student.gender == GENDER_FEMALE),
                "gender_counts": dict(gender_counts),
                "missing_gender_count": sum(1 for student in students if not student.gender),
                "avg_grade": round(mean(grades), 1) if grades else None,
                "math_avg": round(mean(math_grades), 1) if math_grades else None,
                "english_avg": round(mean(english_grades), 1) if english_grades else None,
                "hebrew_avg": round(mean(hebrew_grades), 1) if hebrew_grades else None,
                "grade_bands": _grade_bands(grades),
                "math_bands": _grade_bands(math_grades),
                "english_bands": _grade_bands(english_grades),
                "hebrew_bands": _grade_bands(hebrew_grades),
                "missing_grade_count": len(students) - len(grades),
                "missing_math_count": len(students) - len(math_grades),
                "missing_english_count": len(students) - len(english_grades),
                "missing_hebrew_count": len(students) - len(hebrew_grades),
                "avg_behavior": round(mean(behaviors), 2) if behaviors else None,
                "behavior_counts": dict(behavior_counts),
                "missing_behavior_count": sum(1 for student in students if not student.behavior_score),
                "dominance_total": round(sum(float(value) for value in dominance_values), 2) if dominance_values else 0,
                "dominance_count": len(dominance_values),
                "dominance_average": round(mean(float(value) for value in dominance_values), 1) if dominance_values else None,
                "missing_dominance_count": len(students) - len(dominance_values),
                "friends_satisfied": friends_satisfied,
                "friends_missing": friends_missing,
                "total_with_friend_requests": total_with_friend_requests,
                "schools": dict(schools),
                "source_school_count": len(schools),
                "quality_score": class_quality["score"],
                "quality_summary": class_quality["summary"],
                "quality_penalties": class_quality["penalties"],
                "quality_label": "תקין" if len(students) else "ריק",
            }
        )
    return stats


def _grade_bands(values: list[float | int]) -> dict[str, int]:
    bands = {
        "90-100": 0,
        "80-89": 0,
        "70-79": 0,
        "60-69": 0,
        "0-59": 0,
    }
    for value in values:
        score = float(value)
        if score >= 90:
            bands["90-100"] += 1
        elif score >= 80:
            bands["80-89"] += 1
        elif score >= 70:
            bands["70-79"] += 1
        elif score >= 60:
            bands["60-69"] += 1
        else:
            bands["0-59"] += 1
    return bands


def _student_reasons(
    students: list[Student],
    classes: list[ClassGroup],
    assignments: dict[int, int],
    friendships: list[dict[str, Any]],
    class_stats: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, list[str]]:
    class_by_id = {int(group.id): group for group in classes if group.id is not None}
    requested_by_student: dict[int, list[int]] = defaultdict(list)
    for request in friendships:
        requested_by_student[int(request["student_id"])].append(int(request["requested_friend_id"]))

    reasons: dict[str, list[str]] = {}
    for student in students:
        if student.id is None:
            continue
        class_id = assignments.get(student.id)
        class_name = class_by_id.get(class_id).name if class_by_id.get(class_id) else "כיתה לא ידועה"
        student_reasons = (
            [f"שובץ/ה ל{class_name} כחלק מאיזון גודל הכיתות."]
            if settings.get("balance_class_size", True)
            else [f"שובץ/ה ל{class_name} לפי האילוצים והחלוקה שנבחרו."]
        )
        if settings.get("balance_gender", True) and student.gender:
            student_reasons.append("נלקח בחשבון באיזון מגדר.")
        if settings.get("balance_grades", True) and student.grade_value is not None:
            student_reasons.append("נלקח בחשבון באיזון ציונים.")
        if settings.get("spread_source_school", True) and student.source_school:
            student_reasons.append("נלקח בחשבון בפיזור בית ספר מקור.")
        friend_ids = requested_by_student.get(student.id, [])
        if friend_ids:
            if any(assignments.get(friend_id) == class_id for friend_id in friend_ids):
                student_reasons.append("קיבל/ה לפחות חבר/ה מבוקש/ת אחד/ת.")
            else:
                student_reasons.append("לא נמצא שיבוץ שנותן חבר מבוקש בלי לפגוע יותר באיזון.")
        reasons[str(student.id)] = student_reasons
    return reasons


def _setting_float(settings: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def _setting_int(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return max(0, int(settings.get(key, default) or 0))
    except (TypeError, ValueError):
        return max(0, int(default))


def _effective_class_max(group: ClassGroup, settings: dict[str, Any]) -> int:
    class_max = int(group.max_students or 0)
    global_max = _setting_int(settings, "max_students_per_class", 0)
    values = [value for value in (class_max, global_max) if value > 0]
    return min(values) if values else 0


def _summary_text(total_score: float, hard_violations: list[str], friendship_report: dict[str, Any]) -> str:
    if hard_violations:
        return f"השיבוץ נוצר עם {len(hard_violations)} אילוצים קשיחים שדורשים טיפול."
    missing = len(friendship_report.get("missing", []))
    if missing:
        return f"השיבוץ תקין מבחינת אילוצים קשיחים, אך {missing} תלמידים לא קיבלו חבר מבוקש."
    if total_score >= 85:
        return "השיבוץ מאוזן וטוב מאוד."
    if total_score >= 70:
        return "השיבוץ תקין, עם מקום לשיפור באיזון."
    return "השיבוץ עובד, אך כדאי לבדוק את דוח האיכות ולבצע תיקונים ידניים."


def _friend_priority_weight(priority: int) -> float:
    if priority <= 1:
        return 3.0
    if priority == 2:
        return 1.8
    return 1.0


def _mean_or_none(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return mean(clean) if clean else None


def _percent(part: int, total: int) -> int:
    if total <= 0:
        return 100
    return round((part / total) * 100)


def _class_quality_score(
    *,
    size: int,
    target_size: int,
    boys: int,
    girls: int,
    avg_grade: float | None,
    avg_behavior: float | None,
    friends_missing: int,
    missing_data: int,
    global_grade: float | None,
    global_behavior: float | None,
    global_boys_ratio: float | None,
    max_students_per_class: int,
    max_students_per_gender: int,
) -> dict[str, Any]:
    penalties: dict[str, float] = {}
    penalties["גודל"] = abs(size - target_size) * 3.0 if target_size else 0.0
    if max_students_per_class and size > max_students_per_class:
        penalties["חריגת גודל"] = (size - max_students_per_class) * 8.0
    known_gender = boys + girls
    if known_gender and global_boys_ratio is not None:
        penalties["מגדר"] = abs((boys / known_gender) - global_boys_ratio) * 16.0
    else:
        penalties["מגדר"] = 0.0
    if max_students_per_gender:
        penalties["חריגת מגדר"] = (max(0, boys - max_students_per_gender) + max(0, girls - max_students_per_gender)) * 8.0
    penalties["ציונים"] = abs(float(avg_grade) - global_grade) * 0.25 if avg_grade is not None and global_grade is not None else 0.0
    penalties["התנהגות"] = abs(float(avg_behavior) - global_behavior) * 2.8 if avg_behavior is not None and global_behavior is not None else 0.0
    penalties["חברים"] = friends_missing * 2.5
    penalties["נתונים חסרים"] = min(missing_data, 8) * 0.25
    total_penalty = sum(penalties.values())
    score = max(0.0, min(100.0, 100.0 - total_penalty))
    if score >= 90:
        summary = "חזקה ומאוזנת"
    elif score >= 78:
        summary = "טובה, עם נקודות קטנות לבדיקה"
    elif score >= 65:
        summary = "סבירה, כדאי לבדוק איזונים"
    else:
        summary = "דורשת בדיקה"
    return {
        "score": round(score, 1),
        "summary": summary,
        "penalties": {key: round(value, 2) for key, value in penalties.items()},
    }
