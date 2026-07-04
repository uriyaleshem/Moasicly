from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any

from class_balancer.db import Database
from class_balancer.models.entities import Student
from class_balancer.validation.normalization import behavior_to_number


class ReportService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def quality_report(self, project_id: int) -> dict[str, Any]:
        active = self.database.get_active_assignment_version(project_id)
        if not active:
            return {"has_assignment": False, "summary": "עדיין אין שיבוץ פעיל."}
        rows = self.database.get_active_assignment_rows(project_id)
        students = self.database.get_students(project_id)
        if len(rows) != len(students) or not students:
            return {
                "has_assignment": False,
                "summary": "גרסת השיבוץ הפעילה אינה מלאה. יש להריץ שיבוץ מחדש.",
                "version": active,
                "global_stats": self._global_stats(students, rows, active.get("score", {})),
            }
        score = active.get("score", {})
        missing = score.get("friendship", {}).get("missing", [])
        isolated = score.get("social_isolation", {}).get("isolated", [])
        schools = Counter(row.get("source_school", "") for row in rows if row.get("source_school"))
        return {
            "has_assignment": True,
            "version": active,
            "total_score": score.get("total_score", 0),
            "summary": score.get("summary", ""),
            "penalties": score.get("penalties", {}),
            "hard_violations": score.get("hard_violations", []),
            "missing_friends": missing,
            "isolated_students": isolated,
            "class_stats": score.get("class_stats", []),
            "global_stats": self._global_stats(students, rows, score),
            "school_distribution": dict(schools),
            "manager_text": self._manager_text(score, rows),
            "teacher_summary": self._teacher_summary(score, rows),
        }

    def conflicts_report(self, project_id: int) -> dict[str, Any]:
        report = self.quality_report(project_id)
        if not report.get("has_assignment"):
            return {"conflicts": [], "suggested_actions": ["הריצו שיבוץ אוטומטי כדי לזהות אילוצים מתנגשים."]}
        rows = self.database.get_active_assignment_rows(project_id)
        row_by_student = {int(row["student_id"]): row for row in rows}
        students = {int(student.id): student for student in self.database.get_students(project_id) if student.id is not None}
        conflicts: list[dict[str, Any]] = []
        for item in report.get("hard_violations", []):
            conflicts.append({"type": "hard_constraint", "severity": "critical", "message": item, "reason": "אילוץ קשיח לא מולא."})
        for item in report.get("missing_friends", []):
            student_id = int(item.get("student_id", 0) or 0)
            row = row_by_student.get(student_id, {})
            missing_friend_names = _missing_friend_names(item, students)
            current_class = row.get("class_name", "")
            conflicts.append(
                {
                    "type": "missing_friend",
                    "severity": "warning",
                    "message": f"{item.get('student_name')}: לא קיבל/ה חבר/ה מבוקש/ת.",
                    "reason": (
                        f"כיתה נוכחית: {current_class}. חברים חסרים לבדיקה: {missing_friend_names}."
                        if missing_friend_names
                        else item.get("reason", "")
                    ),
                    "student_id": student_id,
                    "class_name": current_class,
                }
            )
        for item in report.get("isolated_students", []):
            student_id = int(item.get("student_id", 0) or 0)
            row = row_by_student.get(student_id, {})
            conflicts.append(
                {
                    "type": "social_isolation",
                    "severity": "warning",
                    "message": f"{item.get('student_name')}: נשאר/ה לבד מבית הספר {item.get('school')}.",
                    "reason": f"כיתה נוכחית: {row.get('class_name', '-')}. כדאי לבדוק אם יש תלמיד/ה מאותו בית ספר בכיתה אחרת שניתן להחליף בלי לפגוע באיזון.",
                    "student_id": student_id,
                    "class_name": row.get("class_name", ""),
                    "school": item.get("school", ""),
                }
            )
        return {
            "conflicts": conflicts,
            "suggested_actions": _suggested_actions(report, row_by_student, students),
        }

    def compare_versions(self, project_id: int, left_version_id: int, right_version_id: int) -> dict[str, Any]:
        versions = {int(item["id"]): item for item in self.database.get_assignment_versions(project_id)}
        left = versions.get(left_version_id)
        right = versions.get(right_version_id)
        if not left or not right:
            return {"ok": False, "message": "אחת הגרסאות לא נמצאה."}
        left_rows = self.database.get_assignments(left_version_id)
        right_rows = self.database.get_assignments(right_version_id)
        left_assignments = {int(row["student_id"]): int(row["class_id"]) for row in left_rows}
        right_assignments = {int(row["student_id"]): int(row["class_id"]) for row in right_rows}
        students = {student.id: student for student in self.database.get_students(project_id)}
        classes = {group.id: group.name for group in self.database.get_classes(project_id)}
        moved = []
        for student_id, left_class in left_assignments.items():
            right_class = right_assignments.get(student_id)
            if right_class and right_class != left_class:
                student = students.get(student_id)
                moved.append(
                    {
                        "student_id": student_id,
                        "student_name": student.display_name if student else str(student_id),
                        "from_class": classes.get(left_class, str(left_class)),
                        "to_class": classes.get(right_class, str(right_class)),
                    }
                )
        return {
            "ok": True,
            "left": left,
            "right": right,
            "score_delta": round(float(right.get("score_total", 0)) - float(left.get("score_total", 0)), 2),
            "moved_students": moved,
            "moved_count": len(moved),
            "penalty_delta": _penalty_delta(left.get("score", {}), right.get("score", {})),
        }

    def _manager_text(self, score: dict[str, Any], rows: list[dict[str, Any]]) -> str:
        total = score.get("total_score", 0)
        stats = score.get("class_stats", [])
        missing = len(score.get("friendship", {}).get("missing", []))
        violations = len(score.get("hard_violations", []))
        return (
            f"סיכום שיבוץ: שובצו {len(rows)} תלמידים ב-{len(stats)} כיתות. "
            f"ציון האיכות הכללי הוא {total}. "
            f"{missing} תלמידים לא קיבלו חבר/ה מבוקש/ת. "
            f"נמצאו {violations} אילוצים קשיחים שדורשים בדיקה. "
            "הדוח נוצר מקומית מתוך מנוע השיבוץ, ללא שליחת שמות תלמידים לגורם חיצוני."
        )

    def _teacher_summary(self, score: dict[str, Any], rows: list[dict[str, Any]]) -> str:
        stats = score.get("class_stats", []) or []
        penalties = score.get("penalties", {}) or {}
        missing = len(score.get("friendship", {}).get("missing", []))
        isolated = len(score.get("social_isolation", {}).get("isolated", []))
        violations = len(score.get("hard_violations", []))
        lines = [
            f"שורה תחתונה: שובצו {len(rows)} תלמידים ב-{len(stats)} כיתות. ציון השיבוץ הכללי הוא {score.get('total_score', 0)} מתוך 100.",
            f"ירידה במדדים: {_penalty_sentence(penalties)}.",
            f"חברים ואילוצים: {missing} תלמידים לא קיבלו חבר מבוקש, {isolated} תלמידים מסומנים כבדידות חברתית אפשרית, ו-{violations} כללים מחייבים נשברו.",
            "ציוני כיתות:",
        ]
        for item in stats:
            lines.append(
                " · "
                + f"{item.get('name')}: {item.get('size', 0)} תלמידים, ציון כיתה {item.get('quality_score', '-')}, "
                + f"בנים/בנות {item.get('boys', 0)}/{item.get('girls', 0)}, "
                + f"ממוצע {item.get('avg_grade', '-')}, "
                + f"חברים חסרים {item.get('friends_missing', 0)}."
            )
        lines.append("המלצה: להתחיל מפעולות שמעלות את הציון בלי להוסיף כללים מחייבים שנשברים, ואז לבדוק שוב את רשימת האילוצים.")
        return "\n".join(lines)

    def _global_stats(self, students: list[Student], rows: list[dict[str, Any]], score: dict[str, Any]) -> dict[str, Any]:
        grade_values = [student.grade_value for student in students if student.grade_value is not None]
        math_values = [student.math_grade for student in students if student.math_grade is not None]
        english_values = [student.english_grade for student in students if student.english_grade is not None]
        hebrew_values = [student.hebrew_grade for student in students if student.hebrew_grade is not None]
        behavior_values = [behavior_to_number(student.behavior_score) for student in students]
        behavior_values = [value for value in behavior_values if value is not None]
        friendship = score.get("friendship", {})
        return {
            "student_count": len(students),
            "assigned_count": len(rows),
            "class_count": len(score.get("class_stats", [])),
            "gender_counts": dict(Counter(student.gender for student in students if student.gender)),
            "behavior_counts": dict(Counter(student.behavior_score for student in students if student.behavior_score)),
            "source_school_counts": dict(Counter(student.source_school for student in students if student.source_school)),
            "average_grade": round(mean(grade_values), 1) if grade_values else None,
            "math_average": round(mean(math_values), 1) if math_values else None,
            "english_average": round(mean(english_values), 1) if english_values else None,
            "hebrew_average": round(mean(hebrew_values), 1) if hebrew_values else None,
            "behavior_average": round(mean(behavior_values), 2) if behavior_values else None,
            "friends_satisfied_count": len(friendship.get("satisfied", [])),
            "friends_missing_count": len(friendship.get("missing", [])),
            "hard_violations_count": len(score.get("hard_violations", [])),
        }


def _penalty_delta(left_score: dict[str, Any], right_score: dict[str, Any]) -> dict[str, float]:
    left = left_score.get("penalties", {})
    right = right_score.get("penalties", {})
    keys = sorted(set(left) | set(right))
    return {key: round(float(right.get(key, 0)) - float(left.get(key, 0)), 2) for key in keys}


def _penalty_sentence(penalties: dict[str, Any]) -> str:
    if not penalties:
        return "לא נרשמו הפחתות במדדי האיכות"
    labels = {
        "class_size": "גודל כיתות",
        "gender_balance": "מגדר",
        "academic_balance": "ממוצע כללי",
        "subject_balance": "מקצועות",
        "behavior_balance": "התנהגות",
        "dominance_spread": "דומיננטיות",
        "friendship": "חברים",
        "source_school": "בתי ספר מקור",
        "hard_constraints": "כללים מחייבים",
    }
    parts = []
    for key, value in penalties.items():
        try:
            clean_value = round(float(value), 2)
        except (TypeError, ValueError):
            clean_value = value
        parts.append(f"{labels.get(key, key)} ירד {clean_value}")
    return ", ".join(parts)


def _missing_friend_names(item: dict[str, Any], students: dict[int, Student]) -> str:
    names = []
    for slot in item.get("slots", []):
        if slot.get("received"):
            continue
        friend = students.get(int(slot.get("friend_id", 0) or 0))
        if friend:
            names.append(friend.display_name)
    return ", ".join(names[:3])


def _suggested_actions(
    report: dict[str, Any],
    row_by_student: dict[int, dict[str, Any]],
    students: dict[int, Student],
) -> list[str]:
    actions: list[str] = []
    hard_count = len(report.get("hard_violations", []))
    if hard_count:
        actions.append(f"טפלו קודם ב-{hard_count} אילוצים קשיחים. שינוי ידני לא כדאי לפני שכללים מחייבים עומדים.")

    for item in report.get("missing_friends", [])[:4]:
        student_id = int(item.get("student_id", 0) or 0)
        row = row_by_student.get(student_id, {})
        names = _missing_friend_names(item, students)
        if names:
            actions.append(
                f"פתחו את כרטיס {item.get('student_name')} ובדקו העברה/החלפה שתקרב אותו/ה אל {names}; כיתה נוכחית: {row.get('class_name', '-')}."
            )
        else:
            actions.append(f"פתחו את כרטיס {item.get('student_name')} ובדקו הצעות החלפה חכמות עבור בקשת חברים שלא מולאה.")

    for item in report.get("isolated_students", [])[:3]:
        row = row_by_student.get(int(item.get("student_id", 0) or 0), {})
        actions.append(
            f"בדקו את {item.get('student_name')} מבית הספר {item.get('school')}: כיתה נוכחית {row.get('class_name', '-')}; חפשו החלפה עם תלמיד/ה מאותו בית ספר בכיתה אחרת."
        )

    weak_classes = sorted(
        [
            item
            for item in report.get("class_stats", [])
            if float(item.get("quality_score", 100) or 100) < 75
        ],
        key=lambda item: float(item.get("quality_score", 100) or 100),
    )
    for item in weak_classes[:2]:
        actions.append(
            f"כיתה {item.get('name')}: הציון הכיתתי {item.get('quality_score')}. בדקו בעיקר {item.get('quality_summary') or 'איזון גודל, חברים וציונים'}."
        )

    if not actions:
        actions.append("לא נמצאה התנגשות בולטת. בדקו את מדדי האיכות בדוח לפני שינוי ידני.")
    return actions[:8]
