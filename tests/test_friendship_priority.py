from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from class_balancer.db import Database
from class_balancer.assignment_engine.scoring import evaluate_assignment
from class_balancer.assignment_engine.engine import AssignmentEngine
from class_balancer.models.entities import ClassGroup
from class_balancer.models.entities import Student
from class_balancer.services import AssignmentService, ExportService, ImportService, ProjectService, ReportService
from class_balancer.services.assignment_service import _effective_variant_count, _score_rank
from class_balancer.ui.bridge import AppBridge


class FriendshipPriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db = Database(self.root / "test.sqlite3")
        self.project_service = ProjectService(self.db)
        self.import_service = ImportService(self.db)
        self.assignment_service = AssignmentService(self.db)
        self.export_service = ExportService(self.db)
        self.report_service = ReportService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_friendship_first_flows_from_bridge_and_changes_assignment_result(self) -> None:
        project_id = self.project_service.create_project("priority", "7", "2026", 2, "A, B")
        students = self._replace_students(project_id, [100, 100, 100, 100, 50, 50, 50, 50])
        ids = [int(student.id) for student in students]
        self.db.replace_friendships(
            project_id,
            [
                (ids[0], ids[1], 1),
                (ids[1], ids[0], 1),
                (ids[2], ids[3], 1),
                (ids[3], ids[2], 1),
            ],
        )
        bridge = self._bridge(project_id)
        settings = {
            "friendship": True,
            "friendship_weight": 0.5,
            "balance_gender": False,
            "balance_behavior": False,
            "spread_dominant_students": False,
            "spread_source_school": False,
            "search_restarts": 1,
            "max_iterations": 100,
            "stop_when_score_at_least": 90,
            "optimizer_backend": "local",
            "ai_auto_review": False,
            "friendship_required": False,
        }

        bridge.saveRuleSettings({**settings, "friendship_first": False})
        without_priority = self.assignment_service.run_assignment(project_id, variant_count=1)

        bridge.saveRuleSettings({**settings, "friendship_first": True})
        with_priority = self.assignment_service.run_assignment(project_id, variant_count=1)

        self.assertFalse(without_priority["score"]["friendship_first_active"])
        self.assertTrue(with_priority["score"]["friendship_first_active"])
        self.assertTrue(bridge.ruleSettings()["friendship_first"])
        self.assertGreater(
            len(without_priority["score"]["friendship"]["missing"]),
            len(with_priority["score"]["friendship"]["missing"]),
        )
        self.assertEqual(len(with_priority["score"]["friendship"]["missing"]), 0)
        self.assertEqual(with_priority["score"]["hard_violations"], [])
        self.assertEqual(set(with_priority["assignments"]), set(ids))

    def test_friendship_first_keeps_hard_constraints_above_impossible_friend_requests(self) -> None:
        project_id = self.project_service.create_project("hard-before-friends", "7", "2026", 2, "A, B")
        students = self._replace_students(project_id, [80, 81, 82, 83, 84, 85])
        ids = [int(student.id) for student in students]
        classes = self.db.get_classes(project_id)
        class_a = int(classes[0].id)
        class_b = int(classes[1].id)
        self.project_service.update_settings(
            project_id,
            {
                "friendship": True,
                "friendship_first": True,
                "class_size_weight": 0,
                "balance_gender": False,
                "balance_grades": False,
                "balance_behavior": False,
                "spread_source_school": False,
                "spread_dominant_students": False,
                "optimizer_backend": "local",
                "search_restarts": 5,
                "max_iterations": 100,
                "stop_when_score_at_least": 95,
                "ai_auto_review": False,
            },
        )
        self.db.replace_class_constraints(
            project_id,
            [
                {"student_id": ids[0], "allowed_classes": [], "forbidden_classes": [], "locked_class_id": class_a},
                {"student_id": ids[1], "allowed_classes": [], "forbidden_classes": [], "locked_class_id": class_b},
            ],
        )
        self.db.replace_pair_constraints(
            project_id,
            together=[(ids[2], ids[3], "must stay together")],
            separation=[(ids[0], ids[1], "must stay apart"), (ids[0], ids[3], "must stay apart")],
        )
        self.db.replace_friendships(
            project_id,
            [
                (ids[0], ids[1], 1),
                (ids[2], ids[0], 1),
                (ids[4], ids[1], 1),
                (ids[5], ids[1], 1),
            ],
        )

        result = self.assignment_service.run_assignment(project_id, variant_count=1)
        assignments = result["assignments"]

        self.assertEqual(assignments[ids[0]], class_a)
        self.assertEqual(assignments[ids[1]], class_b)
        self.assertNotEqual(assignments[ids[0]], assignments[ids[1]])
        self.assertEqual(assignments[ids[2]], assignments[ids[3]])
        self.assertNotEqual(assignments[ids[0]], assignments[ids[3]])
        self.assertEqual(assignments[ids[4]], assignments[ids[1]])
        self.assertEqual(assignments[ids[5]], assignments[ids[1]])
        missing_ids = {int(item["student_id"]) for item in result["score"]["friendship"]["missing"]}
        self.assertEqual(missing_ids, {ids[0], ids[2]})
        self.assertEqual(len(result["score"]["hard_violations"]), len(missing_ids))

    def test_large_projects_honor_multiple_requested_variants(self) -> None:
        self.assertEqual(_effective_variant_count(4, 230, {}), 4)
        self.assertEqual(_effective_variant_count(12, 230, {}), 12)
        self.assertEqual(_effective_variant_count(12, 230, {"allow_slow_large_search": True}), 12)

    def test_friendship_first_prefers_full_friend_coverage_before_class_balance(self) -> None:
        students = [
            Student(id=index, project_id=1, internal_code=f"S{index:03d}", full_name=f"Student {index}")
            for index in range(1, 13)
        ]
        classes = [ClassGroup(id=index, project_id=1, name=f"C{index}") for index in range(1, 5)]
        friendships = [
            {"student_id": index, "requested_friend_id": index + 1, "priority": 1}
            for index in range(1, 12, 2)
        ] + [
            {"student_id": index + 1, "requested_friend_id": index, "priority": 1}
            for index in range(1, 12, 2)
        ]
        clustered = {
            1: 1,
            2: 1,
            3: 1,
            4: 1,
            5: 1,
            6: 1,
            7: 2,
            8: 2,
            9: 2,
            10: 2,
            11: 2,
            12: 2,
        }
        balanced = {student.id: ((int(student.id) - 1) % 4) + 1 for student in students if student.id}
        settings = {"friendship": True, "friendship_first": True}

        clustered_score = evaluate_assignment(students, classes, clustered, friendships, settings=settings)
        balanced_score = evaluate_assignment(students, classes, balanced, friendships, settings=settings)

        self.assertGreater(len(clustered_score["friendship"]["satisfied"]), len(balanced_score["friendship"]["satisfied"]))
        self.assertGreater(clustered_score["objective"]["class_size_guard_issues"], 0)
        self.assertEqual(balanced_score["objective"]["class_size_guard_issues"], 0)
        self.assertGreater(_score_rank(clustered_score), _score_rank(balanced_score))

        regular_settings = {"friendship": True, "friendship_first": False}
        regular_clustered_score = evaluate_assignment(students, classes, clustered, friendships, settings=regular_settings)
        regular_balanced_score = evaluate_assignment(students, classes, balanced, friendships, settings=regular_settings)
        self.assertFalse(regular_clustered_score["friendship_first_active"])
        self.assertGreater(_score_rank(regular_balanced_score), _score_rank(regular_clustered_score))

    def test_friendship_first_improvement_does_not_break_full_coverage_for_balance(self) -> None:
        students = [
            Student(id=index, project_id=1, internal_code=f"S{index:03d}", full_name=f"Student {index}")
            for index in range(1, 7)
        ]
        classes = [ClassGroup(id=1, project_id=1, name="A"), ClassGroup(id=2, project_id=1, name="B")]
        friendships = [
            {"student_id": 1, "requested_friend_id": 2, "priority": 1},
            {"student_id": 2, "requested_friend_id": 1, "priority": 1},
            {"student_id": 3, "requested_friend_id": 4, "priority": 1},
            {"student_id": 4, "requested_friend_id": 3, "priority": 1},
            {"student_id": 5, "requested_friend_id": 6, "priority": 1},
            {"student_id": 6, "requested_friend_id": 5, "priority": 1},
        ]
        assignments = {1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 2}
        settings = {
            "friendship": True,
            "friendship_first": True,
            "friendship_required": False,
            "balance_gender": False,
            "balance_grades": False,
            "balance_behavior": False,
            "spread_source_school": False,
            "spread_dominant_students": False,
            "optimizer_backend": "local",
            "max_iterations": 8,
            "stop_when_score_at_least": 100,
        }
        score = evaluate_assignment(students, classes, assignments, friendships, settings=settings)
        self.assertEqual(len(score["friendship"]["missing"]), 0)
        self.assertGreater(score["objective"]["class_size_guard_issues"], 0)

        improved_assignments, improved_score = AssignmentEngine()._improve(
            students,
            classes,
            assignments,
            score,
            friendships,
            [],
            {"together": [], "separation": []},
            settings,
            {},
        )

        self.assertEqual(len(improved_score["friendship"]["missing"]), 0)
        self.assertEqual(improved_assignments[1], improved_assignments[2])
        self.assertEqual(improved_assignments[3], improved_assignments[4])
        self.assertEqual(improved_assignments[5], improved_assignments[6])

    def test_source_school_priority_keeps_class_balance_before_school_spread(self) -> None:
        students = [
            Student(id=index, project_id=1, internal_code=f"A{index:03d}", full_name=f"A {index}", source_school="A")
            for index in range(1, 7)
        ] + [
            Student(id=index, project_id=1, internal_code=f"B{index:03d}", full_name=f"B {index}", source_school="B")
            for index in range(7, 13)
        ]
        classes = [ClassGroup(id=1, project_id=1, name="A"), ClassGroup(id=2, project_id=1, name="B")]
        size_balanced_school_clustered = {student.id: (1 if student.source_school == "A" else 2) for student in students}
        school_balanced_size_uneven = {
            1: 1,
            2: 1,
            3: 1,
            4: 1,
            5: 2,
            6: 2,
            7: 1,
            8: 1,
            9: 1,
            10: 1,
            11: 2,
            12: 2,
        }
        settings = {
            "friendship": False,
            "spread_source_school": True,
            "source_school_weight": 3.0,
            "balance_gender": False,
            "balance_grades": False,
            "balance_behavior": False,
            "spread_dominant_students": False,
        }

        clustered_score = evaluate_assignment(students, classes, size_balanced_school_clustered, settings=settings)
        school_score = evaluate_assignment(students, classes, school_balanced_size_uneven, settings=settings)

        self.assertLess(school_score["objective"]["source_school_imbalance"], clustered_score["objective"]["source_school_imbalance"])
        self.assertGreater(school_score["objective"]["class_size_guard_issues"], 0)
        self.assertEqual(clustered_score["objective"]["class_size_guard_issues"], 0)
        self.assertGreater(_score_rank(clustered_score), _score_rank(school_score))

    def test_assignment_spreads_source_schools_when_weight_is_high(self) -> None:
        project_id = self.project_service.create_project("schools", "7", "2026", 2, "A, B")
        students = self.db.replace_students(
            project_id,
            [
                Student(
                    id=None,
                    project_id=project_id,
                    internal_code=f"S{index:03d}",
                    full_name=f"Student {index}",
                    source_school="Alpha" if index <= 6 else "Beta",
                )
                for index in range(1, 13)
            ],
        )
        self.project_service.update_settings(
            project_id,
            {
                "friendship": False,
                "spread_source_school": True,
                "source_school_weight": 3.0,
                "balance_gender": False,
                "balance_grades": False,
                "balance_behavior": False,
                "spread_dominant_students": False,
                "optimizer_backend": "local",
                "search_restarts": 4,
                "max_iterations": 80,
                "ai_auto_review": False,
            },
        )

        result = self.assignment_service.run_assignment(project_id, variant_count=1)
        class_stats = result["score"]["class_stats"]

        self.assertEqual(result["score"]["hard_violations"], [])
        for school in ("Alpha", "Beta"):
            counts = [int(stat["schools"].get(school, 0)) for stat in class_stats]
            self.assertEqual(counts, [3, 3])

    def test_friendship_first_can_move_toward_friend_in_together_group(self) -> None:
        students = [
            Student(id=index, project_id=1, internal_code=f"S{index:03d}", full_name=f"Student {index}")
            for index in range(1, 9)
        ]
        classes = [ClassGroup(id=1, project_id=1, name="A"), ClassGroup(id=2, project_id=1, name="B")]
        assignments = {1: 1, 3: 1, 5: 1, 7: 1, 2: 2, 4: 2, 6: 2, 8: 2}
        friendships = [{"student_id": 1, "requested_friend_id": 2, "priority": 1}]
        pair_constraints = {
            "together": [{"student_id": 2, "other_student_id": 4, "reason": "test"}],
            "separation": [],
        }
        settings = {
            "friendship": True,
            "friendship_first": True,
            "balance_gender": False,
            "balance_grades": False,
            "balance_behavior": False,
            "spread_source_school": False,
            "spread_dominant_students": False,
        }
        score = evaluate_assignment(students, classes, assignments, friendships, [], pair_constraints, settings)
        self.assertEqual(len(score["friendship"]["missing"]), 1)

        engine = AssignmentEngine()
        candidate, candidate_score, timed_out = engine._friendship_swap_improvement(
            students,
            classes,
            assignments,
            score,
            friendships,
            [],
            pair_constraints,
            settings,
            {1: [1], 2: [2, 4], 3: [3], 4: [2, 4], 5: [5], 6: [6], 7: [7], 8: [8]},
            set(),
            None,
        )

        self.assertFalse(timed_out)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate_score["hard_violations"], [])
        self.assertEqual(len(candidate_score["friendship"]["missing"]), 0)
        self.assertGreater(_score_rank(candidate_score), _score_rank(score))

    def test_default_restarts_include_seed_dependent_exploration(self) -> None:
        students = [
            Student(id=index, project_id=1, internal_code=f"S{index:03d}", full_name=f"Student {index}")
            for index in range(1, 9)
        ]
        classes = [ClassGroup(id=1, project_id=1, name="A"), ClassGroup(id=2, project_id=1, name="B")]

        first_modes = self._initial_modes_for_seed(students, classes, 42)
        second_modes = self._initial_modes_for_seed(students, classes, 1039)

        self.assertEqual(len(first_modes), 5)
        self.assertEqual(first_modes[:2], ["priority", "random"])
        self.assertEqual(second_modes[:2], ["priority", "random"])
        self.assertNotEqual(first_modes, second_modes)

    def test_friendship_diagnostic_finds_full_coverage_when_possible(self) -> None:
        project_id = self.project_service.create_project("friend-diag", "7", "2026", 2, "A, B")
        students = self._replace_students(project_id, [80, 81, 82, 83])
        ids = [int(student.id) for student in students]
        self.db.replace_friendships(project_id, [(ids[0], ids[1], 1), (ids[2], ids[3], 1)])

        result = self.assignment_service.friendship_diagnostic(
            project_id,
            {
                "class_size": False,
                "gender": False,
                "class_constraints": False,
                "together": False,
                "separation": False,
                "attempts": 1,
            },
        )

        self.assertTrue(result["legal_full_friend_coverage"])
        self.assertEqual(result["missing_count"], 0)
        self.assertEqual(result["satisfied_percent"], 100)

    def test_friendship_diagnostic_identifies_separation_blocker(self) -> None:
        project_id = self.project_service.create_project("friend-diag-separation", "7", "2026", 1, "A")
        students = self._replace_students(project_id, [80, 81])
        ids = [int(student.id) for student in students]
        self.db.replace_friendships(project_id, [(ids[0], ids[1], 1)])
        self.db.replace_pair_constraints(project_id, together=[], separation=[(ids[0], ids[1], "apart")])

        blocked = self.assignment_service.friendship_diagnostic(
            project_id,
            {
                "class_size": False,
                "gender": False,
                "class_constraints": False,
                "together": False,
                "separation": True,
                "attempts": 1,
            },
        )
        columns = {item["key"] for item in blocked["structural_blockers"]["columns"]}

        self.assertFalse(blocked["legal_full_friend_coverage"])
        self.assertEqual(blocked["verdict"], "proven_blocked_by_selected_rules")
        self.assertEqual(blocked["blocking_stage"]["key"], "separation")
        self.assertEqual(blocked["missing_examples"][0]["student_name"], students[0].display_name)
        self.assertEqual(blocked["structural_blockers"]["requesters_with_no_possible_friend"], 1)
        self.assertIn("separation", columns)

        relaxed = self.assignment_service.friendship_diagnostic(
            project_id,
            {
                "class_size": False,
                "gender": False,
                "class_constraints": False,
                "together": False,
                "separation": False,
                "attempts": 1,
            },
        )
        self.assertTrue(relaxed["legal_full_friend_coverage"])

    def test_friendship_diagnostic_identifies_class_constraint_blocker(self) -> None:
        project_id = self.project_service.create_project("friend-diag-class", "7", "2026", 2, "A, B")
        students = self._replace_students(project_id, [80, 81])
        ids = [int(student.id) for student in students]
        classes = self.db.get_classes(project_id)
        self.db.replace_friendships(project_id, [(ids[0], ids[1], 1)])
        self.db.replace_class_constraints(
            project_id,
            [
                {"student_id": ids[0], "allowed_classes": [int(classes[0].id)], "forbidden_classes": [], "locked_class_id": None},
                {"student_id": ids[1], "allowed_classes": [int(classes[1].id)], "forbidden_classes": [], "locked_class_id": None},
            ],
        )

        blocked = self.assignment_service.friendship_diagnostic(
            project_id,
            {
                "class_size": False,
                "gender": False,
                "class_constraints": True,
                "together": False,
                "separation": False,
                "attempts": 1,
            },
        )
        columns = {item["key"] for item in blocked["structural_blockers"]["columns"]}

        self.assertFalse(blocked["legal_full_friend_coverage"])
        self.assertEqual(blocked["verdict"], "proven_blocked_by_selected_rules")
        self.assertEqual(blocked["blocking_stage"]["key"], "class_constraints")
        self.assertEqual(blocked["missing_examples"][0]["student_name"], students[0].display_name)
        self.assertEqual(blocked["structural_blockers"]["requesters_with_no_possible_friend"], 1)
        self.assertIn("class_constraints", columns)

    def _replace_students(self, project_id: int, grades: list[int]) -> list[Student]:
        return self.db.replace_students(
            project_id,
            [
                Student(
                    id=None,
                    project_id=project_id,
                    internal_code=f"S{index:03d}",
                    full_name=f"Student {index}",
                    gender="M" if index % 2 else "F",
                    average_grade=grade,
                )
                for index, grade in enumerate(grades, start=1)
            ],
        )

    def _bridge(self, project_id: int) -> AppBridge:
        bridge = AppBridge(
            database=self.db,
            project_service=self.project_service,
            import_service=self.import_service,
            assignment_service=self.assignment_service,
            export_service=self.export_service,
            report_service=self.report_service,
        )
        bridge.current_project_id = project_id
        return bridge

    def _initial_modes_for_seed(
        self,
        students: list[Student],
        classes: list[ClassGroup],
        seed: int,
    ) -> list[str]:
        engine = AssignmentEngine()
        modes: list[str] = []
        original = engine._initial_assignment

        def capture_initial_assignment(*args, **kwargs):
            modes.append(str(kwargs.get("ordering_mode", "")))
            return original(*args, **kwargs)

        engine._initial_assignment = capture_initial_assignment  # type: ignore[method-assign]
        engine._best_local_assignment(
            students=students,
            classes=classes,
            friendships=[],
            class_constraints=[],
            pair_constraints={"together": [], "separation": []},
            settings={
                "search_restarts": 5,
                "max_iterations": 0,
                "optimizer_backend": "local",
                "random_seed": seed,
                "friendship": False,
            },
            locked_assignments={},
        )
        return modes


if __name__ == "__main__":
    unittest.main()
