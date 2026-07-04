from __future__ import annotations

import re
from typing import Any

from class_balancer.models.entities import Student


GENDER_MALE = "בן"
GENDER_FEMALE = "בת"
CANONICAL_GENDERS = {GENDER_MALE, GENDER_FEMALE}
MISSING_VALUE_KEYS = {
    "",
    "-",
    "--",
    ".",
    "n",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "אין",
    "איןמידע",
    "לארלוונטי",
    "לאידוע",
    "חסר",
}

GENDER_MAP = {
    "זכר": GENDER_MALE,
    GENDER_MALE: GENDER_MALE,
    "בנים": GENDER_MALE,
    "m": GENDER_MALE,
    "male": GENDER_MALE,
    "boy": GENDER_MALE,
    "1": GENDER_MALE,
    "נקבה": GENDER_FEMALE,
    GENDER_FEMALE: GENDER_FEMALE,
    "בנות": GENDER_FEMALE,
    "f": GENDER_FEMALE,
    "female": GENDER_FEMALE,
    "girl": GENDER_FEMALE,
    "2": GENDER_FEMALE,
}

BEHAVIOR_MAP = {
    "מצוין": "גבוהה",
    "מצוינת": "גבוהה",
    "טוב מאוד": "גבוהה",
    "טוב מאד": "גבוהה",
    "טובה מאוד": "גבוהה",
    "טובה מאד": "גבוהה",
    "טוב": "גבוהה",
    "טובה": "גבוהה",
    "גבוה": "גבוהה",
    "גבוהה": "גבוהה",
    "א": "גבוהה",
    "high": "גבוהה",
    "a": "גבוהה",
    "3": "גבוהה",
    "בינוני": "בינונית",
    "בינונית": "בינונית",
    "סביר": "בינונית",
    "ב": "בינונית",
    "medium": "בינונית",
    "b": "בינונית",
    "2": "בינונית",
    "בעייתי": "נמוכה",
    "בעייתית": "נמוכה",
    "מאתגר": "נמוכה",
    "מאתגרת": "נמוכה",
    "נמוך": "נמוכה",
    "נמוכה": "נמוכה",
    "ג": "נמוכה",
    "low": "נמוכה",
    "c": "נמוכה",
    "1": "נמוכה",
}

GRADE_WORD_MAP = {
    "מצוין": 95.0,
    "מצוינת": 95.0,
    "מצויו": 95.0,
    "טובמאוד": 88.0,
    "טובמאד": 88.0,
    "טובהמאוד": 88.0,
    "טובהמאד": 88.0,
    "כמעטטובמאוד": 82.0,
    "כמעטטובמאד": 82.0,
    "טוב": 75.0,
    "טובה": 75.0,
    "כמעטטוב": 68.0,
    "מספיק": 60.0,
}


def build_students_from_mapping(project_id: int, mapped_rows: list[dict[str, Any]]) -> list[Student]:
    students: list[Student] = []
    for index, row in enumerate(mapped_rows, start=1):
        raw_data = dict(row.get("raw_data", {}))
        mapped_values = {key: value for key, value in row.items() if key != "raw_data"}
        raw_data["_mapped_values"] = mapped_values

        full_name = clean_text(row.get("full_name", ""))
        first_name = clean_text(row.get("first_name", ""))
        last_name = clean_text(row.get("last_name", ""))
        if full_name and not (first_name and last_name):
            first_name, last_name = split_full_name(full_name)
        if not full_name:
            full_name = f"{first_name} {last_name}".strip()

        explicit_code = clean_text(row.get("internal_code", ""))
        internal_code = explicit_code or f"S{index:03d}"
        stable_key = student_stable_key(explicit_code, full_name, index)
        raw_data["_generated_internal_code"] = not bool(explicit_code)
        student = Student(
            id=None,
            project_id=project_id,
            internal_code=internal_code,
            stable_key=stable_key,
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            gender=normalize_gender(row.get("gender", "")),
            source_school=clean_text(row.get("source_school", "")),
            math_grade=parse_grade(row.get("math_grade")),
            english_grade=parse_grade(row.get("english_grade")),
            hebrew_grade=parse_grade(row.get("hebrew_grade")),
            average_grade=parse_grade(row.get("average_grade")),
            behavior_score=normalize_behavior(row.get("behavior_score", "")),
            dominance_score=parse_grade(row.get("dominance_score")),
            parent_notes=clean_text(row.get("parent_notes", "")),
            teacher_notes=clean_text(row.get("teacher_notes", "")),
            interview_notes=clean_text(row.get("interview_notes", "")),
            raw_data=raw_data,
        )
        students.append(student)
    return students


def student_stable_key(internal_code: Any, full_name: Any, row_index: int | None = None) -> str:
    code_key = normalize_name_key(internal_code)
    if code_key:
        return f"code:{code_key}"
    name_key = normalize_name_key(full_name)
    if name_key:
        return f"name:{name_key}"
    if row_index is not None:
        return f"row:{int(row_index):06d}"
    return ""


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if is_missing_value(text):
        return ""
    return re.sub(r"\s+", " ", text)


def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    key = re.sub(r"[\s_\-.'\"’׳״`/\\|:;(){}\[\]]+", "", text.lower())
    return key in MISSING_VALUE_KEYS


def split_full_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in clean_text(full_name).split(" ") if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def normalize_gender(value: Any) -> str:
    if is_missing_value(value):
        return ""
    text = clean_text(value).lower()
    return GENDER_MAP.get(text, clean_text(value))


def normalize_behavior(value: Any) -> str:
    if is_missing_value(value):
        return ""
    text = clean_text(value).lower()
    return BEHAVIOR_MAP.get(text, clean_text(value))


def behavior_to_number(value: Any) -> float | None:
    normalized = normalize_behavior(value)
    if normalized == "גבוהה":
        return 3.0
    if normalized == "בינונית":
        return 2.0
    if normalized == "נמוכה":
        return 1.0
    grade = parse_grade(value)
    if grade is None:
        return None
    if grade > 10:
        return max(1.0, min(3.0, grade / 33.3))
    return max(1.0, min(3.0, grade))


def parse_grade(value: Any) -> float | None:
    if is_missing_value(value):
        return None
    text = clean_text(value)
    if not text:
        return None
    word_score = GRADE_WORD_MAP.get(normalize_name_key(text))
    if word_score is not None:
        return word_score
    text = text.replace(",", ".")
    if re.fullmatch(r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?", text):
        return None
    fraction = re.fullmatch(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", text)
    if fraction:
        numerator = float(fraction.group(1))
        denominator = float(fraction.group(2))
        if denominator <= 0:
            return None
        score = (numerator / denominator) * 100.0
        return score if 0.0 <= score <= 100.0 else None
    percent = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", text)
    if percent:
        score = float(percent.group(1))
        return score if 0.0 <= score <= 100.0 else None
    number = re.fullmatch(r"-?\d+(?:\.\d+)?", text)
    if not number:
        return None
    try:
        score = float(number.group(0))
    except ValueError:
        return None
    return score if 0.0 <= score <= 100.0 else None


def normalize_name_key(value: Any) -> str:
    text = clean_text(value).lower()
    text = text.translate(str.maketrans({"׳": "", "״": "", "’": "", "‘": "", "´": "", "ʹ": "", "ˊ": ""}))
    text = re.sub(r"[\s_\-.'\"’׳״`/\\|:;(){}\[\]]+", "", text)
    return text


def split_multi_value(value: Any) -> list[str]:
    text = clean_relationship_name(value)
    if not text:
        return []
    parts = re.split(r"[,;|/\n\r]+", text)
    return [clean_relationship_name(part) for part in parts if clean_relationship_name(part)]


def clean_relationship_name(value: Any) -> str:
    if is_missing_value(value):
        return ""
    text = clean_text(value)
    text = re.sub(r"\s*[-–—]\s*(?:לו|לה|חשוב(?:ה|/ה)?|מאוד|מאד|עדיפות|לאחר\s+שיחה.*|.*שיחה\s+עם.*)$", " ", text)
    text = re.sub(r"[\[(][^\])]*[\])]", " ", text)
    text = re.sub(r"\b(?:חשוב|חשובה|חשוב/ה|מאוד|מאד|עדיפות)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n,;|/.:-–—")
    if is_missing_value(text) or normalize_name_key(text) in {"לא", "אףאחד", "אףאחת"}:
        return ""
    return text


def relationship_name_keys(value: Any) -> list[str]:
    text = clean_relationship_name(value)
    if not text:
        return []
    keys: list[str] = []

    def add(candidate: str) -> None:
        key = normalize_name_key(candidate)
        if key and key not in keys:
            keys.append(key)

    add(text)
    tokens = [token for token in re.split(r"\s+", text) if token]
    if len(tokens) >= 2:
        add(" ".join(reversed(tokens)))
        add(f"{tokens[0]} {tokens[-1]}")
        add(f"{tokens[-1]} {tokens[0]}")
    return keys
