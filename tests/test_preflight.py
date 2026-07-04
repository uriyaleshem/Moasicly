from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from class_balancer.assignment_engine import PreflightError
from class_balancer.db import Database
from class_balancer.models.entities import Student
from class_balancer.services import AssignmentService, ProjectService


class PreflightHardConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db = Database(self.root / "test.sqlite3")
        self.project_service = ProjectService(self.db)
        self.assignment_service = AssignmentService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_together_disjoint_allowed_classes_blocks_and_does_not_save(self) -> None:
        project_id, students, classes = self._project_with_students(2)
        self.db.replace_class_constraints(
            project_id,
            [
                {
                    "student_id": int(students[0].id),
                    "allowed_classes": [classes[0].name],
                    "forbidden_classes": [],
                    "locked_class_id": None,
                },
                {
                    "student_id": int(students[1].id),
                    "allowed_classes": [classes[1].name],
                    "forbidden_classes": [],
                    "locked_class_id": None,
                },
            ],
        )
        self.db.replace_pair_constraints(
            project_id,
            together=[(int(students[0].id), int(students[1].id), "test")],
            separation=[],
        )

        codes = self._best_effort_codes(project_id)

        self.assertIn("TOGETHER_GROUP_EMPTY_DOMAIN", codes)

    def test_empty_student_domain_blocks_candidate_fallback(self) -> None:
        project_id, students, classes = self._project_with_students(1)
        self.db.replace_class_constraints(
            project_id,
            [
                {
                    "student_id": int(students[0].id),
                    "allowed_classes": [classes[0].name],
                    "forbidden_classes": [classes[0].name],
                    "locked_class_id": None,
                }
            ],
        )

        codes = self._best_effort_codes(project_id)

        self.assertIn("STUDENT_EMPTY_DOMAIN", codes)

    def test_different_locks_in_together_component_blocks(self) -> None:
        project_id, students, classes = self._project_with_students(2)
        self.db.replace_class_constraints(
            project_id,
            [
                {
                    "student_id": int(students[0].id),
                    "allowed_classes": [],
                    "forbidden_classes": [],
                    "locked_class_id": int(classes[0].id),
                },
                {
                    "student_id": int(students[1].id),
                    "allowed_classes": [],
                    "forbidden_classes": [],
                    "locked_class_id": int(classes[1].id),
                },
            ],
        )
        self.db.replace_pair_constraints(
            project_id,
            together=[(int(students[0].id), int(students[1].id), "test")],
            separation=[],
        )

        codes = self._best_effort_codes(project_id)

        self.assertIn("TOGETHER_GROUP_LOCK_CONFLICT", codes)

    def test_same_pair_together_and_separate_blocks(self) -> None:
        project_id, students, _ = self._project_with_students(2)
        pair = (int(students[0].id), int(students[1].id), "test")
        self.db.replace_pair_constraints(project_id, together=[pair], separation=[pair])

        codes = self._best_effort_codes(project_id)

        self.assertIn("TOGETHER_SEPARATION_CONFLICT", codes)

    def test_locked_students_exceeding_hard_capacity_blocks(self) -> None:
        project_id, students, classes = self._project_with_students(2, settings={"hard_class_capacity": True})
        self._set_class_capacity(int(classes[0].id), max_students=1)
        self.db.replace_class_constraints(
            project_id,
            [
                {
                    "student_id": int(student.id),
                    "allowed_classes": [],
                    "forbidden_classes": [],
                    "locked_class_id": int(classes[0].id),
                }
                for student in students
            ],
        )

        codes = self._best_effort_codes(project_id)

        self.assertIn("LOCKED_CLASS_CAPACITY_EXCEEDED", codes)

    def test_total_hard_capacity_too_small_blocks(self) -> None:
        project_id, _, classes = self._project_with_students(3, settings={"hard_class_capacity": True})
        for group in classes:
            self._set_class_capacity(int(group.id), max_students=1)

        codes = self._best_effort_codes(project_id)

        self.assertIn("TOTAL_HARD_MAX_CAPACITY_TOO_SMALL", codes)

    def test_unknown_class_reference_is_structured_issue(self) -> None:
        project_id, students, _ = self._project_with_students(1)
        self.db.replace_class_constraints(
            project_id,
            [
                {
                    "student_id": int(students[0].id),
                    "allowed_classes": ["Missing class"],
                    "forbidden_classes": [],
                    "locked_class_id": None,
                }
            ],
        )

        codes = self._blocked_codes(project_id)

        self.assertIn("UNKNOWN_CLASS_REFERENCE", codes)
        self.assert_no_assignment_saved(project_id)

    def test_hard_friendship_impossible_saves_best_effort(self) -> None:
        project_id, students, classes = self._project_with_students(2, settings={"friendship_hard": True})
        self.db.replace_friendships(project_id, [(int(students[0].id), int(students[1].id), 1)])
        self.db.replace_class_constraints(
            project_id,
            [
                {
                    "student_id": int(students[0].id),
                    "allowed_classes": [classes[0].name],
                    "forbidden_classes": [],
                    "locked_class_id": None,
                },
                {
                    "student_id": int(students[1].id),
                    "allowed_classes": [classes[1].name],
                    "forbidden_classes": [],
                    "locked_class_id": None,
                },
            ],
        )

        result = self.assignment_service.run_assignment(project_id)
        codes = {
            issue["code"]
            for issue in result["score"]["preflight"]["issues"]
            if issue["severity"] == "critical"
        }

        self.assertIn("FRIENDSHIP_HARD_IMPOSSIBLE", codes)
        self.assertGreater(len(result["score"]["hard_violations"]), 0)
        self.assertIsNotNone(self.db.get_active_assignment_version(project_id))

    def _project_with_students(
        self,
        count: int,
        settings: dict[str, object] | None = None,
    ) -> tuple[int, list[Student], list[object]]:
        project_id = self.project_service.create_project(
            name="preflight",
            grade_level="7",
            school_year="2026",
            class_count=2,
            class_names_text="A, B",
        )
        if settings:
            self.project_service.update_settings(project_id, settings)
        students = self.db.replace_students(
            project_id,
            [
                Student(
                    id=None,
                    project_id=project_id,
                    internal_code=f"S{index:03d}",
                    full_name=f"Student {index}",
                )
                for index in range(1, count + 1)
            ],
        )
        return project_id, students, self.db.get_classes(project_id)

    def _blocked_codes(self, project_id: int) -> set[str]:
        with self.assertRaises(PreflightError) as caught:
            self.assignment_service.run_assignment(project_id)
        report = caught.exception.report.to_dict()
        self.assertFalse(report["ok"])
        return {issue["code"] for issue in report["issues"] if issue["severity"] == "critical"}

    def _best_effort_codes(self, project_id: int) -> set[str]:
        result = self.assignment_service.run_assignment(project_id)
        self.assertIsNotNone(self.db.get_active_assignment_version(project_id))
        self.assertGreater(len(result["score"]["hard_violations"]), 0)
        report = result["score"]["preflight"]
        self.assertFalse(report["ok"])
        return {issue["code"] for issue in report["issues"] if issue["severity"] == "critical"}

    def _set_class_capacity(self, class_id: int, max_students: int, min_students: int = 0) -> None:
        self.db.connection.execute(
            "UPDATE classes SET min_students = ?, max_students = ? WHERE id = ?",
            (min_students, max_students, class_id),
        )
        self.db.connection.commit()

    def assert_no_assignment_saved(self, project_id: int) -> None:
        self.assertIsNone(self.db.get_active_assignment_version(project_id))
        self.assertEqual(self.db.get_assignment_versions(project_id), [])


if __name__ == "__main__":
    unittest.main()
