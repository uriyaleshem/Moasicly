from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from class_balancer.models.entities import ClassGroup, Student
from class_balancer.validation.normalization import CANONICAL_GENDERS, normalize_name_key


CRITICAL = "critical"
WARNING = "warning"
BEST_EFFORT_CRITICAL_CODES = frozenset(
    {
        "FRIENDSHIP_HARD_IMPOSSIBLE",
        "LOCKED_CLASS_CAPACITY_EXCEEDED",
        "LOCKED_GENDER_CAPACITY_EXCEEDED",
        "STUDENT_EMPTY_DOMAIN",
        "TOGETHER_GROUP_EMPTY_DOMAIN",
        "TOGETHER_GROUP_EXCEEDS_CLASS_CAPACITY",
        "TOGETHER_GROUP_LOCK_CONFLICT",
        "TOGETHER_SEPARATION_CONFLICT",
        "TOTAL_HARD_GENDER_CAPACITY_TOO_SMALL",
        "TOTAL_HARD_MAX_CAPACITY_TOO_SMALL",
        "TOTAL_HARD_MIN_CAPACITY_TOO_LARGE",
    }
)


@dataclass(slots=True)
class ConstraintConflict:
    code: str
    student_ids: list[int] = field(default_factory=list)
    class_ids: list[int] = field(default_factory=list)
    description_he: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FeasibilityIssue:
    code: str
    severity: str
    message_he: str
    student_ids: list[int] = field(default_factory=list)
    class_ids: list[int] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FeasibilityReport:
    ok: bool
    issues: list[FeasibilityIssue] = field(default_factory=list)
    conflicts: list[ConstraintConflict] = field(default_factory=list)
    student_domains: dict[int, list[int]] = field(default_factory=dict)
    together_components: list[list[int]] = field(default_factory=list)
    component_domains: list[list[int]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def critical_issues(self) -> list[FeasibilityIssue]:
        return [issue for issue in self.issues if issue.severity == CRITICAL]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "student_domains": {str(key): value for key, value in self.student_domains.items()},
            "together_components": self.together_components,
            "component_domains": self.component_domains,
            "metadata": self.metadata,
        }


class PreflightError(ValueError):
    def __init__(self, report: FeasibilityReport) -> None:
        self.report = report
        codes = ", ".join(issue.code for issue in report.critical_issues[:5])
        message = "השיבוץ לא הופעל כי נמצאו אילוצים קשיחים סותרים"
        if codes:
            message = f"{message}: {codes}"
        super().__init__(message)


def preflight_allows_best_effort(report: FeasibilityReport) -> bool:
    critical_issues = report.critical_issues
    return bool(critical_issues) and all(issue.code in BEST_EFFORT_CRITICAL_CODES for issue in critical_issues)


def run_preflight(
    students: list[Student],
    classes: list[ClassGroup],
    friendships: list[dict[str, Any]] | None = None,
    class_constraints: list[dict[str, Any]] | None = None,
    pair_constraints: dict[str, list[dict[str, Any]]] | None = None,
    settings: dict[str, Any] | None = None,
    locked_assignments: dict[int, int] | None = None,
) -> FeasibilityReport:
    friendships = friendships or []
    class_constraints = class_constraints or []
    pair_constraints = pair_constraints or {"together": [], "separation": []}
    settings = settings or {}
    locked_assignments = locked_assignments or {}

    issues: list[FeasibilityIssue] = []
    conflicts: list[ConstraintConflict] = []
    student_by_id = {int(student.id): student for student in students if student.id is not None}
    class_by_id = {int(group.id): group for group in classes if group.id is not None}
    all_class_ids = sorted(class_by_id)

    if not students:
        issues.append(
            _issue(
                "NO_STUDENTS",
                CRITICAL,
                "אין תלמידים לשיבוץ. יש לייבא תלמידים לפני הרצת השיבוץ.",
                actions=[{"type": "open_import"}],
            )
        )
    if not classes:
        issues.append(
            _issue(
                "NO_CLASSES",
                CRITICAL,
                "אין כיתות לשיבוץ. יש להגדיר לפחות כיתה אחת.",
                actions=[{"type": "open_class_setup"}],
            )
        )

    normalized_classes: dict[str, list[int]] = defaultdict(list)
    for group in classes:
        if group.id is not None:
            normalized_classes[normalize_name_key(group.name)].append(int(group.id))
    duplicate_class_ids = [
        class_id for ids in normalized_classes.values() if len(ids) > 1 for class_id in ids
    ]
    if duplicate_class_ids:
        issues.append(
            _issue(
                "DUPLICATE_NORMALIZED_CLASS_NAME",
                CRITICAL,
                "נמצאו שמות כיתות כפולים או זהים לאחר נרמול. יש לתת לכל כיתה שם ייחודי.",
                class_ids=sorted(duplicate_class_ids),
                actions=[{"type": "open_class_setup"}],
            )
        )

    class_name_to_id = {
        name: ids[0]
        for name, ids in normalized_classes.items()
        if name and len(ids) == 1
    }
    constraints_by_student: dict[int, dict[str, Any]] = {}
    for item in class_constraints:
        student_id = _to_int(item.get("student_id"))
        if student_id is None or student_id not in student_by_id:
            issues.append(
                _issue(
                    "UNKNOWN_STUDENT_REFERENCE",
                    CRITICAL,
                    "נמצא אילוץ כיתה שמפנה לתלמיד שאינו קיים בפרויקט.",
                    student_ids=[student_id] if student_id is not None else [],
                    details={"constraint": _safe_constraint_details(item)},
                    actions=[{"type": "open_validation"}],
                )
            )
            continue
        constraints_by_student[student_id] = item
        for field_name in ("allowed_classes", "forbidden_classes"):
            _, unknown = _resolve_class_refs(item.get(field_name, []), class_name_to_id, class_by_id)
            for value in unknown:
                issues.append(
                    _issue(
                        "UNKNOWN_CLASS_REFERENCE",
                        CRITICAL,
                        "נמצא אילוץ שמפנה לכיתה שאינה קיימת. יש לתקן את רשימת הכיתות המותרות או האסורות.",
                        student_ids=[student_id],
                        details={"field": field_name, "value": value},
                        actions=[{"type": "open_student_constraint", "student_id": student_id}],
                    )
                )
        locked_class_id = _to_int(item.get("locked_class_id"))
        if locked_class_id is not None and locked_class_id not in class_by_id:
            issues.append(
                _issue(
                    "UNKNOWN_LOCKED_CLASS_REFERENCE",
                    CRITICAL,
                    "נמצא תלמיד נעול לכיתה שאינה קיימת. יש להסיר את הנעילה או לבחור כיתה קיימת.",
                    student_ids=[student_id],
                    class_ids=[locked_class_id],
                    actions=[{"type": "open_student_constraint", "student_id": student_id}],
                )
            )

    for student_id, class_id in locked_assignments.items():
        if student_id not in student_by_id:
            issues.append(
                _issue(
                    "UNKNOWN_LOCKED_STUDENT_REFERENCE",
                    CRITICAL,
                    "גרסת השיבוץ הפעילה כוללת נעילה לתלמיד שאינו קיים עוד.",
                    student_ids=[student_id],
                    class_ids=[class_id],
                    actions=[{"type": "open_results"}],
                )
            )
        if class_id not in class_by_id:
            issues.append(
                _issue(
                    "UNKNOWN_LOCKED_CLASS_REFERENCE",
                    CRITICAL,
                    "גרסת השיבוץ הפעילה כוללת נעילה לכיתה שאינה קיימת עוד.",
                    student_ids=[student_id],
                    class_ids=[class_id],
                    actions=[{"type": "open_results"}],
                )
            )

    student_domains = _student_domains(
        student_by_id=student_by_id,
        class_ids=set(all_class_ids),
        class_by_id=class_by_id,
        class_name_to_id=class_name_to_id,
        constraints_by_student=constraints_by_student,
        locked_assignments=locked_assignments,
    )
    for student_id, domain in student_domains.items():
        if not domain:
            issues.append(
                _issue(
                    "STUDENT_EMPTY_DOMAIN",
                    CRITICAL,
                    "לתלמיד יש אילוצי כיתה שסוגרים את כל האפשרויות. יש לשנות כיתות מותרות, כיתות אסורות או נעילה.",
                    student_ids=[student_id],
                    actions=[{"type": "open_student_constraint", "student_id": student_id}],
                )
            )

    valid_together_rows, valid_separation_rows = _validate_pair_rows(
        pair_constraints,
        student_by_id,
        issues,
    )
    _validate_friendships(friendships, student_by_id, settings, issues)
    _validate_duplicate_names_when_relationships_exist(
        students,
        bool(friendships or valid_together_rows or valid_separation_rows),
        issues,
    )

    together_components = _together_components(student_by_id, valid_together_rows)
    component_index_by_student = {
        student_id: index
        for index, component in enumerate(together_components)
        for student_id in component
    }
    component_domains: list[list[int]] = []
    for component in together_components:
        domain = set(all_class_ids)
        for student_id in component:
            domain &= set(student_domains.get(student_id, []))
        sorted_domain = sorted(domain)
        component_domains.append(sorted_domain)
        if len(component) > 1 and not sorted_domain:
            issues.append(
                _issue(
                    "TOGETHER_GROUP_EMPTY_DOMAIN",
                    CRITICAL,
                    "תלמידים שחייבים להיות יחד מוגבלים לכיתות שונות ולכן אין כיתה אפשרית עבורם.",
                    student_ids=component,
                    actions=[
                        {"type": "open_student_constraint", "student_id": student_id}
                        for student_id in component
                    ],
                )
            )

    _validate_locked_components(
        together_components,
        constraints_by_student,
        locked_assignments,
        class_by_id,
        issues,
        conflicts,
    )
    _validate_separation_conflicts(
        valid_separation_rows,
        component_index_by_student,
        issues,
        conflicts,
    )
    _validate_capacity(
        students,
        classes,
        together_components,
        component_domains,
        constraints_by_student,
        locked_assignments,
        settings,
        issues,
    )
    _validate_hard_friendships(
        friendships,
        student_by_id,
        component_index_by_student,
        component_domains,
        valid_separation_rows,
        settings,
        issues,
    )

    ok = not any(issue.severity == CRITICAL for issue in issues)
    return FeasibilityReport(
        ok=ok,
        issues=issues,
        conflicts=conflicts,
        student_domains={student_id: sorted(domain) for student_id, domain in student_domains.items()},
        together_components=together_components,
        component_domains=component_domains,
        metadata={
            "student_count": len(students),
            "class_count": len(classes),
            "hard_class_capacity": bool(settings.get("hard_class_capacity", False)),
            "friendship_hard": _friendship_is_hard(settings),
        },
    )


def _student_domains(
    student_by_id: dict[int, Student],
    class_ids: set[int],
    class_by_id: dict[int, ClassGroup],
    class_name_to_id: dict[str, int],
    constraints_by_student: dict[int, dict[str, Any]],
    locked_assignments: dict[int, int],
) -> dict[int, set[int]]:
    domains: dict[int, set[int]] = {}
    for student_id in student_by_id:
        domain = set(class_ids)
        constraint = constraints_by_student.get(student_id, {})
        allowed, _ = _resolve_class_refs(constraint.get("allowed_classes", []), class_name_to_id, class_by_id)
        forbidden, _ = _resolve_class_refs(constraint.get("forbidden_classes", []), class_name_to_id, class_by_id)
        if constraint.get("allowed_classes"):
            domain &= allowed
        domain -= forbidden
        locked_class = locked_assignments.get(student_id)
        constraint_lock = _to_int(constraint.get("locked_class_id"))
        if constraint_lock is not None:
            locked_class = constraint_lock
        if locked_class is not None:
            domain &= {int(locked_class)} if int(locked_class) in class_by_id else set()
        domains[student_id] = domain
    return domains


def _validate_pair_rows(
    pair_constraints: dict[str, list[dict[str, Any]]],
    student_by_id: dict[int, Student],
    issues: list[FeasibilityIssue],
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    together_rows = _valid_pair_rows(
        pair_constraints.get("together", []),
        "together",
        student_by_id,
        issues,
    )
    separation_rows = _valid_pair_rows(
        pair_constraints.get("separation", []),
        "separation",
        student_by_id,
        issues,
    )
    for label, rows in (("together", together_rows), ("separation", separation_rows)):
        counts = Counter(tuple(sorted(pair)) for pair in rows)
        for pair, count in counts.items():
            if count > 1:
                issues.append(
                    _issue(
                        "DUPLICATE_PAIR_CONSTRAINT",
                        WARNING,
                        "נמצא אילוץ זוגי כפול. הוא לא משנה את התוצאה אך כדאי לנקות אותו.",
                        student_ids=list(pair),
                        details={"constraint_type": label, "count": count},
                        actions=[{"type": "open_relationships"}],
                    )
                )
    return together_rows, separation_rows


def _valid_pair_rows(
    rows: list[dict[str, Any]],
    label: str,
    student_by_id: dict[int, Student],
    issues: list[FeasibilityIssue],
) -> list[tuple[int, int]]:
    valid: list[tuple[int, int]] = []
    for item in rows:
        left = _to_int(item.get("student_id"))
        right = _to_int(item.get("other_student_id"))
        if left is None or right is None or left not in student_by_id or right not in student_by_id:
            issues.append(
                _issue(
                    "UNKNOWN_STUDENT_REFERENCE",
                    CRITICAL,
                    "נמצא אילוץ בין תלמידים שמפנה לתלמיד שאינו קיים בפרויקט.",
                    student_ids=[value for value in (left, right) if value is not None],
                    details={"constraint_type": label, "constraint": _safe_constraint_details(item)},
                    actions=[{"type": "open_relationships"}],
                )
            )
            continue
        if left == right:
            issues.append(
                _issue(
                    "SELF_PAIR_REFERENCE",
                    CRITICAL,
                    "נמצא אילוץ שבו תלמיד מפנה לעצמו. יש להסיר את האילוץ העצמי.",
                    student_ids=[left],
                    details={"constraint_type": label},
                    actions=[{"type": "open_relationships", "student_id": left}],
                )
            )
            continue
        valid.append((left, right))
    return valid


def _validate_friendships(
    friendships: list[dict[str, Any]],
    student_by_id: dict[int, Student],
    settings: dict[str, Any],
    issues: list[FeasibilityIssue],
) -> None:
    seen: Counter[tuple[int, int]] = Counter()
    friendship_hard = _friendship_is_hard(settings)
    for item in friendships:
        student_id = _to_int(item.get("student_id"))
        friend_id = _to_int(item.get("requested_friend_id"))
        if student_id is None or friend_id is None or student_id not in student_by_id or friend_id not in student_by_id:
            issues.append(
                _issue(
                    "UNKNOWN_FRIENDSHIP_STUDENT_REFERENCE",
                    CRITICAL,
                    "נמצאה בקשת חברות שמפנה לתלמיד שאינו קיים בפרויקט.",
                    student_ids=[value for value in (student_id, friend_id) if value is not None],
                    details={"request": _safe_constraint_details(item)},
                    actions=[{"type": "open_relationships"}],
                )
            )
            continue
        if student_id == friend_id:
            issues.append(
                _issue(
                    "SELF_FRIENDSHIP_REFERENCE",
                    CRITICAL if friendship_hard else WARNING,
                    "נמצאה בקשת חברות שבה תלמיד מבקש את עצמו. יש להסיר את הבקשה העצמית.",
                    student_ids=[student_id],
                    actions=[{"type": "open_relationships", "student_id": student_id}],
                )
            )
            continue
        seen[(student_id, friend_id)] += 1
    for pair, count in seen.items():
        if count > 1:
            issues.append(
                _issue(
                    "DUPLICATE_FRIENDSHIP_REQUEST",
                    WARNING,
                    "נמצאה בקשת חברות כפולה. היא תיספר פעם אחת בלבד.",
                    student_ids=list(pair),
                    details={"count": count},
                    actions=[{"type": "open_relationships", "student_id": pair[0]}],
                )
            )


def _validate_duplicate_names_when_relationships_exist(
    students: list[Student],
    relationships_exist: bool,
    issues: list[FeasibilityIssue],
) -> None:
    if not relationships_exist:
        return
    by_name: dict[str, list[int]] = defaultdict(list)
    for student in students:
        if student.id is None:
            continue
        key = normalize_name_key(student.display_name)
        if key:
            by_name[key].append(int(student.id))
    duplicate_ids = [student_id for ids in by_name.values() if len(ids) > 1 for student_id in ids]
    if duplicate_ids:
        issues.append(
            _issue(
                "AMBIGUOUS_STUDENT_NAME_REFERENCES",
                WARNING,
                "יש תלמידים עם שמות זהים בפרויקט שיש בו קשרי חברות או אילוצי זוגות. ודאו שהקשרים מפנים לתלמיד הנכון.",
                student_ids=sorted(duplicate_ids),
                actions=[{"type": "open_relationships"}],
            )
        )


def _together_components(
    student_by_id: dict[int, Student],
    together_rows: list[tuple[int, int]],
) -> list[list[int]]:
    parent = {student_id: student_id for student_id in student_by_id}

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in together_rows:
        union(left, right)
    grouped: dict[int, list[int]] = defaultdict(list)
    for student_id in sorted(student_by_id):
        grouped[find(student_id)].append(student_id)
    return [sorted(component) for component in grouped.values()]


def _validate_locked_components(
    components: list[list[int]],
    constraints_by_student: dict[int, dict[str, Any]],
    locked_assignments: dict[int, int],
    class_by_id: dict[int, ClassGroup],
    issues: list[FeasibilityIssue],
    conflicts: list[ConstraintConflict],
) -> None:
    for component in components:
        locks = _component_locks(component, constraints_by_student, locked_assignments, class_by_id)
        if len(locks) > 1:
            class_ids = sorted(locks)
            conflicts.append(
                ConstraintConflict(
                    code="TOGETHER_GROUP_LOCK_CONFLICT",
                    student_ids=component,
                    class_ids=class_ids,
                    description_he="תלמידים שחייבים להיות יחד נעולים ליותר מכיתה אחת.",
                )
            )
            issues.append(
                _issue(
                    "TOGETHER_GROUP_LOCK_CONFLICT",
                    CRITICAL,
                    "תלמידים שחייבים להיות יחד נעולים לכיתות שונות. יש להסיר אחת מהנעילות או לבטל את אילוץ היחד.",
                    student_ids=component,
                    class_ids=class_ids,
                    actions=[
                        {"type": "open_student_constraint", "student_id": student_id}
                        for student_id in component
                    ],
                )
            )


def _validate_separation_conflicts(
    separation_rows: list[tuple[int, int]],
    component_index_by_student: dict[int, int],
    issues: list[FeasibilityIssue],
    conflicts: list[ConstraintConflict],
) -> None:
    for left, right in separation_rows:
        left_component = component_index_by_student.get(left)
        right_component = component_index_by_student.get(right)
        if left_component is not None and left_component == right_component:
            conflicts.append(
                ConstraintConflict(
                    code="TOGETHER_SEPARATION_CONFLICT",
                    student_ids=sorted({left, right}),
                    description_he="אותם תלמידים נמצאים גם באילוץ יחד וגם באילוץ הפרדה.",
                )
            )
            issues.append(
                _issue(
                    "TOGETHER_SEPARATION_CONFLICT",
                    CRITICAL,
                    "נמצא זוג תלמידים שחייב להיות יחד וגם חייב להיות בנפרד. יש להסיר אחד מהאילוצים.",
                    student_ids=sorted({left, right}),
                    actions=[{"type": "open_relationships"}],
                )
            )


def _validate_capacity(
    students: list[Student],
    classes: list[ClassGroup],
    components: list[list[int]],
    component_domains: list[list[int]],
    constraints_by_student: dict[int, dict[str, Any]],
    locked_assignments: dict[int, int],
    settings: dict[str, Any],
    issues: list[FeasibilityIssue],
) -> None:
    class_by_id = {int(group.id): group for group in classes if group.id is not None}
    if classes:
        finite_caps = [_effective_class_max(group, settings) for group in classes if _effective_class_max(group, settings) > 0]
        if len(finite_caps) == len(classes) and sum(finite_caps) < len(students):
            issues.append(
                _issue(
                    "TOTAL_HARD_MAX_CAPACITY_TOO_SMALL",
                    CRITICAL,
                    "סך הקיבולת המקסימלית הקשיחה קטן ממספר התלמידים. יש להגדיל קיבולת או להפחית מספר תלמידים.",
                    class_ids=sorted(class_by_id),
                    actions=[{"type": "open_class_setup"}],
                    details={"capacity": sum(finite_caps), "students": len(students)},
                )
            )
        for component, domain in zip(components, component_domains):
            if not domain:
                continue
            max_values = [_effective_class_max(class_by_id[class_id], settings) for class_id in domain if class_id in class_by_id]
            if max_values and all(value > 0 and len(component) > value for value in max_values):
                issues.append(
                    _issue(
                        "TOGETHER_GROUP_EXCEEDS_CLASS_CAPACITY",
                        CRITICAL,
                        "קבוצת תלמידים שחייבת להיות יחד גדולה מכל כיתה מותרת לפי הקיבולת הקשיחה.",
                        student_ids=component,
                        class_ids=domain,
                        actions=[{"type": "open_class_setup"}, {"type": "open_relationships"}],
                    )
                )

        locked_counts: Counter[int] = Counter()
        for component in components:
            locks = _component_locks(component, constraints_by_student, locked_assignments, class_by_id)
            if len(locks) == 1:
                locked_counts[next(iter(locks))] += len(component)
        for class_id, count in locked_counts.items():
            group = class_by_id.get(class_id)
            max_students = _effective_class_max(group, settings) if group else 0
            if group and max_students and count > max_students:
                issues.append(
                    _issue(
                        "LOCKED_CLASS_CAPACITY_EXCEEDED",
                        CRITICAL,
                        "מספר התלמידים הנעולים לכיתה גדול מהקיבולת הקשיחה שלה. יש לשחרר נעילות או להגדיל קיבולת.",
                        class_ids=[class_id],
                        actions=[{"type": "open_results"}, {"type": "open_class_setup"}],
                        details={"locked_students": count, "max_students": max_students},
                    )
                )

        max_gender = _setting_int(settings, "max_students_per_gender", 0)
        if max_gender > 0:
            student_by_id = {int(student.id): student for student in students if student.id is not None}
            gender_totals = Counter(student.gender for student in students if student.gender in CANONICAL_GENDERS)
            for gender, count in gender_totals.items():
                if count > max_gender * len(classes):
                    issues.append(
                        _issue(
                            "TOTAL_HARD_GENDER_CAPACITY_TOO_SMALL",
                            CRITICAL,
                            f"סך הקיבולת למגדר {gender} קטן ממספר התלמידים במגדר הזה. יש להגדיל מגבלה או מספר כיתות.",
                            class_ids=sorted(class_by_id),
                            actions=[{"type": "open_rules"}],
                            details={"gender": gender, "capacity": max_gender * len(classes), "students": count},
                        )
                    )
            locked_gender_counts: dict[int, Counter[str]] = defaultdict(Counter)
            for component in components:
                locks = _component_locks(component, constraints_by_student, locked_assignments, class_by_id)
                if len(locks) != 1:
                    continue
                class_id = next(iter(locks))
                for student_id in component:
                    student = student_by_id.get(int(student_id))
                    if student and student.gender in CANONICAL_GENDERS:
                        locked_gender_counts[class_id][student.gender] += 1
            for class_id, counts in locked_gender_counts.items():
                for gender, count in counts.items():
                    if count > max_gender:
                        issues.append(
                            _issue(
                                "LOCKED_GENDER_CAPACITY_EXCEEDED",
                                CRITICAL,
                                f"מספר התלמידים הנעולים ממגדר {gender} לכיתה גדול מהמקסימום למגדר.",
                                class_ids=[class_id],
                                actions=[{"type": "open_results"}, {"type": "open_rules"}],
                                details={"gender": gender, "locked_students": count, "max_students_per_gender": max_gender},
                            )
                        )

    total_min = sum(int(group.min_students or 0) for group in classes)
    if total_min > len(students):
        issues.append(
            _issue(
                "TOTAL_HARD_MIN_CAPACITY_TOO_LARGE",
                CRITICAL,
                "סך המינימום שהוגדר לכיתות גדול ממספר התלמידים. יש להפחית מינימום כיתתי.",
                class_ids=sorted(class_by_id),
                actions=[{"type": "open_class_setup"}],
                details={"minimum": total_min, "students": len(students)},
            )
        )


def _effective_class_max(group: ClassGroup, settings: dict[str, Any]) -> int:
    class_max = int(group.max_students or 0)
    global_max = _setting_int(settings, "max_students_per_class", 0)
    values = [value for value in (class_max, global_max) if value > 0]
    return min(values) if values else 0


def _setting_int(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return max(0, int(settings.get(key, default) or 0))
    except (TypeError, ValueError):
        return max(0, int(default))


def _validate_hard_friendships(
    friendships: list[dict[str, Any]],
    student_by_id: dict[int, Student],
    component_index_by_student: dict[int, int],
    component_domains: list[list[int]],
    separation_rows: list[tuple[int, int]],
    settings: dict[str, Any],
    issues: list[FeasibilityIssue],
) -> None:
    if not _friendship_is_hard(settings):
        return
    requested_by_student: dict[int, set[int]] = defaultdict(set)
    for item in friendships:
        student_id = _to_int(item.get("student_id"))
        friend_id = _to_int(item.get("requested_friend_id"))
        if (
            student_id is None
            or friend_id is None
            or student_id == friend_id
            or student_id not in student_by_id
            or friend_id not in student_by_id
        ):
            continue
        requested_by_student[student_id].add(friend_id)

    separated_components = {
        tuple(sorted((component_index_by_student[left], component_index_by_student[right])))
        for left, right in separation_rows
        if left in component_index_by_student
        and right in component_index_by_student
        and component_index_by_student[left] != component_index_by_student[right]
    }
    for student_id, requested_ids in requested_by_student.items():
        source_component = component_index_by_student.get(student_id)
        if source_component is None:
            continue
        possible = False
        for friend_id in requested_ids:
            friend_component = component_index_by_student.get(friend_id)
            if friend_component is None:
                continue
            if source_component == friend_component:
                possible = True
                break
            component_pair = tuple(sorted((source_component, friend_component)))
            if component_pair in separated_components:
                continue
            if set(component_domains[source_component]) & set(component_domains[friend_component]):
                possible = True
                break
        if not possible:
            issues.append(
                _issue(
                    "FRIENDSHIP_HARD_IMPOSSIBLE",
                    CRITICAL,
                    "הוגדר שחברות היא חובה, אך לתלמיד אין אף חבר מבוקש שיכול להיות איתו באותה כיתה לפי האילוצים הקשיחים.",
                    student_ids=[student_id, *sorted(requested_ids)],
                    actions=[{"type": "open_relationships", "student_id": student_id}],
                )
            )


def _component_locks(
    component: list[int],
    constraints_by_student: dict[int, dict[str, Any]],
    locked_assignments: dict[int, int],
    class_by_id: dict[int, ClassGroup],
) -> set[int]:
    locks: set[int] = set()
    for student_id in component:
        if student_id in locked_assignments and locked_assignments[student_id] in class_by_id:
            locks.add(int(locked_assignments[student_id]))
        constraint_lock = _to_int(constraints_by_student.get(student_id, {}).get("locked_class_id"))
        if constraint_lock is not None and constraint_lock in class_by_id:
            locks.add(constraint_lock)
    return locks


def _resolve_class_refs(
    values: list[Any],
    class_name_to_id: dict[str, int],
    class_by_id: dict[int, ClassGroup],
) -> tuple[set[int], list[Any]]:
    resolved: set[int] = set()
    unknown: list[Any] = []
    for value in values or []:
        class_id = _to_int(value)
        if class_id is not None:
            if class_id in class_by_id:
                resolved.add(class_id)
            else:
                unknown.append(value)
            continue
        class_id = class_name_to_id.get(normalize_name_key(value))
        if class_id is not None:
            resolved.add(class_id)
        else:
            unknown.append(value)
    return resolved, unknown


def _friendship_is_hard(settings: dict[str, Any]) -> bool:
    return bool(
        settings.get("friendship_hard", False)
        or settings.get("hard_friendship", False)
        or settings.get("friendship_required", False)
    )


def _issue(
    code: str,
    severity: str,
    message_he: str,
    student_ids: list[int] | None = None,
    class_ids: list[int] | None = None,
    actions: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
) -> FeasibilityIssue:
    return FeasibilityIssue(
        code=code,
        severity=severity,
        message_he=message_he,
        student_ids=student_ids or [],
        class_ids=class_ids or [],
        actions=actions or [],
        details=details or {},
    )


def _safe_constraint_details(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(item).items()
        if key in {"id", "student_id", "other_student_id", "requested_friend_id", "priority"}
    }


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None
