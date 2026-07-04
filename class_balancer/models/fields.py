from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemField:
    name: str
    label_he: str
    required: bool = False
    help_text: str = ""


SYSTEM_FIELDS: list[SystemField] = [
    SystemField("internal_code", "מזהה תלמיד", False, "מומלץ לייבוא חוזר יציב"),
    SystemField("first_name", "שם פרטי", False),
    SystemField("last_name", "שם משפחה", False),
    SystemField("full_name", "שם מלא", False, "חובה אם אין שם פרטי ושם משפחה"),
    SystemField("gender", "מגדר", False, "נדרש לאיזון מגדר"),
    SystemField("source_school", "בית ספר מקור", False, "נדרש לפיזור בתי ספר"),
    SystemField("math_grade", "ציון מתמטיקה", False),
    SystemField("english_grade", "ציון אנגלית", False),
    SystemField("hebrew_grade", "ציון עברית", False),
    SystemField("average_grade", "ממוצע ציונים", False),
    SystemField("behavior_score", "התנהגות", False),
    SystemField("dominance_score", "דומיננטיות / אתגר", False, "נדרש לפיזור תלמידים דומיננטיים או מאתגרים"),
    SystemField("parent_notes", "הערות הורים", False),
    SystemField("teacher_notes", "הערות מורה", False),
    SystemField("interview_notes", "הערות ראיון", False),
    SystemField("friend_1", "חבר/ה 1", False),
    SystemField("friend_2", "חבר/ה 2", False),
    SystemField("friend_3", "חבר/ה 3", False),
    SystemField("allowed_classes", "כיתות מותרות", False),
    SystemField("forbidden_classes", "כיתות אסורות", False),
    SystemField("must_be_with", "חייב/ת להיות עם", False),
    SystemField("must_not_be_with", "אסור להיות עם", False),
]

FIELD_NAMES = [field.name for field in SYSTEM_FIELDS]

DEFAULT_RULE_SETTINGS = {
    "balance_class_size": True,
    "balance_gender": True,
    "balance_grades": True,
    "balance_behavior": True,
    "spread_dominant_students": True,
    "friendship": True,
    "friendship_required": True,
    "friendship_first": False,
    "friendship_priority_order": False,
    "spread_source_school": True,
    "avoid_social_isolation": True,
    "hard_class_capacity": True,
    "max_students_per_class": 40,
    "max_students_per_gender": 20,
    "class_size_weight": 1.2,
    "gender_weight": 1.0,
    "grade_weight": 1.1,
    "subject_weight": 0.6,
    "behavior_weight": 1.0,
    "dominance_weight": 0.8,
    "friendship_weight": 2.2,
    "source_school_weight": 1.1,
    "grade_tolerance": 4,
    "gender_tolerance": 10,
    "behavior_tolerance": 0.35,
    "dominance_tolerance": 5,
    "max_iterations": 220,
    "search_restarts": 6,
    "first_improvement_threshold": 80,
    "swap_search_min_score": 90,
    "stop_when_score_at_least": 92,
    "optimizer_backend": "auto",
    "optimizer_time_limit_seconds": 8,
    "random_seed": 42,
    "ai_assisted_assignment": True,
    "ai_external_allowed": False,
    "ai_auto_review": False,
    "ai_review_threshold": 78,
    "ai_provider_limit": 3,
    "allow_slow_large_search": False,
}
