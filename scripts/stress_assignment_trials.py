from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from class_balancer.db import Database  # noqa: E402
from class_balancer.models.fields import DEFAULT_RULE_SETTINGS  # noqa: E402
from class_balancer.services import AssignmentService, ImportService, ProjectService  # noqa: E402


HEADERS = [
    "שם מלא",
    "מין",
    "בית ספר קודם",
    "ממוצע",
    "מתמטיקה",
    "אנגלית",
    "עברית",
    "התנהגות",
    "דומיננטיות",
    "חבר 1",
    "חבר 2",
    "כיתות אסורות",
    "חייב/ת להיות עם",
    "אסור להיות עם",
]

FIRST_NAMES = [
    "נועה",
    "מאיה",
    "יואב",
    "אדם",
    "רוני",
    "דניאל",
    "תמר",
    "איתי",
    "שירה",
    "אורי",
    "מיכל",
    "עידו",
    "ליה",
    "גיא",
    "יעל",
    "עומר",
]
LAST_NAMES = [
    "כהן",
    "לוי",
    "מזרחי",
    "פרץ",
    "שלום",
    "חדד",
    "אברהם",
    "שפירא",
    "ברק",
    "רוזן",
    "דיין",
    "גל",
]
SCHOOLS = ["אלון", "ברוש", "ארז", "דקל", "גפן", "רימון"]
BEHAVIORS = ["מצוין", "טובה", "בינוני", "מאתגרת"]


def run_trials(out_dir: Path | None = None, trials: int = 10) -> dict[str, Any]:
    root = out_dir or Path(tempfile.mkdtemp(prefix="class_balancer_trials_"))
    root.mkdir(parents=True, exist_ok=True)
    db = Database(root / "trials.sqlite3")
    project_service = ProjectService(db)
    import_service = ImportService(db)
    assignment_service = AssignmentService(db)

    summaries: list[dict[str, Any]] = []
    try:
        for index in range(1, trials + 1):
            class_count = 2 + (index % 3)
            student_count = 12 + (index * 2)
            class_names = [f"ז{class_index}" for class_index in range(1, class_count + 1)]
            csv_path = root / f"trial_{index:02d}_{student_count}_students.csv"
            _write_trial_csv(csv_path, index, student_count, class_names)
            project_id = project_service.create_project(
                name=f"ניסוי אלגוריתם {index}",
                grade_level="ז",
                school_year="תשפז",
                class_count=class_count,
                class_names_text=", ".join(class_names),
            )
            project_service.update_settings(project_id, _trial_settings(index))
            import_service.load_preview(csv_path)
            import_started = time.perf_counter()
            import_result = import_service.save_imported_students(project_id)
            import_seconds = time.perf_counter() - import_started
            assignment_started = time.perf_counter()
            result = assignment_service.run_assignment(
                project_id,
                name=f"שיבוץ ניסוי {index}",
                variant_count=2 + (index % 2),
            )
            assignment_seconds = time.perf_counter() - assignment_started
            score = result["score"]
            class_sizes = [item["size"] for item in score.get("class_stats", [])]
            summaries.append(
                {
                    "trial": index,
                    "csv_path": str(csv_path),
                    "students": import_result["students_count"],
                    "classes": class_count,
                    "score": score["total_score"],
                    "hard_violations": len(score.get("hard_violations", [])),
                    "missing_friends": len(score.get("friendship", {}).get("missing", [])),
                    "class_sizes": class_sizes,
                    "size_gap": max(class_sizes) - min(class_sizes) if class_sizes else None,
                    "variants_checked": score.get("candidate_note", {}).get("variants_checked", 1),
                    "import_seconds": round(import_seconds, 4),
                    "assignment_seconds": round(assignment_seconds, 4),
                }
            )
            print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)
    finally:
        db.close()

    scores = [float(item["score"]) for item in summaries]
    return {
        "root": str(root),
        "trials": summaries,
        "average_score": round(mean(scores), 2) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "hard_violations": sum(int(item["hard_violations"]) for item in summaries),
        "average_assignment_seconds": round(mean(float(item["assignment_seconds"]) for item in summaries), 4) if summaries else 0,
        "max_assignment_seconds": max(float(item["assignment_seconds"]) for item in summaries) if summaries else 0,
    }


def _write_trial_csv(path: Path, seed: int, student_count: int, class_names: list[str]) -> None:
    rows = []
    names = _unique_names(student_count, seed)
    for index, name in enumerate(names):
        pair_index = index + 1 if index % 2 == 0 else index - 1
        friend_1 = names[pair_index] if 0 <= pair_index < student_count else ""
        friend_2 = names[index + 2] if index % 7 == 0 and index + 2 < student_count else ""
        forbidden = class_names[(index + seed) % len(class_names)] if index % 17 == 0 else ""
        must_be_with = names[index + 1] if index % 23 == 0 and index + 1 < student_count else ""
        must_not_be_with = names[index + 2] if index % 19 == 0 and index + 2 < student_count else ""
        base_grade = 62 + ((index * 7 + seed * 3) % 36)
        rows.append(
            [
                name,
                "בת" if index % 2 == 0 else "בן",
                SCHOOLS[(index + seed) % len(SCHOOLS)],
                str(base_grade),
                str(max(55, min(100, base_grade + ((index % 5) - 2)))),
                str(max(55, min(100, base_grade + ((index % 7) - 3)))),
                str(max(55, min(100, base_grade + ((index % 3) - 1)))),
                BEHAVIORS[(index + seed) % len(BEHAVIORS)],
                str((index * 11 + seed) % 40),
                friend_1,
                friend_2,
                forbidden,
                must_be_with,
                must_not_be_with,
            ]
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def _unique_names(count: int, seed: int) -> list[str]:
    names = []
    for index in range(count):
        first = FIRST_NAMES[(index + seed) % len(FIRST_NAMES)]
        last = LAST_NAMES[(index * 3 + seed) % len(LAST_NAMES)]
        names.append(f"{first} {last} {index + 1}")
    return names


def _trial_settings(index: int) -> dict[str, Any]:
    settings = dict(DEFAULT_RULE_SETTINGS)
    settings.update(
        {
            "optimizer_backend": "local",
            "search_restarts": 1 + (index % 2),
            "max_iterations": 12 + (index * 2),
            "stop_when_score_at_least": 93,
            "swap_search_min_score": 72,
            "class_size_weight": 1.2 + ((index % 3) * 0.15),
            "grade_weight": 0.9 + ((index % 4) * 0.12),
            "behavior_weight": 0.9 + ((index % 2) * 0.2),
            "friendship_weight": 0.8 + ((index % 4) * 0.15),
            "source_school_weight": 0.65 + ((index % 3) * 0.15),
            "ai_auto_review": False,
        }
    )
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate temporary datasets and stress-test assignments.")
    parser.add_argument("--out", type=Path, help="Directory for temporary trial CSV files and database.")
    parser.add_argument("--trials", type=int, default=10)
    args = parser.parse_args()
    result = run_trials(args.out, max(1, args.trials))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
