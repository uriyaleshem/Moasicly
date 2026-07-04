from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from class_balancer.models.fields import FIELD_NAMES
from class_balancer.validation.normalization import parse_grade

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - optional dependency
    fuzz = None


SYNONYMS: dict[str, list[str]] = {
    "internal_code": ["מזהה", "מזהה תלמיד", "מספר תלמיד", "קוד תלמיד", "תז", "ת.ז", "תעודת זהות", "מספר זהות", "id", "student id", "student number"],
    "first_name": ["שם פרטי", "פרטי", "שם הילד", "first name", "firstname", "given name"],
    "last_name": ["שם משפחה", "משפחה", "שם משפ'", "last name", "lastname", "surname"],
    "full_name": ["שם מלא", "שם", "שם התלמיד", "תלמיד", "student name", "full name", "name"],
    "gender": ["מין", "מגדר", "בן/בת", "זכר נקבה", "gender", "sex"],
    "source_school": ["בית ספר", "בית ספר מקור", "בית ספר קודם", "שם בית ספר נוכחי", "יסודי", "source school", "previous school"],
    "math_grade": ["מתמטיקה", "ציון מתמטיקה", "math", "math grade"],
    "english_grade": ["אנגלית", "ציון אנגלית", "english", "english grade"],
    "hebrew_grade": ["עברית", "שפה", "לשון", "hebrew", "language grade"],
    "average_grade": ["ממוצע", "ממוצע ציונים", "ציון ממוצע", "average", "avg", "grade"],
    "behavior_score": ["התנהגות", "תפקוד", "משמעת", "behavior", "conduct"],
    "dominance_score": ["דומיננטי", "דומיננטיות", "מנהיגות", "dominant", "dominance"],
    "parent_notes": ["הערות הורים", "הורה", "parents notes", "parent notes"],
    "teacher_notes": ["הערות מורה", "הערות מחנכת", "teacher notes", "notes"],
    "interview_notes": ["ראיון", "הערות ראיון", "interview"],
    "friend_1": ["חבר 1", "חברה 1", "חבר ראשון", "חברה ראשונה", "חבר/ה ראשון/ה", "חבר/ה ראשון/ה (אין משמעות לדירוג)", "בחירת חבר 1", "friend 1", "friend1"],
    "friend_2": ["חבר 2", "חברה 2", "חבר שני", "חברה שנייה", "חבר/ה שני/ה", "בחירת חבר 2", "friend 2", "friend2"],
    "friend_3": ["חבר 3", "חברה 3", "חבר שלישי", "חברה שלישית", "חבר/ה שלישי/ת", "בחירת חבר 3", "friend 3", "friend3"],
    "allowed_classes": ["כיתות מותרות", "כיתה מותרת", "חייב להיות בכיתה מספר", "רק בכיתה", "allowed classes", "allowed"],
    "forbidden_classes": ["כיתות אסורות", "כיתה אסורה", "לא בכיתה", "forbidden classes", "forbidden"],
    "must_be_with": ["חייב להיות עם", "חייבת להיות עם", "ביחד עם", "must be with", "together"],
    "must_not_be_with": ["אסור להיות עם", "אסור יחד", "להפריד", "לא לשבץ יחד עם", "must not be with", "separate"],
}

NUMERIC_FIELDS = {"math_grade", "english_grade", "hebrew_grade", "average_grade", "dominance_score"}
LONG_TEXT_FIELD_HINTS = {"parent_notes", "teacher_notes", "interview_notes"}
VALUE_REQUIRED_FIELDS = {"allowed_classes", "forbidden_classes", "must_be_with", "must_not_be_with"}


def suggest_mapping(headers: list[str], rows: list[dict[str, Any]] | None = None) -> dict[str, str]:
    pairs: list[tuple[int, str, str]] = []
    for field_name in FIELD_NAMES:
        for header in headers:
            score = _match_score(field_name, header)
            if rows is not None:
                score = _typed_score(field_name, header, rows, score)
            if score >= 74:
                pairs.append((score, field_name, header))
    pairs.sort(key=lambda item: (-item[0], FIELD_NAMES.index(item[1]), headers.index(item[2])))

    used_headers: set[str] = set()
    used_fields: set[str] = set()
    mapping: dict[str, str] = {field_name: "" for field_name in FIELD_NAMES}
    for _score, field_name, header in pairs:
        if field_name in used_fields or header in used_headers:
            continue
        mapping[field_name] = header
        used_fields.add(field_name)
        used_headers.add(header)
    return mapping


def apply_mapping(rows: list[dict[str, Any]], mapping: dict[str, str]) -> list[dict[str, Any]]:
    mapped_rows: list[dict[str, Any]] = []
    for row in rows:
        mapped: dict[str, Any] = {"raw_data": dict(row)}
        for field_name, source_column in mapping.items():
            if field_name not in FIELD_NAMES:
                continue
            mapped[field_name] = row.get(source_column, "") if source_column else ""
        mapped_rows.append(mapped)
    return mapped_rows


def _match_score(field_name: str, header: str) -> int:
    normalized_header = _normalize(header)
    if not normalized_header:
        return 0
    candidates = [field_name, *SYNONYMS.get(field_name, [])]
    scores = []
    for candidate in candidates:
        normalized_candidate = _normalize(candidate)
        if normalized_header == normalized_candidate:
            scores.append(100)
        elif _safe_contains_match(normalized_header, normalized_candidate):
            scores.append(88)
        else:
            scores.append(_safe_fuzzy_score(normalized_header, normalized_candidate))
    return max(scores or [0])


def _safe_contains_match(normalized_header: str, normalized_candidate: str) -> bool:
    if len(normalized_candidate) < 5 or len(normalized_header) < 5:
        return False
    if len(normalized_header) > max(24, len(normalized_candidate) * 2):
        return False
    return normalized_candidate in normalized_header or normalized_header in normalized_candidate


def _safe_fuzzy_score(normalized_header: str, normalized_candidate: str) -> int:
    if len(normalized_header) > max(28, len(normalized_candidate) * 2):
        return 0
    if len(normalized_candidate) <= 3 and normalized_header != normalized_candidate:
        return 0
    return _fuzzy_ratio(normalized_header, normalized_candidate)


def _typed_score(field_name: str, header: str, rows: list[dict[str, Any]], score: int) -> int:
    if score <= 0:
        return 0
    profile = _column_profile(header, rows)
    if field_name in NUMERIC_FIELDS:
        if profile["long_text_ratio"] >= 0.25:
            return 0
        if profile["non_empty"] >= 5 and profile["grade_parse_ratio"] < 0.25:
            return min(score, 68)
    if field_name in LONG_TEXT_FIELD_HINTS and profile["short_value_ratio"] >= 0.9 and profile["non_empty"] >= 5:
        return min(score, 78)
    if field_name in VALUE_REQUIRED_FIELDS and profile["non_empty"] == 0:
        return min(score, 70)
    return score


def _column_profile(header: str, rows: list[dict[str, Any]]) -> dict[str, float]:
    values = [str(row.get(header, "") or "").strip() for row in rows[:50]]
    non_empty = [value for value in values if value]
    if not non_empty:
        return {"non_empty": 0, "grade_parse_ratio": 0.0, "long_text_ratio": 0.0, "short_value_ratio": 1.0}
    grade_parseable = sum(1 for value in non_empty if parse_grade(value) is not None)
    long_text = sum(1 for value in non_empty if len(value) > 80 or value.count(" ") >= 12 or "?" in value)
    short_values = sum(1 for value in non_empty if len(value) <= 40 and value.count(" ") <= 5)
    count = len(non_empty)
    return {
        "non_empty": float(count),
        "grade_parse_ratio": grade_parseable / count,
        "long_text_ratio": long_text / count,
        "short_value_ratio": short_values / count,
    }


def _fuzzy_ratio(left: str, right: str) -> int:
    if fuzz is not None:
        return int(fuzz.ratio(left, right))
    return int(SequenceMatcher(None, left, right).ratio() * 100)


def _normalize(value: str) -> str:
    lowered = str(value).strip().lower()
    return re.sub(r"[\s_\-./:;|()\[\]{}]+", "", lowered)
