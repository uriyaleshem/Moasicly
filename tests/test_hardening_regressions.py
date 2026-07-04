from __future__ import annotations

from collections import Counter
import csv
import importlib.util
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from class_balancer.ai import AiClient
from class_balancer.ai.client import ACTION_SELECTION_SCHEMA
from class_balancer.assignment_engine import PreflightError
from class_balancer.ai.settings import _read_env_file, _write_provider_token
from class_balancer.assignment_engine.engine import AssignmentEngine
from class_balancer.assignment_engine.scoring import evaluate_assignment
from class_balancer.db import Database
from class_balancer.db.database import SCHEMA_VERSION
from class_balancer.importers.mapping import suggest_mapping
from class_balancer.importers.file_importer import load_table
from class_balancer.models.entities import ClassGroup, Student
from class_balancer.services import AssignmentService, ImportService, ProjectService
from class_balancer.validation.normalization import normalize_behavior, parse_grade
from class_balancer.validation.validator import validate_students


class HardeningRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_no_known_mojibake_literals_in_source(self) -> None:
        hebrew_geresh = chr(0x05F3)
        patterns = (hebrew_geresh + chr(0x05D1) + hebrew_geresh, "ג" + "€", "\ufffd", "\u009f", "\u009e")
        roots = [Path("class_balancer"), Path("tests")]
        offenders: list[str] = []
        for root in roots:
            for path in root.rglob("*"):
                if path.suffix not in {".py", ".qml", ".md", ".toml", ".txt"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if any(pattern in text for pattern in patterns):
                    offenders.append(str(path))
        self.assertEqual(offenders, [])

    @unittest.skipIf(importlib.util.find_spec("ortools") is None, "OR-Tools is not installed")
    def test_exact_optimizer_balances_canonical_gender(self) -> None:
        students = [
            Student(id=index, project_id=1, internal_code=f"S{index:03d}", full_name=f"Student {index}", gender="בן")
            for index in range(1, 11)
        ] + [
            Student(id=index, project_id=1, internal_code=f"S{index:03d}", full_name=f"Student {index}", gender="בת")
            for index in range(11, 21)
        ]
        classes = [
            ClassGroup(id=1, project_id=1, name="A", target_students=10),
            ClassGroup(id=2, project_id=1, name="B", target_students=10),
        ]

        assignments = AssignmentEngine()._exact_assignment(
            students=students,
            classes=classes,
            friendships=[],
            class_constraints=[],
            pair_constraints={"together": [], "separation": []},
            locked_assignments={},
            settings={
                "optimizer_backend": "exact",
                "balance_class_size": True,
                "balance_gender": True,
                "gender_weight": 1.0,
                "balance_grades": False,
                "balance_behavior": False,
                "spread_dominant_students": False,
                "spread_source_school": False,
                "friendship": False,
                "random_seed": 42,
            },
        )

        self.assertIsNotNone(assignments)
        boys_by_class = {
            class_id: sum(1 for student in students if assignments[int(student.id)] == class_id and student.gender == "בן")
            for class_id in (1, 2)
        }
        self.assertEqual(boys_by_class, {1: 5, 2: 5})

    def test_score_keeps_raw_objective_when_display_score_would_saturate(self) -> None:
        students = [
            Student(id=index, project_id=1, internal_code=f"S{index:03d}", full_name=f"Student {index}")
            for index in range(1, 101)
        ]
        classes = [ClassGroup(id=index, project_id=1, name=f"C{index}") for index in range(1, 5)]
        assignments = {int(student.id): ((int(student.id) - 1) % 4) + 1 for student in students if student.id}
        friendships = [
            {"student_id": index, "requested_friend_id": ((index + 1) if index < 100 else 1), "priority": 1}
            for index in range(1, 101)
        ]

        score = evaluate_assignment(
            students,
            classes,
            assignments,
            friendships=friendships,
            settings={"friendship": True, "balance_gender": False, "balance_grades": False, "spread_source_school": False},
        )

        self.assertGreater(score["raw_objective"], 100)
        self.assertGreater(score["total_score"], 0)
        self.assertIn("normalized_soft_penalty", score["objective"])

    def test_replacing_classes_invalidates_assignment_versions(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_service = ProjectService(db)
            assignment_service = AssignmentService(db)
            project_id = project_service.create_project("Project", "7", "2026", 2, "A, B")
            students = db.replace_students(
                project_id,
                [
                    Student(id=None, project_id=project_id, internal_code=f"S{index:03d}", full_name=f"Student {index}")
                    for index in range(1, 5)
                ],
            )
            classes = db.get_classes(project_id)
            assignments = {
                int(students[index].id): int(classes[index % 2].id)
                for index in range(len(students))
            }
            db.save_assignment_version(project_id, "v1", assignments, {"total_score": 95, "hard_violations": []})

            project_service.update_classes(project_id, "C, D")

            self.assertIsNone(db.get_active_assignment_version(project_id))
            self.assertFalse(assignment_service.dashboard(project_id)["has_assignment"])
        finally:
            db.close()

    def test_schema_migrations_are_recorded(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            versions = {
                int(row["version"])
                for row in db.connection.execute("SELECT version FROM schema_migrations").fetchall()
            }
            self.assertGreaterEqual(versions, {1, 2, 3, 4})
            user_version = int(db.connection.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(user_version, SCHEMA_VERSION)
        finally:
            db.close()

    def test_reimport_preserves_student_ids_when_rows_reordered(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_service = ProjectService(db)
            import_service = ImportService(db)
            project_id = project_service.create_project("Import", "7", "2026", 2, "A, B")
            first = self.root / "first.csv"
            second = self.root / "second.csv"
            for path, names in (
                (first, ["נועה כהן", "מאיה לוי"]),
                (second, ["מאיה לוי", "נועה כהן"]),
            ):
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["שם מלא", "ממוצע"])
                    for name in names:
                        writer.writerow([name, "90"])

            import_service.load_preview(first)
            import_service.save_imported_students(project_id)
            first_ids = {student.full_name: int(student.id) for student in db.get_students(project_id)}

            import_service.load_preview(second)
            import_service.save_imported_students(project_id)
            second_ids = {student.full_name: int(student.id) for student in db.get_students(project_id)}

            self.assertEqual(second_ids, first_ids)
        finally:
            db.close()

    def test_identical_reimport_keeps_existing_assignment_version(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_service = ProjectService(db)
            import_service = ImportService(db)
            project_id = project_service.create_project("Import", "7", "2026", 2, "A, B")
            csv_path = self.root / "students.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["מזהה", "שם מלא"])
                writer.writerow(["001", "נועה כהן"])
                writer.writerow(["002", "מאיה לוי"])
            import_service.load_preview(csv_path)
            import_service.save_imported_students(project_id)
            students = db.get_students(project_id)
            classes = db.get_classes(project_id)
            assignments = {
                int(students[index].id): int(classes[index % 2].id)
                for index in range(len(students))
            }
            version_id = db.save_assignment_version(project_id, "v1", assignments, {"total_score": 95, "hard_violations": []})

            import_service.load_preview(csv_path)
            import_service.save_imported_students(project_id)

            self.assertEqual(int(db.get_active_assignment_version(project_id)["id"]), version_id)
        finally:
            db.close()

    def test_assignments_reject_cross_project_rows(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            p1 = db.create_project("P1", "7", "2026", ["A", "B"])
            p2 = db.create_project("P2", "7", "2026", ["A", "B"])
            s1 = db.replace_students(p1, [Student(id=None, project_id=p1, internal_code="S001", full_name="P1 Student")])[0]
            s2 = db.replace_students(p2, [Student(id=None, project_id=p2, internal_code="S001", full_name="P2 Student")])[0]
            c1 = db.get_classes(p1)[0]
            c2 = db.get_classes(p2)[0]

            with self.assertRaises(ValueError):
                db.save_assignment_version(
                    p1,
                    "bad",
                    {int(s2.id): int(c2.id)},
                    {"total_score": 90, "hard_violations": []},
                )

            cursor = db.connection.execute(
                """
                INSERT INTO assignment_versions (project_id, name, created_at, score_total, score_json, notes, is_active)
                VALUES (?, 'manual', 'now', 0, '{}', '', 0)
                """,
                (p1,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.connection.execute(
                    """
                    INSERT INTO assignments
                        (project_id, version_id, student_id, class_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'now', 'now')
                    """,
                    (p1, int(cursor.lastrowid), int(s2.id), int(c2.id)),
                )
            self.assertEqual(int(s1.project_id), p1)
            self.assertEqual(int(c1.project_id), p1)
        finally:
            db.close()

    def test_assignment_version_saves_best_effort_hard_violations(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_id = db.create_project("P1", "7", "2026", ["A", "B"])
            students = db.replace_students(
                project_id,
                [
                    Student(id=None, project_id=project_id, internal_code="S001", full_name="Student 1"),
                    Student(id=None, project_id=project_id, internal_code="S002", full_name="Student 2"),
                ],
            )
            class_id = int(db.get_classes(project_id)[0].id)
            db.connection.execute("UPDATE classes SET max_students = 1 WHERE id = ?", (class_id,))
            db.connection.commit()
            assignments = {int(student.id): class_id for student in students}
            score = {"total_score": 50, "hard_violations": ["A has too many students."]}

            version_id = db.save_assignment_version(project_id, "best effort", assignments, score)

            active = db.get_active_assignment_version(project_id)
            self.assertIsNotNone(active)
            self.assertEqual(int(active["id"]), version_id)
            self.assertEqual(active["score"]["hard_violations"], score["hard_violations"])
            self.assertEqual(len(db.get_assignments(version_id)), 2)
        finally:
            db.close()

    def test_regular_assignment_saves_clean_and_best_effort_variants(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_id = db.create_project("P1", "7", "2026", ["A", "B", "C"])
            db.replace_students(
                project_id,
                [
                    Student(id=None, project_id=project_id, internal_code=f"S{index:03d}", full_name=f"Student {index}")
                    for index in range(1, 5)
                ],
            )

            class FakeEngine:
                def __init__(self) -> None:
                    self.calls = 0

                def run(self, students, classes, **kwargs):  # type: ignore[no-untyped-def]
                    index = self.calls
                    self.calls += 1
                    class_ids = [int(group.id) for group in classes]
                    assignments = {
                        int(student.id): class_ids[(offset + index) % len(class_ids)]
                        for offset, student in enumerate(students)
                    }
                    hard_violations = ["hard violation"] if index == 1 else []
                    return {
                        "assignments": assignments,
                        "score": {
                            "total_score": 99 if hard_violations else 80,
                            "hard_violations": hard_violations,
                            "objective": {
                                "hard_violation_count": len(hard_violations),
                                "normalized_soft_penalty": 1 if hard_violations else 20,
                            },
                            "penalties": {},
                            "friendship": {},
                            "class_stats": [],
                        },
                    }

            service = AssignmentService(db)
            service.engine = FakeEngine()

            result = service.run_assignment(
                project_id,
                name="Regular",
                variant_count=1,
                settings_override={"ai_assisted_assignment": False},
            )
            versions = db.get_assignment_versions(project_id)

            self.assertEqual(result["score"]["hard_violations"], [])
            self.assertEqual(len(versions), 2)
            self.assertTrue(versions[0]["is_active"])
            self.assertEqual(versions[0]["name"], "Regular")
            self.assertEqual(versions[0]["score"]["hard_violations"], [])
            self.assertTrue(any(version["score"].get("hard_violations") for version in versions))
        finally:
            db.close()

    def test_assignment_run_saves_top_five_requested_variants(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_id = db.create_project("P1", "7", "2026", ["A", "B", "C", "D", "E", "F"])
            db.replace_students(
                project_id,
                [
                    Student(id=None, project_id=project_id, internal_code=f"S{index:03d}", full_name=f"Student {index}")
                    for index in range(1, 7)
                ],
            )

            class FakeEngine:
                def __init__(self) -> None:
                    self.calls = 0

                def run(self, students, classes, **kwargs):  # type: ignore[no-untyped-def]
                    index = self.calls
                    self.calls += 1
                    class_ids = [int(group.id) for group in classes]
                    assignments = {
                        int(student.id): class_ids[(offset + index) % len(class_ids)]
                        for offset, student in enumerate(students)
                    }
                    hard_violations = ["hard violation"] if index >= 4 else []
                    return {
                        "assignments": assignments,
                        "score": {
                            "total_score": 100 - index,
                            "hard_violations": hard_violations,
                            "objective": {
                                "hard_violation_count": len(hard_violations),
                                "normalized_soft_penalty": index,
                            },
                            "penalties": {},
                            "friendship": {},
                            "class_stats": [],
                        },
                    }

            service = AssignmentService(db)
            service.engine = FakeEngine()

            result = service.run_assignment(
                project_id,
                name="MAX",
                variant_count=6,
                settings_override={"save_top_variants": 5, "ai_assisted_assignment": False},
            )
            versions = db.get_assignment_versions(project_id)

            self.assertEqual(result["score"]["total_score"], 100)
            self.assertEqual(len(versions), 5)
            self.assertTrue(versions[0]["is_active"])
            self.assertEqual(versions[0]["name"], "MAX")
            self.assertTrue(any(version["score"].get("hard_violations") for version in versions))
        finally:
            db.close()

    def test_gemini_receives_schema_and_key_header(self) -> None:
        captured: dict[str, object] = {}

        class CapturingClient(AiClient):
            def _post_json(self, url, body, headers):  # type: ignore[no-untyped-def]
                captured["url"] = url
                captured["body"] = body
                captured["headers"] = headers
                return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}

        CapturingClient()._send_gemini(
            "secret-token",
            "task",
            {"x": 1},
            structured=True,
            schema=ACTION_SELECTION_SCHEMA,
        )

        body = captured["body"]
        self.assertNotIn("secret-token", str(captured["url"]))
        self.assertEqual(captured["headers"], {"x-goog-api-key": "secret-token"})
        self.assertIn("responseJsonSchema", body["generationConfig"])

    def test_env_token_writer_rejects_newline_injection(self) -> None:
        env_path = self.root / ".env"

        with self.assertRaises(ValueError):
            _write_provider_token(env_path, "OpenAI", "token\nGEMINI_API_KEY=leak")

        _write_provider_token(env_path, "OpenAI", "clean-token")
        values = _read_env_file(env_path)
        self.assertEqual(values["OPENAI_API_KEY"], "clean-token")
        self.assertNotIn("GEMINI_API_KEY", values)

    def test_provider_limit_is_honored(self) -> None:
        class SingleProviderClient(AiClient):
            def _send(self, provider, *args, **kwargs):  # type: ignore[no-untyped-def]
                return (
                    '{"summary_he":"ok","no_improvement":true,'
                    '"selected_candidate_ids":[],"notes":[]}'
                )

        old_values = {
            key: os.environ.get(key)
            for key in ("CLASS_BALANCER_AI_ENABLED", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
        }
        os.environ["CLASS_BALANCER_AI_ENABLED"] = "true"
        os.environ["OPENAI_API_KEY"] = "test-token"
        os.environ["ANTHROPIC_API_KEY"] = "test-token"
        os.environ["GEMINI_API_KEY"] = "test-token"
        try:
            result = SingleProviderClient().complete_action_selection(
                "select",
                {"candidate_actions": []},
                "fallback",
                allow_external=True,
                provider_limit=1,
            )
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(len(result["providers"]), 1)

    def test_csv_preview_streams_with_detected_dialect_and_multiline_cells(self) -> None:
        csv_path = self.root / "students.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["שם מלא", "ממוצע"])
            writer.writerow(["נועה\nכהן", "90"])
            writer.writerow(["מאיה לוי", "91"])

        table = load_table(csv_path, preview_limit=1)

        self.assertEqual(table.headers, ["שם מלא", "ממוצע"])
        self.assertEqual(table.row_count, 2)
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(table.rows[0]["שם מלא"], "נועה כהן")

    def test_mapping_uses_one_to_one_global_header_match(self) -> None:
        mapping = suggest_mapping(["מזהה תלמיד", "שם", "ממוצע"])

        self.assertEqual(mapping["internal_code"], "מזהה תלמיד")
        self.assertEqual(mapping["full_name"], "שם")
        self.assertEqual(mapping["average_grade"], "ממוצע")
        used = [value for value in mapping.values() if value]
        self.assertEqual(len(used), len(set(used)))

    def test_audit_workbook_headers_map_without_dangerous_dominance(self) -> None:
        headers = [
            "תז",
            "שם",
            "שם בית ספר נוכחי",
            "אנגלית",
            "מתמטיקה",
            "עברית",
            "התנהגות",
            "חייב להיות בכיתה מספר",
            "לא לשבץ יחד עם",
            "כיתה מותרת",
            "חבר/ה ראשון/ה (אין משמעות לדירוג)",
            "חבר/ה שני/ה",
            "חבר/ה שלישי/ת",
            "אילו אתגרים חווה ילדכם בשנים האחרונות?",
        ]
        rows = [
            {
                "אנגלית": "טוב מאוד",
                "מתמטיקה": "מצוין",
                "עברית": "75",
                "התנהגות": "א",
                "חייב להיות בכיתה מספר": "",
                "לא לשבץ יחד עם": "תלמיד אחר",
                "כיתה מותרת": "ז'3",
                "אילו אתגרים חווה ילדכם בשנים האחרונות?": "טקסט חופשי ארוך על חוויה משפחתית ולימודית",
            }
        ]

        mapping = suggest_mapping(headers, rows)

        self.assertEqual(mapping["internal_code"], "תז")
        self.assertEqual(mapping["source_school"], "שם בית ספר נוכחי")
        self.assertEqual(mapping["friend_1"], "חבר/ה ראשון/ה (אין משמעות לדירוג)")
        self.assertEqual(mapping["friend_2"], "חבר/ה שני/ה")
        self.assertEqual(mapping["friend_3"], "חבר/ה שלישי/ת")
        self.assertEqual(mapping["allowed_classes"], "כיתה מותרת")
        self.assertEqual(mapping["must_not_be_with"], "לא לשבץ יחד עם")
        self.assertEqual(mapping["dominance_score"], "")

    def test_parse_grade_is_explicit_about_ranges_and_fractions(self) -> None:
        self.assertIsNone(parse_grade("80-90"))
        self.assertEqual(parse_grade("8/10"), 80.0)
        self.assertIsNone(parse_grade("ציון 90 בערך"))

    def test_hebrew_categorical_grades_and_behavior_are_normalized(self) -> None:
        self.assertEqual(parse_grade("מצוין"), 95.0)
        self.assertEqual(parse_grade("טוב מאד"), 88.0)
        self.assertEqual(parse_grade("כמעט טוב מאוד"), 82.0)
        self.assertEqual(parse_grade("מצויו"), 95.0)
        self.assertIsNone(parse_grade("N"))
        self.assertEqual(normalize_behavior("א"), "גבוהה")
        self.assertEqual(normalize_behavior("ב"), "בינונית")
        self.assertEqual(normalize_behavior("ג"), "נמוכה")
        self.assertEqual(normalize_behavior("טובה מאוד"), "גבוהה")
        self.assertEqual(normalize_behavior("N"), "")

    def test_import_excludes_questionnaire_only_and_locks_single_allowed_class(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_service = ProjectService(db)
            import_service = ImportService(db)
            project_id = project_service.create_project("Import", "7", "2026", 2, "ז'1, ז'2")
            csv_path = self.root / "students.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["תז", "שם", "מקור הרשומה", "כיתה מותרת"])
                writer.writerow(["001", "נועה כהן", "קובץ נתונים + שאלון", "ז'1"])
                writer.writerow(["", "רשומת שאלון", "שאלון בלבד", "ז'2"])

            import_service.load_preview(csv_path)
            result = import_service.save_imported_students(project_id)
            constraints = db.get_class_constraints(project_id)

            self.assertEqual(result["students_count"], 1)
            self.assertEqual(result["excluded_count"], 1)
            self.assertEqual(len(constraints), 1)
            self.assertIsNotNone(constraints[0]["locked_class_id"])
        finally:
            db.close()

    def test_relationship_import_splits_conjunctions_and_unique_fuzzy_names(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_service = ProjectService(db)
            import_service = ImportService(db)
            project_id = project_service.create_project("Import", "7", "2026", 2, "ז'1, ז'2")
            csv_path = self.root / "students.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["תז", "שם", "חבר/ה ראשון/ה (אין משמעות לדירוג)", "חבר/ה שני/ה", "חבר/ה שלישי/ת"])
                writer.writerow(["001", "מבקש קשרים", "נטע שטייר וגילי קרפמן", "תמר שריבמן - לאחר שיחה עם האם", "בני קלניץ - לו"])
                writer.writerow(["002", "נטע דילן שטייר", "", "", ""])
                writer.writerow(["003", "גילי קרפמן", "", "", ""])
                writer.writerow(["004", "תמר שרייבמן", "", "", ""])
                writer.writerow(["005", "בנימין קלניץ", "", "", ""])

            import_service.load_preview(csv_path)
            result = import_service.save_imported_students(project_id)
            friendships = db.get_friendships(project_id)
            warnings = [
                issue
                for issue in db.get_validation_issues(project_id, include_resolved=True)
                if issue.field_name in {"friend_1", "friend_2", "friend_3"}
                and "לא נפתרה" in issue.message
            ]

            self.assertEqual(result["critical_count"], 0)
            self.assertEqual(len(friendships), 4)
            self.assertEqual(warnings, [])
        finally:
            db.close()

    def test_unresolved_friend_is_warning_and_does_not_block_preflight(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_service = ProjectService(db)
            import_service = ImportService(db)
            assignment_service = AssignmentService(db)
            project_id = project_service.create_project("Import", "7", "2026", 2, "ז'1, ז'2")
            csv_path = self.root / "students.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["תז", "שם", "חבר/ה ראשון/ה (אין משמעות לדירוג)"])
                writer.writerow(["001", "נועה כהן", "שם לא קיים"])
                writer.writerow(["002", "מאיה לוי", ""])

            import_service.load_preview(csv_path)
            result = import_service.save_imported_students(project_id)
            issues = db.get_validation_issues(project_id, include_resolved=True)
            friend_issues = [issue for issue in issues if issue.field_name == "friend_1" and "שם לא קיים" in issue.message]
            report = assignment_service.preflight_report(project_id)

            self.assertEqual(result["critical_count"], 0)
            self.assertEqual(len(friend_issues), 1)
            self.assertEqual(friend_issues[0].severity, "warning")
            self.assertTrue(report["ok"])
        finally:
            db.close()

    def test_unresolved_separation_validation_blocks_assignment_run(self) -> None:
        db = Database(self.root / "test.sqlite3")
        try:
            project_service = ProjectService(db)
            import_service = ImportService(db)
            assignment_service = AssignmentService(db)
            project_id = project_service.create_project("Import", "7", "2026", 2, "ז'1, ז'2")
            csv_path = self.root / "students.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["תז", "שם", "לא לשבץ יחד עם"])
                writer.writerow(["001", "נועה כהן", "שם לא קיים"])
                writer.writerow(["002", "מאיה לוי", ""])

            import_service.load_preview(csv_path)
            result = import_service.save_imported_students(project_id)
            report = assignment_service.preflight_report(project_id)

            self.assertGreater(result["critical_count"], 0)
            self.assertFalse(report["ok"])
            self.assertIn("IMPORT_VALIDATION_CRITICAL", {issue["code"] for issue in report["issues"]})
            with self.assertRaises(PreflightError):
                assignment_service.run_assignment(project_id)
        finally:
            db.close()

    def test_real_student_workbook_import_and_assignment_regression(self) -> None:
        workbook = _real_student_workbook_path()
        if workbook is None:
            self.skipTest("real student workbook is not available in Downloads")
        db = Database(self.root / "actual.sqlite3")
        try:
            project_service = ProjectService(db)
            import_service = ImportService(db)
            assignment_service = AssignmentService(db)
            project_id = project_service.create_project(
                "Actual workbook",
                "7",
                "2026",
                6,
                "ז'2, ז'3, ז'4, ז'5, ז'6, ז'7",
            )
            project_service.update_settings(project_id, {"ai_assisted_assignment": False})

            import_service.load_preview(workbook, sheet_name="נתונים מאוחדים")
            result = import_service.save_imported_students(project_id)
            issues = db.get_validation_issues(project_id, include_resolved=True)
            issue_counts = Counter(issue.severity for issue in issues)
            friend_issue_counts = Counter(
                issue.severity
                for issue in issues
                if issue.field_name in {"friend_1", "friend_2", "friend_3"}
            )
            separation_issue_counts = Counter(
                issue.severity
                for issue in issues
                if issue.field_name == "must_not_be_with"
            )
            class_constraints = db.get_class_constraints(project_id)

            self.assertEqual(result["students_count"], 210)
            self.assertEqual(result["excluded_count"], 2)
            self.assertEqual(sum(1 for item in class_constraints if item.get("locked_class_id")), 57)
            self.assertEqual(friend_issue_counts.get("critical", 0), 0)
            self.assertGreater(friend_issue_counts.get("warning", 0), 0)

            blocked_report = assignment_service.preflight_report(project_id)
            if separation_issue_counts.get("critical", 0):
                self.assertFalse(blocked_report["ok"])
                critical_field_names = {
                    issue.get("details", {}).get("field_name")
                    for issue in blocked_report["issues"]
                    if issue.get("severity") == "critical"
                }
                self.assertEqual(critical_field_names, {"must_not_be_with"})
            else:
                self.assertTrue(blocked_report["ok"])

            cleaned_issues = [issue for issue in issues if issue.field_name != "must_not_be_with"]
            db.replace_validation_issues(project_id, cleaned_issues)
            clean_report = assignment_service.preflight_report(project_id)
            self.assertTrue(clean_report["ok"])

            start = time.perf_counter()
            assignment = assignment_service.run_assignment(project_id)
            elapsed = time.perf_counter() - start
            sizes = sorted(stat["size"] for stat in assignment["score"]["class_stats"])
            active_rows = db.get_active_assignment_rows(project_id)
            active_version = db.get_active_assignment_version(project_id)

            self.assertLessEqual(elapsed, 120.0)
            self.assertIsNotNone(active_version)
            self.assertEqual(active_version["score"]["hard_violations"], assignment["score"]["hard_violations"])
            self.assertLessEqual(max(sizes) - min(sizes), 2)
            self.assertEqual(len(active_rows), 210)
            self.assertEqual(sum(1 for row in active_rows if row.get("locked_manually")), 57)
            self.assertGreaterEqual(issue_counts.get("warning", 0), friend_issue_counts.get("warning", 0))
        finally:
            db.close()

    def test_dominance_out_of_range_is_reported(self) -> None:
        issues = validate_students(
            1,
            [Student(id=1, project_id=1, internal_code="S001", full_name="Student", dominance_score=250)],
            ["A", "B"],
            settings={
                "balance_gender": False,
                "balance_grades": False,
                "friendship": False,
                "spread_source_school": False,
            },
        )

        self.assertTrue(any(issue.field_name == "dominance_score" for issue in issues))


def _real_student_workbook_path() -> Path | None:
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        return None
    needle = "תלמידים מאוחד"
    for path in downloads.glob("*.xlsx"):
        if needle in path.name:
            return path
    return None


if __name__ == "__main__":
    unittest.main()
