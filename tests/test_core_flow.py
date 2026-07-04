from __future__ import annotations

import csv
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from class_balancer.ai import AiClient
from class_balancer.ai.client import (
    _local_structured_review,
    _parse_action_selection_response,
    _parse_structured_response,
)
from class_balancer.ai.settings import load_ai_settings
from class_balancer.db import Database
from class_balancer.importers.file_importer import load_table
from class_balancer.importers.templates import latest_rule_template, latest_template, save_rule_template, save_template
from class_balancer.models.entities import Student, ValidationIssue
from class_balancer.services import AssignmentService, ExportService, ImportService, ProjectService, ReportService
from class_balancer.ui.bridge import AppBridge
from class_balancer.validation import normalize_name_key, validate_students


class CoreFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db = Database(self.root / "test.sqlite3")
        self.project_service = ProjectService(self.db)
        self.import_service = ImportService(self.db)
        self.assignment_service = AssignmentService(self.db)
        self.export_service = ExportService(self.db)
        self.report_service = ReportService(self.db)
        self.project_id = self.project_service.create_project(
            name="בדיקה",
            grade_level="ז",
            school_year="תשפז",
            class_count=2,
            class_names_text="ז1, ז2",
        )

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_import_assign_manual_undo_and_export(self) -> None:
        csv_path = self._write_students_csv()
        table = self.import_service.load_preview(csv_path)

        self.assertIn("שם מלא", table.headers)
        self.assertEqual(self.import_service.current_mapping["full_name"], "שם מלא")

        import_result = self.import_service.save_imported_students(self.project_id)
        self.assertEqual(import_result["students_count"], 6)
        self.assertEqual(import_result["critical_count"], 0)
        self.project_service.update_settings(self.project_id, {"friendship_required": False})

        result = self.assignment_service.run_assignment(self.project_id)
        assignments = result["assignments"]
        self.assertEqual(len(assignments), 6)
        self.assertGreater(result["score"]["total_score"], 70)

        class_sizes: dict[int, int] = {}
        for class_id in assignments.values():
            class_sizes[class_id] = class_sizes.get(class_id, 0) + 1
        self.assertLessEqual(max(class_sizes.values()) - min(class_sizes.values()), 1)

        first_student_id = next(iter(assignments))
        target_class_id = next(class_id for class_id in class_sizes if class_id != assignments[first_student_id])
        versions_before_move = self.db.get_assignment_versions(self.project_id)
        move_result = self.assignment_service.move_student(self.project_id, first_student_id, target_class_id, lock=True)
        self.assertIn("after", move_result)
        versions_after_move = self.db.get_assignment_versions(self.project_id)
        self.assertEqual(len(versions_after_move), len(versions_before_move))
        self.assertEqual(versions_after_move[0]["id"], versions_before_move[0]["id"])
        active_after_move = self.assignment_service.dashboard(self.project_id)
        moved_row = next(row for row in active_after_move["rows"] if row["student_id"] == first_student_id)
        self.assertTrue(moved_row["locked_manually"])

        self.assertTrue(self.assignment_service.undo(self.project_id))
        active_after_undo = self.assignment_service.dashboard(self.project_id)
        undo_row = next(row for row in active_after_undo["rows"] if row["student_id"] == first_student_id)
        self.assertEqual(undo_row["class_id"], assignments[first_student_id])
        self.assertIn("requested_friends", undo_row)
        self.assertIn("got_friend", undo_row)
        self.assertIn("friend_slots", undo_row)
        self.assertIn("reason_summary", undo_row)
        self.assertIn("behavior_counts", result["score"]["class_stats"][0])
        self.assertIn("math_avg", result["score"]["class_stats"][0])
        self.assertIn("quality_score", result["score"]["class_stats"][0])

        export_path = self.export_service.export_project(self.project_id, self.root / "out.xlsx")
        self.assertTrue(export_path.exists())
        with zipfile.ZipFile(export_path) as archive:
            names = set(archive.namelist())
        self.assertIn("xl/workbook.xml", names)
        self.assertIn("xl/worksheets/sheet1.xml", names)

        report = self.report_service.quality_report(self.project_id)
        self.assertTrue(report["has_assignment"])
        self.assertIn("manager_text", report)
        self.assertIn("teacher_summary", report)
        self.assertIn("global_stats", report)
        self.assertEqual(report["global_stats"]["student_count"], 6)

        suggestions = self.assignment_service.action_suggestions(self.project_id, limit=3)
        self.assertGreaterEqual(len(suggestions), 1)
        self.assertIn(suggestions[0]["action_type"], {"move", "swap"})
        self.assertIn("score_before", suggestions[0])
        self.assertIn("score_after", suggestions[0])
        self.assertIn("delta", suggestions[0])

        versions = self.db.get_assignment_versions(self.project_id)
        self.assertGreaterEqual(len(versions), 1)

    def test_validation_issues_export_is_available_before_assignment(self) -> None:
        student = self.db.replace_students(
            self.project_id,
            [Student(id=None, project_id=self.project_id, internal_code="001", full_name="נועה כהן")],
        )[0]
        self.db.replace_validation_issues(
            self.project_id,
            [
                ValidationIssue(
                    id=None,
                    project_id=self.project_id,
                    student_id=int(student.id),
                    field_name="friend_1",
                    severity="critical",
                    message="נועה כהן: החבר/ה לא נמצא/ה ברשימת התלמידים.",
                ),
                ValidationIssue(
                    id=None,
                    project_id=self.project_id,
                    student_id=None,
                    field_name="include_in_assignment",
                    severity="warning",
                    message="רשומה הוחרגה מהשיבוץ עד אימות ידני.",
                ),
            ],
        )

        export_path = self.export_service.export_validation_issues(self.project_id, self.root / "validation.xlsx")
        table = load_table(export_path, sheet_name="שגיאות")

        self.assertEqual(table.row_count, 2)
        self.assertIn("שם תלמיד", table.headers)
        self.assertEqual(table.rows[0]["חומרה"], "שגיאה קריטית")
        self.assertEqual(table.rows[0]["שם תלמיד"], "נועה כהן")
        self.assertIn("החבר/ה", table.rows[0]["פירוט"])

    def test_student_edit_mapping_template_and_ai_env_detection(self) -> None:
        csv_path = self._write_students_csv()
        self.import_service.load_preview(csv_path)
        self.import_service.save_imported_students(self.project_id)
        student = self.db.get_students(self.project_id)[0]

        self.db.update_student_fields(
            self.project_id,
            int(student.id),
            {"gender": "בן", "average_grade": 91, "not_allowed": "ignored"},
        )
        updated = self.db.get_students(self.project_id)[0]
        self.assertEqual(updated.gender, "בן")
        self.assertEqual(updated.average_grade, 91)

        template_path = self.root / "templates.json"
        save_template("בדיקה", self.import_service.current_mapping, ["שם מלא"], path=template_path)
        template = latest_template(path=template_path)
        self.assertIsNotNone(template)
        self.assertEqual(template["name"], "בדיקה")

        rule_path = self.root / "rule_templates.json"
        save_rule_template("כללים", {"balance_gender": False}, path=rule_path)
        rule_template = latest_rule_template(path=rule_path)
        self.assertEqual(rule_template["settings"]["balance_gender"], False)

        old_value = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-token"
        try:
            settings = load_ai_settings()
            openai = next(item for item in settings["providers"] if item["provider"] == "OpenAI")
            self.assertTrue(openai["configured"])
            self.assertEqual(openai["env_var"], "OPENAI_API_KEY")
        finally:
            if old_value is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_value

    def test_assignment_runs_are_versioned_and_renameable(self) -> None:
        csv_path = self._write_students_csv()
        self.import_service.load_preview(csv_path)
        self.import_service.save_imported_students(self.project_id)

        self.assignment_service.run_assignment(self.project_id)
        first_versions = self.db.get_assignment_versions(self.project_id)
        self.assertEqual(first_versions[0]["name"], "הרצה 1")

        self.db.rename_assignment_version(self.project_id, int(first_versions[0]["id"]), "הרצה לבדיקה")
        renamed = self.db.get_active_assignment_version(self.project_id)
        self.assertIsNotNone(renamed)
        self.assertEqual(renamed["name"], "הרצה לבדיקה")

        self.assignment_service.run_assignment(self.project_id)
        second_versions = self.db.get_assignment_versions(self.project_id)
        self.assertEqual(second_versions[0]["name"], "הרצה 2")
        self.assertGreaterEqual(len(second_versions), 2)

    def test_assignment_respects_hard_student_constraints(self) -> None:
        csv_path = self._write_students_csv()
        self.import_service.load_preview(csv_path)
        self.import_service.save_imported_students(self.project_id)

        students = self.db.get_students(self.project_id)
        classes = self.db.get_classes(self.project_id)
        class_by_name = {group.name: group for group in classes}
        first = students[0]
        second = students[1]
        forced_class = class_by_name["ז2"]
        forbidden_class = class_by_name["ז2"]

        self.db.replace_class_constraints(
            self.project_id,
            [
                {
                    "student_id": int(first.id),
                    "allowed_classes": [forced_class.name],
                    "forbidden_classes": [],
                    "locked_class_id": None,
                },
                {
                    "student_id": int(second.id),
                    "allowed_classes": [],
                    "forbidden_classes": [forbidden_class.name],
                    "locked_class_id": None,
                },
            ],
        )
        self.db.replace_pair_constraints(
            self.project_id,
            together=[],
            separation=[(int(first.id), int(second.id), "בדיקת הפרדה")],
        )
        self.project_service.update_settings(self.project_id, {"friendship_required": False})

        result = self.assignment_service.run_assignment(self.project_id)
        assignments = result["assignments"]

        self.assertEqual(assignments[int(first.id)], int(forced_class.id))
        self.assertNotEqual(assignments[int(second.id)], int(forbidden_class.id))
        self.assertNotEqual(assignments[int(first.id)], assignments[int(second.id)])
        self.assertEqual(result["score"]["hard_violations"], [])

    def test_hundred_student_demo_file_balances_without_empty_classes(self) -> None:
        project_id = self.project_service.create_project(
            name="בדיקת מאה תלמידים",
            grade_level="ז",
            school_year="תשפז",
            class_count=4,
            class_names_text="כיתה 1, כיתה 2, כיתה 3, כיתה 4",
        )
        csv_path = Path("examples/demo_students_100_easy.csv")
        self.import_service.load_preview(csv_path)
        import_result = self.import_service.save_imported_students(project_id)
        result = self.assignment_service.run_assignment(project_id)

        self.assertEqual(import_result["students_count"], 100)
        self.assertEqual(import_result["critical_count"], 0)
        self.assertEqual(result["score"]["hard_violations"], [])
        class_sizes = [stat["size"] for stat in result["score"]["class_stats"]]
        self.assertEqual(class_sizes, [25, 25, 25, 25])
        self.assertGreater(result["score"]["total_score"], 70)
        self.assertLessEqual(len(result["score"]["friendship"]["missing"]), 4)

    def test_auto_ai_review_payload_is_anonymous_and_bounded(self) -> None:
        csv_path = self._write_students_csv()
        self.import_service.load_preview(csv_path)
        self.import_service.save_imported_students(self.project_id)
        self.assignment_service.run_assignment(self.project_id)

        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = self.project_id
        self.assertEqual(len(bridge.studentsForClass(0)), 6)
        payload = bridge._auto_ai_review_payload(self.project_id, 60.0)
        payload_text = str(payload)

        self.assertFalse(payload["privacy"]["contains_student_names"])
        self.assertFalse(payload["privacy"]["contains_notes"])
        self.assertFalse(payload["privacy"]["contains_raw_rows"])
        self.assertNotIn("נועה", payload_text)
        self.assertNotIn("כהן", payload_text)
        self.assertLessEqual(len(payload["classes"]), 12)

    def test_conflicts_report_for_page_navigation_is_lazy(self) -> None:
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = self.project_id

        def fail_if_called(project_id: int) -> dict[str, object]:
            raise AssertionError(f"conflicts report should not be computed synchronously for {project_id}")

        bridge._conflicts_report = fail_if_called  # type: ignore[method-assign]
        data = bridge.conflictsReport()

        self.assertEqual(data["status"], "not_loaded")
        self.assertEqual(data["conflicts"], [])
        self.assertEqual(data["action_candidates"], [])

    def test_conflicts_report_async_state_does_not_use_global_busy(self) -> None:
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = self.project_id
        started: list[int] = []

        def fake_start(project_id: int) -> None:
            started.append(project_id)
            bridge._conflicts_report_loading_project_id = project_id
            bridge._conflicts_report_status = {
                "status": "running",
                "message": "loading",
                "project_id": project_id,
                "conflicts": [],
                "suggested_actions": [],
                "action_candidates": [],
            }

        bridge._start_conflicts_report_worker = fake_start  # type: ignore[method-assign]
        result = bridge.loadConflictsReportAsync()

        self.assertTrue(result["started"])
        self.assertEqual(started, [self.project_id])
        self.assertEqual(bridge.conflictsReport()["status"], "running")
        self.assertFalse(bridge.busy)

    def test_ai_report_assistant_async_does_not_build_report_on_click(self) -> None:
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = self.project_id
        calls: list[dict[str, object]] = []

        def fail_if_called(project_id: int) -> dict[str, object]:
            raise AssertionError(f"quality report should not be built synchronously for {project_id}")

        def fake_start(
            request_id: str,
            task: str,
            payload: dict[str, object] | None,
            fallback: str | None,
            allow_external: bool,
            project_id: int | None = None,
        ) -> None:
            calls.append(
                {
                    "request_id": request_id,
                    "payload": payload,
                    "fallback": fallback,
                    "allow_external": allow_external,
                    "project_id": project_id,
                }
            )

        bridge.report_service.quality_report = fail_if_called  # type: ignore[method-assign]
        bridge._start_assistant_request = fake_start  # type: ignore[method-assign]

        result = bridge.askAiAssistantAsync("report", "כתוב דוח מפורט", False)

        self.assertTrue(result["started"])
        self.assertEqual(calls, [{
            "request_id": "report",
            "payload": None,
            "fallback": None,
            "allow_external": False,
            "project_id": self.project_id,
        }])

    def test_stale_conflicts_report_worker_result_is_ignored(self) -> None:
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = self.project_id
        bridge._conflicts_report_request_token = 4

        stale_report = {"conflicts": [{"message": "old"}], "suggested_actions": [], "action_candidates": []}
        bridge._conflicts_report_worker_finished(self.project_id, 3, stale_report)
        self.assertNotIn(self.project_id, bridge._conflicts_report_cache)

        fresh_report = {"conflicts": [{"message": "new"}], "suggested_actions": [], "action_candidates": []}
        bridge._conflicts_report_worker_finished(self.project_id, 4, fresh_report)
        self.assertEqual(bridge.conflictsReport()["status"], "ready")
        self.assertEqual(bridge.conflictsReport()["conflicts"][0]["message"], "new")

    def test_project_ai_permission_is_single_project_setting(self) -> None:
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = self.project_id

        self.assertFalse(bridge.projectAllowsExternalAi())
        self.assertTrue(bridge.setProjectAllowsExternalAi(True))
        self.assertTrue(bridge.projectAllowsExternalAi())

        bridge.saveRuleSettings({"balance_gender": False})
        self.assertTrue(bridge.projectAllowsExternalAi())
        self.assertFalse(bridge.ruleSettings()["balance_gender"])

    def test_rule_settings_accept_qml_js_object(self) -> None:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtQml import QJSEngine

        _app = QCoreApplication.instance() or QCoreApplication([])
        engine = QJSEngine()
        value = engine.evaluate("({ balance_gender: false, max_iterations: 240 })")
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = self.project_id

        bridge.saveRuleSettings(value)
        settings = bridge.ruleSettings()

        self.assertFalse(settings["balance_gender"])
        self.assertEqual(settings["max_iterations"], 240)

    def test_bulk_allowed_classes_and_friend_slots(self) -> None:
        csv_path = self._write_students_csv()
        self.import_service.load_preview(csv_path)
        self.import_service.save_imported_students(self.project_id)
        students = self.db.get_students(self.project_id)
        classes = self.db.get_classes(self.project_id)
        allowed = [classes[0].name]
        selected_ids = [int(students[0].id), int(students[1].id)]
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = self.project_id

        updated_count = bridge.applyAllowedClassesToStudents(selected_ids, allowed)
        result = self.assignment_service.run_assignment(self.project_id)
        dashboard = self.assignment_service.dashboard(self.project_id)

        self.assertEqual(updated_count, 2)
        for student_id in selected_ids:
            self.assertEqual(result["assignments"][student_id], int(classes[0].id))
        row = next(item for item in dashboard["rows"] if item["student_id"] == selected_ids[0])
        self.assertEqual([slot["priority"] for slot in row["friend_slots"]], [1, 2, 3])
        self.assertTrue(row["friend_slots"][0]["requested"])
        self.assertIn(row["friend_slots"][0]["status"], {"received", "missing"})

    def test_friendship_priority_order_changes_penalty_weighting(self) -> None:
        csv_path = self._write_students_csv()
        self.import_service.load_preview(csv_path)
        self.import_service.save_imported_students(self.project_id)
        students = self.db.get_students(self.project_id)
        classes = self.db.get_classes(self.project_id)
        assignments = {
            int(students[0].id): int(classes[0].id),
            int(students[1].id): int(classes[1].id),
            int(students[2].id): int(classes[0].id),
            int(students[3].id): int(classes[0].id),
            int(students[4].id): int(classes[1].id),
            int(students[5].id): int(classes[1].id),
        }
        friendships = [
            {"student_id": int(students[0].id), "requested_friend_id": int(students[1].id), "priority": 1},
            {"student_id": int(students[0].id), "requested_friend_id": int(students[2].id), "priority": 2},
        ]
        from class_balancer.assignment_engine.scoring import evaluate_assignment

        flat = evaluate_assignment(students, classes, assignments, friendships, settings={"friendship": True})
        ordered = evaluate_assignment(
            students,
            classes,
            assignments,
            friendships,
            settings={"friendship": True, "friendship_priority_order": True},
        )

        self.assertGreater(ordered["penalties"]["friendship"], flat["penalties"]["friendship"])

    def test_friendship_first_rank_keeps_friend_coverage_before_class_size_guard(self) -> None:
        csv_path = self._write_students_csv()
        self.import_service.load_preview(csv_path)
        self.import_service.save_imported_students(self.project_id)
        students = self.db.get_students(self.project_id)
        classes = self.db.get_classes(self.project_id)
        friendships = [
            {"student_id": int(students[0].id), "requested_friend_id": int(students[1].id), "priority": 1},
        ]
        missing_assignment = {
            int(students[0].id): int(classes[0].id),
            int(students[1].id): int(classes[1].id),
            int(students[2].id): int(classes[0].id),
            int(students[3].id): int(classes[0].id),
            int(students[4].id): int(classes[1].id),
            int(students[5].id): int(classes[1].id),
        }
        covered_assignment = dict(missing_assignment)
        covered_assignment[int(students[1].id)] = int(classes[0].id)
        from class_balancer.assignment_engine.scoring import evaluate_assignment
        from class_balancer.services.assignment_service import _score_rank

        missing = evaluate_assignment(
            students,
            classes,
            missing_assignment,
            friendships,
            settings={"friendship": True, "friendship_first": True},
        )
        covered = evaluate_assignment(
            students,
            classes,
            covered_assignment,
            friendships,
            settings={"friendship": True, "friendship_first": True},
        )

        self.assertEqual(missing["friendship"]["priority_missing"], 1)
        self.assertEqual(covered["friendship"]["priority_missing"], 0)
        self.assertEqual(missing["objective"]["class_size_guard_issues"], 0)
        self.assertGreater(covered["objective"]["class_size_guard_issues"], 0)
        self.assertGreater(_score_rank(covered), _score_rank(missing))

    def test_multiple_students_can_request_same_friend_without_duplicate_issue(self) -> None:
        students = [
            Student(id=1, project_id=self.project_id, internal_code="S001", full_name="נועה כהן"),
            Student(
                id=2,
                project_id=self.project_id,
                internal_code="S002",
                full_name="מאיה לוי",
                raw_data={"_mapped_values": {"friend_1": "נועה כהן"}},
            ),
            Student(
                id=3,
                project_id=self.project_id,
                internal_code="S003",
                full_name="רוני פרץ",
                raw_data={"_mapped_values": {"friend_1": "נועה כהן"}},
            ),
        ]

        issues = validate_students(
            project_id=self.project_id,
            students=students,
            class_names=["ז1", "ז2"],
            settings={"friendship": True, "balance_gender": False, "balance_grades": False, "spread_source_school": False},
        )

        self.assertFalse(
            [
                issue
                for issue in issues
                if issue.severity == "critical" or "כפילות" in issue.message
            ]
        )

    def test_hebrew_class_geresh_is_ignored_when_matching_class_names(self) -> None:
        self.assertEqual(normalize_name_key("ז׳1"), normalize_name_key("ז1"))
        self.assertEqual(normalize_name_key("ז״2"), normalize_name_key("ז2"))

    def test_delete_project_removes_it_from_recent_projects(self) -> None:
        second_id = self.project_service.create_project(
            name="פרויקט למחיקה",
            grade_level="ח",
            school_year="תשפז",
            class_count=2,
            class_names_text="ח1, ח2",
        )
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = second_id

        self.assertTrue(bridge.deleteProject(second_id))
        project_ids = {item["id"] for item in bridge.recentProjects()}
        self.assertNotIn(second_id, project_ids)
        self.assertNotEqual(bridge.currentProject().get("id"), second_id)

    def test_ai_client_local_fallback_without_enabled_ai(self) -> None:
        old_enabled = os.environ.get("CLASS_BALANCER_AI_ENABLED")
        os.environ["CLASS_BALANCER_AI_ENABLED"] = "false"
        try:
            result = AiClient().complete(
                "סכם דוח",
                {"students": [{"id": "S001", "average_grade": 90}]},
                "סיכום מקומי",
            )
            self.assertFalse(result["used_ai"])
            self.assertIn("סיכום מקומי", result["text"])
        finally:
            if old_enabled is None:
                os.environ.pop("CLASS_BALANCER_AI_ENABLED", None)
            else:
                os.environ["CLASS_BALANCER_AI_ENABLED"] = old_enabled

    def test_local_structured_ai_review_has_system_shape(self) -> None:
        payload = {"penalties": {"friendship": 12, "class_size": 0}}
        review = _local_structured_review("בדיקה מקומית", payload)

        self.assertIn("summary_he", review)
        self.assertIn("recommendations", review)
        self.assertTrue(review["recommendations"][0]["privacy_safe"])

    def test_malformed_structured_ai_response_becomes_usable_warning(self) -> None:
        parsed, warning = _parse_structured_response('{"summary_he":"תשובה שנחתכה באמצע')

        self.assertIn("summary_he", parsed)
        self.assertIn("recommendations", parsed)
        self.assertTrue(warning)
        self.assertTrue(parsed["parse_failed"])

    def test_repairable_structured_ai_response_is_not_parse_failure(self) -> None:
        parsed, warning = _parse_structured_response(
            "{summary_he: 'סיכום', risk_level: 'low', recommendations: [], best_recommendation_index: 0}"
        )

        self.assertEqual(parsed["summary_he"], "סיכום")
        self.assertFalse(parsed["parse_failed"])
        self.assertTrue(warning)

    def test_action_selection_ai_response_maps_candidate_ids(self) -> None:
        parsed, warning = _parse_action_selection_response(
            '{"summary_he":"בחרתי פעולה אחת שמשפרת ציון","no_improvement":false,'
            '"selected_candidate_ids":["A001"],'
            '"notes":[{"candidate_id":"A001","reason_he":"שיפור ללא כלל חדש"}]}'
        )

        self.assertFalse(parsed["parse_failed"])
        self.assertFalse(parsed["no_improvement"])
        self.assertEqual(parsed["selected_candidate_ids"], ["A001"])
        self.assertEqual(parsed["notes"][0]["reason_he"], "שיפור ללא כלל חדש")
        self.assertEqual(warning, "")

    def test_action_selection_collects_all_providers_before_choosing(self) -> None:
        class MultiProviderAiClient(AiClient):
            def _send(self, provider, *args, **kwargs):  # type: ignore[no-untyped-def]
                if provider == "OpenAI":
                    return (
                        '{"summary_he":"openai","no_improvement":false,'
                        '"selected_candidate_ids":["A001"],'
                        '"notes":[{"candidate_id":"A001","reason_he":"small score gain"}]}'
                    )
                return (
                    '{"summary_he":"other","no_improvement":false,'
                    '"selected_candidate_ids":["A002"],'
                    '"notes":[{"candidate_id":"A002","reason_he":"friendship gain"}]}'
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
            result = MultiProviderAiClient().complete_action_selection(
                "select",
                {
                    "candidate_actions": [
                        {
                            "candidate_id": "A001",
                            "delta": 1.0,
                            "score_after": 81,
                            "hard_before": 0,
                            "hard_after": 0,
                            "friendship_missing_before": 2,
                            "friendship_missing_after": 2,
                        },
                        {
                            "candidate_id": "A002",
                            "delta": -0.2,
                            "score_after": 79,
                            "hard_before": 0,
                            "hard_after": 0,
                            "friendship_missing_before": 2,
                            "friendship_missing_after": 1,
                        },
                    ]
                },
                "fallback",
                allow_external=True,
                provider_limit=3,
            )
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(result["used_ai"])
        self.assertEqual(len(result["providers"]), 3)
        self.assertEqual(result["source"], "multi-provider")
        self.assertEqual(result["selection"]["selected_candidate_ids"][0], "A002")

    def test_broken_structured_provider_is_not_counted_as_ai_success(self) -> None:
        class BrokenAiClient(AiClient):
            def _send(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                return '{"summary_he":"תשובה שנחתכה באמצע'

        old_enabled = os.environ.get("CLASS_BALANCER_AI_ENABLED")
        old_token = os.environ.get("OPENAI_API_KEY")
        os.environ["CLASS_BALANCER_AI_ENABLED"] = "true"
        os.environ["OPENAI_API_KEY"] = "test-token"
        try:
            result = BrokenAiClient().complete_structured_all(
                "בדיקה",
                {"penalties": {"friendship": 12}},
                "סיכום מקומי",
                allow_external=True,
                provider_limit=1,
            )
            self.assertFalse(result["used_ai"])
            self.assertEqual(result["status"], "ai_failed")
            self.assertTrue(result["providers"][0]["parsed"]["parse_failed"])
        finally:
            if old_enabled is None:
                os.environ.pop("CLASS_BALANCER_AI_ENABLED", None)
            else:
                os.environ["CLASS_BALANCER_AI_ENABLED"] = old_enabled
            if old_token is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_token

    def _write_students_csv(self) -> Path:
        csv_path = self.root / "students.csv"
        rows = [
            ["שם מלא", "מין", "בית ספר קודם", "ממוצע", "התנהגות", "חבר 1"],
            ["נועה כהן", "בת", "אלון", "92", "מצוין", "מאיה לוי"],
            ["מאיה לוי", "בת", "אלון", "88", "טובה", "נועה כהן"],
            ["יואב מזרחי", "בן", "ברוש", "77", "בינוני", "אדם פרץ"],
            ["אדם פרץ", "בן", "ברוש", "81", "טובה", "יואב מזרחי"],
            ["רוני שלום", "בת", "ארז", "69", "בעייתית", "דניאל חדד"],
            ["דניאל חדד", "בן", "ארז", "95", "מצוין", "רוני שלום"],
        ]
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerows(rows)
        return csv_path


if __name__ == "__main__":
    unittest.main()
