from __future__ import annotations

from collections import Counter, defaultdict
from math import ceil, floor
import random
from statistics import mean
import time
from typing import Any, Callable

from class_balancer.assignment_engine.preflight import PreflightError, preflight_allows_best_effort, run_preflight
from class_balancer.assignment_engine.scoring import evaluate_assignment, score_rank
from class_balancer.models.entities import ClassGroup, Student
from class_balancer.validation.normalization import GENDER_FEMALE, GENDER_MALE, behavior_to_number, normalize_name_key


ProgressCallback = Callable[[float, str], None]


class AssignmentEngine:
    def run(
        self,
        students: list[Student],
        classes: list[ClassGroup],
        friendships: list[dict[str, Any]] | None = None,
        class_constraints: list[dict[str, Any]] | None = None,
        pair_constraints: dict[str, list[dict[str, Any]]] | None = None,
        settings: dict[str, Any] | None = None,
        locked_assignments: dict[int, int] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not students:
            raise ValueError("אין תלמידים לשיבוץ.")
        if not classes:
            raise ValueError("אין כיתות לשיבוץ.")
        settings = settings or {}
        friendships = friendships or []
        class_constraints = class_constraints or []
        pair_constraints = pair_constraints or {"together": [], "separation": []}
        locked_assignments = locked_assignments or {}
        self._last_solver_telemetry: dict[str, Any] = {}
        preflight = run_preflight(
            students=students,
            classes=classes,
            friendships=friendships,
            class_constraints=class_constraints,
            pair_constraints=pair_constraints,
            settings=settings,
            locked_assignments=locked_assignments,
        )
        if not preflight.ok and not preflight_allows_best_effort(preflight):
            raise PreflightError(preflight)
        _report_progress(progress_callback, 2, "בדיקת האילוצים עברה, אפשר להתחיל לחשב שיבוץ.")

        candidates: list[tuple[dict[int, int], dict[str, Any]]] = []
        _report_progress(progress_callback, 8, "בודק אם קיימת דרך חישוב מדויקת לפרויקט הזה.")
        try:
            exact_assignments = self._exact_assignment(
                students,
                classes,
                friendships,
                class_constraints,
                pair_constraints,
                locked_assignments,
                settings,
            )
        except ValueError:
            exact_assignments = None
        if exact_assignments:
            _report_progress(progress_callback, 22, "נמצא פתרון מדויק ראשוני, מנסה לשפר אותו בלי לשבור אילוצים.")
            exact_score = evaluate_assignment(students, classes, exact_assignments, friendships, class_constraints, pair_constraints, settings)
            exact_assignments, exact_score = self._improve(
                students,
                classes,
                exact_assignments,
                exact_score,
                friendships,
                class_constraints,
                pair_constraints,
                settings,
                locked_assignments,
                progress_callback=_progress_range(progress_callback, 22, 42),
            )
            if self._last_solver_telemetry:
                exact_score["solver_telemetry"] = dict(self._last_solver_telemetry)
            candidates.append((exact_assignments, exact_score))
        _report_progress(progress_callback, 44, "מריץ חיפוש מקומי: בונה חלוקות ומשפר אותן בהדרגה.")

        try:
            local_assignments, local_score = self._best_local_assignment(
                students,
                classes,
                friendships,
                class_constraints,
                pair_constraints,
                settings,
                locked_assignments,
                progress_callback=_progress_range(progress_callback, 44, 94),
            )
            candidates.append((local_assignments, local_score))
        except ValueError:
            if not candidates:
                raise

        assignments, best_score = max(candidates, key=lambda item: _score_rank(item[1]))
        _report_progress(progress_callback, 98, "משווה בין המועמדים שנמצאו.")
        best_score["engine_note"] = {
            "assignment_source": "exact_optimizer" if exact_assignments and assignments == exact_assignments else "local_search",
            "exact_optimizer_available": bool(exact_assignments),
            "external_ai_used_for_assignment": False,
        }
        best_score["preflight"] = preflight.to_dict()
        _report_progress(progress_callback, 100, "מנוע השיבוץ סיים את החישוב.")
        return {"assignments": assignments, "score": best_score}

    def _best_local_assignment(
        self,
        students: list[Student],
        classes: list[ClassGroup],
        friendships: list[dict[str, Any]],
        class_constraints: list[dict[str, Any]],
        pair_constraints: dict[str, list[dict[str, Any]]],
        settings: dict[str, Any],
        locked_assignments: dict[int, int],
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[dict[int, int], dict[str, Any]]:
        restart_count = max(1, min(10, int(settings.get("search_restarts", 6) or 1)))
        seed = int(settings.get("random_seed", 42) or 42)
        base_modes = ["grade_high", "grade_low", "behavior", "dominance", "source_school"]
        mode_offset = (seed - 42) % len(base_modes)
        exploration_modes = [*base_modes[mode_offset:], *base_modes[:mode_offset]]
        modes = ["priority", "random", *exploration_modes]
        initials: list[tuple[dict[int, int], dict[str, Any]]] = []
        for index in range(restart_count):
            _report_progress(progress_callback, (index / restart_count) * 36, f"בונה חלוקה התחלתית {index + 1} מתוך {restart_count}.")
            mode = modes[index % len(modes)]
            assignments = self._initial_assignment(
                students,
                classes,
                friendships,
                class_constraints,
                pair_constraints,
                locked_assignments,
                settings,
                ordering_mode=mode,
                seed=seed + index,
            )
            score = evaluate_assignment(students, classes, assignments, friendships, class_constraints, pair_constraints, settings)
            initials.append((assignments, score))
            _report_progress(progress_callback, ((index + 1) / restart_count) * 36, f"חלוקה התחלתית {index + 1} נוקדה ונשמרה להשוואה.")

        initials.sort(key=lambda item: _score_rank(item[1]), reverse=True)
        best_assignments, best_score = initials[0]
        target_score = float(settings.get("stop_when_score_at_least", 92) or 92)
        if _quality_target_met(best_score, settings, target_score):
            _report_progress(progress_callback, 100, "נמצא שיבוץ שכבר עומד ביעד האיכות, אין צורך בשיפור נוסף.")
            return best_assignments, best_score
        polish_count = 1 if len(students) >= 120 else min(2, len(initials))
        for polish_index, (assignments, score) in enumerate(initials[:polish_count]):
            if _quality_target_met(best_score, settings, target_score):
                break
            improved_assignments, improved_score = self._improve(
                students,
                classes,
                assignments,
                score,
                friendships,
                class_constraints,
                pair_constraints,
                settings,
                locked_assignments,
                progress_callback=_progress_range(
                    progress_callback,
                    36 + (polish_index / max(1, polish_count)) * 62,
                    36 + ((polish_index + 1) / max(1, polish_count)) * 62,
                ),
            )
            if _is_better(improved_score, best_score):
                best_assignments, best_score = improved_assignments, improved_score
        _report_progress(progress_callback, 100, "החיפוש המקומי הסתיים.")
        return best_assignments, best_score

    def _exact_assignment(
        self,
        students: list[Student],
        classes: list[ClassGroup],
        friendships: list[dict[str, Any]],
        class_constraints: list[dict[str, Any]],
        pair_constraints: dict[str, list[dict[str, Any]]],
        locked_assignments: dict[int, int],
        settings: dict[str, Any],
    ) -> dict[int, int] | None:
        backend = str(settings.get("optimizer_backend", "auto") or "auto").lower()
        if backend == "local":
            return None
        try:
            from ortools.sat.python import cp_model
        except ImportError:
            return None

        class_ids = [int(group.id) for group in classes if group.id is not None]
        if not class_ids:
            return None
        class_by_id = {int(group.id): group for group in classes if group.id is not None}
        class_name_to_id = {normalize_name_key(group.name): int(group.id) for group in classes if group.id is not None}
        student_by_id = {int(student.id): student for student in students if student.id is not None}
        constraints_by_student = {
            int(item["student_id"]): item for item in class_constraints if item.get("student_id") is not None
        }
        groups = self._together_groups(students, pair_constraints)
        if len(groups) > 260:
            return None
        group_index_by_student = {
            student_id: group_index
            for group_index, group in enumerate(groups)
            for student_id in group
        }
        candidates_by_group: list[list[int]] = []
        for group in groups:
            locked_class = self._locked_class_for_group(group, constraints_by_student, locked_assignments)
            candidates = self._candidate_classes_for_group(group, class_ids, constraints_by_student, class_name_to_id)
            if locked_class and locked_class in class_by_id:
                candidates = [locked_class]
            if not candidates:
                candidates = list(class_ids)
            candidates_by_group.append(candidates)

        model = cp_model.CpModel()
        x: dict[tuple[int, int], Any] = {}
        for group_index, candidates in enumerate(candidates_by_group):
            for class_id in candidates:
                x[(group_index, class_id)] = model.NewBoolVar(f"g{group_index}_c{class_id}")
            model.Add(sum(x[(group_index, class_id)] for class_id in candidates) == 1)

        objective: list[Any] = []
        solver_warnings: list[str] = []
        target_size = max(1, round(len(students) / len(class_ids)))
        ideal_min = floor(len(students) / len(class_ids))
        ideal_max = ceil(len(students) / len(class_ids))
        size_weight = _weight(settings, "class_size_weight", 1.0)
        for class_id in class_ids:
            size_expr = sum(len(groups[group_index]) * x[(group_index, class_id)] for group_index, candidates in enumerate(candidates_by_group) if class_id in candidates)
            group = class_by_id[class_id]
            effective_max = _effective_class_max(group, settings)
            if effective_max:
                model.Add(size_expr <= effective_max)
            if group.min_students:
                model.Add(size_expr >= int(group.min_students))
            if settings.get("balance_class_size", True) and size_weight > 0:
                target = int(group.target_students or target_size)
                allowed_min = min(target, ideal_min) if group.target_students else ideal_min
                allowed_max = max(target, ideal_max) if group.target_students else ideal_max
                guard_min, guard_max = _class_size_guard_bounds(group, allowed_min, allowed_max, settings)
                model.Add(size_expr >= guard_min)
                model.Add(size_expr <= guard_max)
                size_dev = model.NewIntVar(0, len(students), f"size_dev_{class_id}")
                model.AddAbsEquality(size_dev, size_expr - target)
                objective_weight = _objective_weight(45.0, size_weight)
                if objective_weight:
                    objective.append(objective_weight * size_dev)

        def group_sum(group: list[int], attr: str, scale: int = 10) -> int:
            total = 0
            for student_id in group:
                student = student_by_id.get(student_id)
                if not student:
                    continue
                if attr == "grade_value":
                    value = student.grade_value
                elif attr == "behavior_value":
                    value = behavior_to_number(student.behavior_score)
                else:
                    value = getattr(student, attr)
                if value is not None:
                    total += int(round(float(value) * scale))
            return total

        feature_specs = []
        if settings.get("balance_grades", True):
            feature_specs.extend(
                [
                    ("grade_value", "grade", _weight(settings, "grade_weight", 1.0), 10),
                    ("math_grade", "math", _weight(settings, "subject_weight", 0.6), 10),
                    ("english_grade", "english", _weight(settings, "subject_weight", 0.6), 10),
                    ("hebrew_grade", "hebrew", _weight(settings, "subject_weight", 0.6), 10),
                ]
            )
        if settings.get("balance_behavior", True):
            feature_specs.append(("behavior_value", "behavior", _weight(settings, "behavior_weight", 1.0), 10))
        if settings.get("spread_dominant_students", True):
            feature_specs.append(("dominance_score", "dominance", _weight(settings, "dominance_weight", 0.8), 10))

        for attr, label, weight, scale in feature_specs:
            group_values = [group_sum(group, attr, scale) for group in groups]
            total = sum(group_values)
            if total == 0:
                continue
            target = int(round(total / len(class_ids)))
            max_total = max(total, abs(target) * len(class_ids), 1)
            for class_id in class_ids:
                expr = sum(group_values[group_index] * x[(group_index, class_id)] for group_index, candidates in enumerate(candidates_by_group) if class_id in candidates)
                dev = model.NewIntVar(0, max_total, f"{label}_dev_{class_id}")
                model.AddAbsEquality(dev, expr - target)
                objective_weight = _objective_weight(2.5, weight)
                if objective_weight:
                    objective.append(objective_weight * dev)

        if settings.get("balance_gender", True):
            boys_by_group = [
                sum(1 for student_id in group if student_by_id.get(student_id) and student_by_id[student_id].gender == GENDER_MALE)
                for group in groups
            ]
            total_boys = sum(boys_by_group)
            gender_weight = _objective_weight(14.0, _weight(settings, "gender_weight", 1.0))
            if total_boys and gender_weight:
                target_boys = int(round(total_boys / len(class_ids)))
                for class_id in class_ids:
                    expr = sum(boys_by_group[group_index] * x[(group_index, class_id)] for group_index, candidates in enumerate(candidates_by_group) if class_id in candidates)
                    dev = model.NewIntVar(0, total_boys, f"gender_dev_{class_id}")
                    model.AddAbsEquality(dev, expr - target_boys)
                    objective.append(gender_weight * dev)

        gender_cap = _max_students_per_gender(settings)
        if gender_cap:
            boys_by_group = [
                sum(1 for student_id in group if student_by_id.get(student_id) and student_by_id[student_id].gender == GENDER_MALE)
                for group in groups
            ]
            girls_by_group = [
                sum(1 for student_id in group if student_by_id.get(student_id) and student_by_id[student_id].gender == GENDER_FEMALE)
                for group in groups
            ]
            for class_id in class_ids:
                boys_expr = sum(boys_by_group[group_index] * x[(group_index, class_id)] for group_index, candidates in enumerate(candidates_by_group) if class_id in candidates)
                girls_expr = sum(girls_by_group[group_index] * x[(group_index, class_id)] for group_index, candidates in enumerate(candidates_by_group) if class_id in candidates)
                model.Add(boys_expr <= gender_cap)
                model.Add(girls_expr <= gender_cap)

        if settings.get("spread_source_school", True):
            school_counts = Counter(student.source_school for student in students if student.source_school)
            schools_with_multiple_students = [school for school, count in school_counts.most_common() if count > 1]
            tracked_schools = schools_with_multiple_students[:60]
            if len(schools_with_multiple_students) > len(tracked_schools):
                solver_warnings.append(
                    f"CP-SAT איזן רק {len(tracked_schools)} מתוך {len(schools_with_multiple_students)} בתי ספר מקור עם יותר מתלמיד אחד."
                )
            source_weight = _weight(settings, "source_school_weight", 1.1)
            source_base = 34.0 if _source_school_priority_active(settings) else 12.0
            source_objective_weight = _objective_weight(source_base, source_weight)
            if source_objective_weight:
                for school in tracked_schools:
                    by_group = [
                        sum(1 for student_id in group if student_by_id.get(student_id) and student_by_id[student_id].source_school == school)
                        for group in groups
                    ]
                    total = sum(by_group)
                    low_target = total // len(class_ids)
                    high_target = ceil(total / len(class_ids))
                    for class_id in class_ids:
                        expr = sum(by_group[group_index] * x[(group_index, class_id)] for group_index, candidates in enumerate(candidates_by_group) if class_id in candidates)
                        excess = model.NewIntVar(0, total, f"school_excess_{len(objective)}_{class_id}")
                        shortage = model.NewIntVar(0, total, f"school_shortage_{len(objective)}_{class_id}")
                        model.Add(expr - high_target <= excess)
                        model.Add(low_target - expr <= shortage)
                        objective.append(source_objective_weight * excess)
                        if low_target > 0:
                            objective.append(max(1, int(round(source_objective_weight * 0.8))) * shortage)

        separation_pairs = _pair_lookup(pair_constraints.get("separation", []))
        for left, right in separation_pairs:
            left_group = group_index_by_student.get(left)
            right_group = group_index_by_student.get(right)
            if left_group is None or right_group is None or left_group == right_group:
                continue
            common_classes = set(candidates_by_group[left_group]) & set(candidates_by_group[right_group])
            for class_id in common_classes:
                model.Add(x[(left_group, class_id)] + x[(right_group, class_id)] <= 1)

        if settings.get("friendship", True):
            priority_mode = bool(settings.get("friendship_priority_order", False))
            base_reward = 10000 if _friendship_priority_active(settings) else 18
            reward = _objective_weight(float(base_reward), _weight(settings, "friendship_weight", 2.2))
            friendship_required = _friendship_required(settings)
            friendship_terms_by_student: dict[int, list[Any]] = defaultdict(list)
            friendship_already_satisfied: set[int] = set()
            if len(friendships) > 600:
                solver_warnings.append(f"CP-SAT שקל 600 מתוך {len(friendships)} בקשות חברות.")
            for request_index, request in enumerate(friendships[:600]):
                requester_id = int(request["student_id"])
                friend_id = int(request["requested_friend_id"])
                left_group = group_index_by_student.get(requester_id)
                right_group = group_index_by_student.get(friend_id)
                if left_group is None or right_group is None or left_group == right_group:
                    if left_group is not None and left_group == right_group:
                        friendship_already_satisfied.add(requester_id)
                    continue
                priority_weight = _friend_priority_weight(int(request.get("priority", 1) or 1)) if priority_mode else 1.0
                common_classes = set(candidates_by_group[left_group]) & set(candidates_by_group[right_group])
                for class_id in common_classes:
                    together = model.NewBoolVar(f"friend_{request_index}_{left_group}_{right_group}_{class_id}")
                    model.Add(together <= x[(left_group, class_id)])
                    model.Add(together <= x[(right_group, class_id)])
                    model.Add(together >= x[(left_group, class_id)] + x[(right_group, class_id)] - 1)
                    if reward:
                        objective.append(-max(1, int(round(reward * priority_weight))) * together)
                    friendship_terms_by_student[requester_id].append(together)
            if friendship_required:
                requested_student_ids = {
                    int(request["student_id"])
                    for request in friendships[:600]
                    if int(request.get("student_id", 0) or 0) in student_by_id
                }
                for student_id in requested_student_ids:
                    if student_id in friendship_already_satisfied:
                        continue
                    terms = friendship_terms_by_student.get(student_id, [])
                    if terms:
                        model.Add(sum(terms) >= 1)
                    else:
                        model.Add(0 == 1)

        model.Minimize(sum(objective))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(1.0, float(settings.get("optimizer_time_limit_seconds", 8) or 8))
        solver.parameters.random_seed = int(settings.get("random_seed", 42) or 42)
        solver.parameters.num_search_workers = max(1, int(settings.get("optimizer_workers", 1) or 1))
        status = solver.Solve(model)
        self._last_solver_telemetry = {
            "backend": "cp_sat",
            "status": solver.StatusName(status),
            "objective_value": float(solver.ObjectiveValue()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
            "best_bound": float(solver.BestObjectiveBound()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
            "wall_time_seconds": round(float(solver.WallTime()), 4),
            "conflicts": int(solver.NumConflicts()),
            "branches": int(solver.NumBranches()),
            "workers": int(solver.parameters.num_search_workers),
            "random_seed": int(solver.parameters.random_seed),
            "warnings": solver_warnings,
        }
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None

        assignments: dict[int, int] = {}
        for group_index, group in enumerate(groups):
            chosen = None
            for class_id in candidates_by_group[group_index]:
                if solver.BooleanValue(x[(group_index, class_id)]):
                    chosen = class_id
                    break
            if chosen is None:
                return None
            for student_id in group:
                assignments[student_id] = chosen
        return assignments if len(assignments) == len(student_by_id) else None

    def _initial_assignment(
        self,
        students: list[Student],
        classes: list[ClassGroup],
        friendships: list[dict[str, Any]],
        class_constraints: list[dict[str, Any]],
        pair_constraints: dict[str, list[dict[str, Any]]],
        locked_assignments: dict[int, int],
        settings: dict[str, Any],
        ordering_mode: str = "priority",
        seed: int = 42,
    ) -> dict[int, int]:
        class_ids = [int(group.id) for group in classes if group.id is not None]
        class_by_id = {int(group.id): group for group in classes if group.id is not None}
        class_name_to_id = {normalize_name_key(group.name): int(group.id) for group in classes if group.id is not None}
        constraints_by_student = {
            int(item["student_id"]): item for item in class_constraints if item.get("student_id") is not None
        }
        student_by_id = {int(student.id): student for student in students if student.id is not None}
        friendships_by_student = _friendship_lookup(friendships, bool(settings.get("friendship_priority_order", False)))
        class_students: dict[int, list[int]] = {class_id: [] for class_id in class_ids}
        separation_pairs = _pair_lookup(pair_constraints.get("separation", []))
        groups = self._together_groups(students, pair_constraints)
        assignments: dict[int, int] = {}
        class_loads: dict[int, int] = {class_id: 0 for class_id in class_ids}

        for group in groups:
            locked_class = self._locked_class_for_group(group, constraints_by_student, locked_assignments)
            if locked_class and locked_class in class_by_id:
                for student_id in group:
                    assignments[student_id] = locked_class
                    class_students[locked_class].append(student_id)
                class_loads[locked_class] += len(group)

        ordered_groups = sorted(
            [group for group in groups if not any(student_id in assignments for student_id in group)],
            key=lambda group: _group_order_key(
                group,
                ordering_mode,
                seed,
                class_ids,
                constraints_by_student,
                class_name_to_id,
                student_by_id,
            ),
        )
        for group in ordered_groups:
            candidates = self._candidate_classes_for_group(group, class_ids, constraints_by_student, class_name_to_id)
            if not candidates:
                candidates = list(class_ids)
            class_id = self._choose_class(
                group,
                candidates,
                class_loads,
                class_by_id,
                settings,
                assignments,
                class_students,
                student_by_id,
                friendships_by_student,
                separation_pairs,
            )
            for student_id in group:
                assignments[student_id] = class_id
                class_students[class_id].append(student_id)
            class_loads[class_id] += len(group)
        return assignments

    def _improve(
        self,
        students: list[Student],
        classes: list[ClassGroup],
        assignments: dict[int, int],
        best_score: dict[str, Any],
        friendships: list[dict[str, Any]],
        class_constraints: list[dict[str, Any]],
        pair_constraints: dict[str, list[dict[str, Any]]],
        settings: dict[str, Any],
        locked_assignments: dict[int, int],
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[dict[int, int], dict[str, Any]]:
        class_ids = [int(group.id) for group in classes if group.id is not None]
        class_name_to_id = {normalize_name_key(group.name): int(group.id) for group in classes if group.id is not None}
        constraints_by_student = {
            int(item["student_id"]): item for item in class_constraints if item.get("student_id") is not None
        }
        groups = self._together_groups(students, pair_constraints)
        group_by_student = {student_id: group for group in groups for student_id in group}
        locked_students = set(locked_assignments)
        for item in class_constraints:
            if item.get("locked_class_id"):
                locked_students.add(int(item["student_id"]))
        movable_groups = [group for group in groups if not any(student_id in locked_students for student_id in group)]
        requested_iterations = int(settings.get("max_iterations", 650) or 0)
        max_iterations = _adaptive_max_iterations(settings, requested_iterations, len(students))
        time_limit_seconds = _adaptive_improve_time_limit_seconds(settings, len(students))
        deadline = time.monotonic() + time_limit_seconds if time_limit_seconds is not None else None
        move_group_limit = _adaptive_move_group_limit(settings, len(movable_groups), len(students))
        swap_left_limit, swap_right_span = _adaptive_swap_limits(settings, len(movable_groups), len(students))
        target_score = float(settings.get("stop_when_score_at_least", 92) or 92)
        first_improvement_threshold = int(settings.get("first_improvement_threshold", 80) or 80)
        first_improvement = len(students) >= first_improvement_threshold
        friendship_first_active = _friendship_priority_active(settings)

        current = dict(assignments)
        timed_out = False
        iterations_completed = 0
        for iteration in range(max_iterations):
            if _deadline_reached(deadline):
                timed_out = True
                break
            _report_progress(progress_callback, (iteration / max(1, max_iterations)) * 100, f"מנסה שיפור {iteration + 1} מתוך {max_iterations}: העברה או החלפה שמשפרת ציון.")
            if _quality_target_met(best_score, settings, target_score):
                break
            if friendship_first_active:
                friendship_candidate, friendship_candidate_score, timed_out = self._friendship_swap_improvement(
                    students,
                    classes,
                    current,
                    best_score,
                    friendships,
                    class_constraints,
                    pair_constraints,
                    settings,
                    group_by_student,
                    locked_students,
                    deadline,
                )
                if friendship_candidate is not None:
                    current = friendship_candidate
                    best_score = friendship_candidate_score
                    iterations_completed = iteration + 1
                    continue
                if timed_out:
                    break
            class_size_candidate, class_size_candidate_score, timed_out = self._class_size_guard_improvement(
                students,
                classes,
                current,
                best_score,
                friendships,
                class_constraints,
                pair_constraints,
                settings,
                movable_groups,
                constraints_by_student,
                class_name_to_id,
                deadline,
            )
            if class_size_candidate is not None:
                current = class_size_candidate
                best_score = class_size_candidate_score
                iterations_completed = iteration + 1
                continue
            if timed_out:
                break
            if not friendship_first_active:
                friendship_candidate, friendship_candidate_score, timed_out = self._friendship_swap_improvement(
                    students,
                    classes,
                    current,
                    best_score,
                    friendships,
                    class_constraints,
                    pair_constraints,
                    settings,
                    group_by_student,
                    locked_students,
                    deadline,
                )
                if friendship_candidate is not None:
                    current = friendship_candidate
                    best_score = friendship_candidate_score
                    iterations_completed = iteration + 1
                    continue
                if timed_out:
                    break
            best_candidate: dict[int, int] | None = None
            best_candidate_score = best_score
            found_first_improvement = False
            search_groups = _windowed_groups(movable_groups, iteration, move_group_limit)
            for group in search_groups:
                if _deadline_reached(deadline):
                    timed_out = True
                    break
                if found_first_improvement:
                    break
                current_class = current.get(group[0])
                candidates = self._candidate_classes_for_group(group, class_ids, constraints_by_student, class_name_to_id)
                for class_id in candidates:
                    if _deadline_reached(deadline):
                        timed_out = True
                        break
                    if class_id == current_class:
                        continue
                    candidate = dict(current)
                    for student_id in group:
                        candidate[student_id] = class_id
                    if not _respects_local_hard_constraints(candidate, students, classes, pair_constraints, settings):
                        continue
                    score = evaluate_assignment(students, classes, candidate, friendships, class_constraints, pair_constraints, settings)
                    if _is_better(score, best_candidate_score):
                        best_candidate = candidate
                        best_candidate_score = score
                        if first_improvement:
                            found_first_improvement = True
                            break
                if timed_out:
                    break
            if best_candidate is not None:
                current = best_candidate
                best_score = best_candidate_score
                iterations_completed = iteration + 1
                continue
            if timed_out:
                break

            friendship_missing = len(best_score.get("friendship", {}).get("missing", []))
            if friendship_missing == 0 and float(best_score.get("total_score", 0) or 0) >= float(settings.get("swap_search_min_score", 70)):
                break

            improved = False
            swap_groups = _windowed_groups(movable_groups, iteration, swap_left_limit)
            for left_index, left_group in enumerate(swap_groups):
                if _deadline_reached(deadline):
                    timed_out = True
                    break
                if improved:
                    break
                for right_group in swap_groups[left_index + 1 : left_index + 1 + swap_right_span]:
                    if _deadline_reached(deadline):
                        timed_out = True
                        break
                    left_class = current.get(left_group[0])
                    right_class = current.get(right_group[0])
                    if not left_class or not right_class or left_class == right_class:
                        continue
                    candidate = dict(current)
                    for student_id in left_group:
                        candidate[student_id] = right_class
                    for student_id in right_group:
                        candidate[student_id] = left_class
                    if not _respects_local_hard_constraints(candidate, students, classes, pair_constraints, settings):
                        continue
                    score = evaluate_assignment(students, classes, candidate, friendships, class_constraints, pair_constraints, settings)
                    if _is_better(score, best_score):
                        current = candidate
                        best_score = score
                        improved = True
                        break
                if timed_out:
                    break
            if not improved:
                break
            iterations_completed = iteration + 1
        _report_progress(progress_callback, 100, "שלב השיפור הסתיים, לא נמצאה העברה טובה יותר כרגע.")
        best_score["local_search_telemetry"] = {
            "requested_iterations": requested_iterations,
            "effective_iterations": max_iterations,
            "iterations_completed": iterations_completed,
            "time_limit_seconds": time_limit_seconds,
            "stopped_by_time_limit": timed_out,
            "move_group_limit": move_group_limit,
            "swap_left_limit": swap_left_limit,
            "swap_right_span": swap_right_span,
        }
        return current, best_score

    def _class_size_guard_improvement(
        self,
        students: list[Student],
        classes: list[ClassGroup],
        current: dict[int, int],
        best_score: dict[str, Any],
        friendships: list[dict[str, Any]],
        class_constraints: list[dict[str, Any]],
        pair_constraints: dict[str, list[dict[str, Any]]],
        settings: dict[str, Any],
        movable_groups: list[list[int]],
        constraints_by_student: dict[int, dict[str, Any]],
        class_name_to_id: dict[str, int],
        deadline: float | None,
    ) -> tuple[dict[int, int] | None, dict[str, Any], bool]:
        objective = best_score.get("objective", {}) or {}
        try:
            guard_issues = int(objective.get("class_size_guard_issues", 0) or 0)
        except (TypeError, ValueError):
            guard_issues = 0
        if guard_issues <= 0:
            return None, best_score, False

        class_by_id = {int(group.id): group for group in classes if group.id is not None}
        if not class_by_id:
            return None, best_score, False

        class_groups: dict[int, list[list[int]]] = defaultdict(list)
        class_sizes: dict[int, int] = {class_id: 0 for class_id in class_by_id}
        for group in movable_groups:
            class_id = current.get(group[0])
            if class_id in class_by_id:
                class_groups[int(class_id)].append(group)
        for _student_id, class_id in current.items():
            if class_id in class_by_id:
                class_sizes[int(class_id)] += 1

        ideal_min = floor(len(students) / len(class_by_id))
        ideal_max = ceil(len(students) / len(class_by_id))
        target = round(len(students) / len(class_by_id))
        bounds: dict[int, tuple[int, int]] = {}
        for class_id, group in class_by_id.items():
            group_target = group.target_students or target
            allowed_min = min(group_target, ideal_min) if group.target_students else ideal_min
            allowed_max = max(group_target, ideal_max) if group.target_students else ideal_max
            bounds[class_id] = _class_size_guard_bounds(group, allowed_min, allowed_max, settings)

        undersized = [class_id for class_id, size in class_sizes.items() if size < bounds[class_id][0]]
        oversized = [class_id for class_id, size in class_sizes.items() if size > bounds[class_id][1]]
        if not undersized or not oversized:
            return None, best_score, False
        undersized.sort(key=lambda class_id: (class_sizes[class_id] - bounds[class_id][0], class_id))
        oversized.sort(key=lambda class_id: (bounds[class_id][1] - class_sizes[class_id], class_id))

        best_candidate: dict[int, int] | None = None
        best_candidate_score = best_score
        checked = 0
        candidate_limit = 420 if len(students) >= 120 else 900
        for target_class in undersized:
            if _deadline_reached(deadline):
                return best_candidate, best_candidate_score, True
            for source_class in oversized:
                if _deadline_reached(deadline):
                    return best_candidate, best_candidate_score, True
                if source_class == target_class:
                    continue
                for group in class_groups.get(source_class, []):
                    if checked >= candidate_limit:
                        return best_candidate, best_candidate_score, False
                    if _deadline_reached(deadline):
                        return best_candidate, best_candidate_score, True
                    candidates = self._candidate_classes_for_group(
                        group,
                        list(class_by_id),
                        constraints_by_student,
                        class_name_to_id,
                    )
                    if target_class not in candidates:
                        continue
                    checked += 1
                    candidate = dict(current)
                    for student_id in group:
                        candidate[student_id] = target_class
                    if not _respects_local_hard_constraints(candidate, students, classes, pair_constraints, settings):
                        continue
                    score = evaluate_assignment(
                        students,
                        classes,
                        candidate,
                        friendships,
                        class_constraints,
                        pair_constraints,
                        settings,
                    )
                    if _is_better(score, best_candidate_score):
                        best_candidate = candidate
                        best_candidate_score = score
        return best_candidate, best_candidate_score, False

    def _friendship_swap_improvement(
        self,
        students: list[Student],
        classes: list[ClassGroup],
        current: dict[int, int],
        best_score: dict[str, Any],
        friendships: list[dict[str, Any]],
        class_constraints: list[dict[str, Any]],
        pair_constraints: dict[str, list[dict[str, Any]]],
        settings: dict[str, Any],
        group_by_student: dict[int, list[int]],
        locked_students: set[int],
        deadline: float | None,
    ) -> tuple[dict[int, int] | None, dict[str, Any], bool]:
        if not settings.get("friendship", True):
            return None, best_score, False
        missing = list((best_score.get("friendship", {}) or {}).get("missing", []) or [])
        if not missing:
            return None, best_score, False

        class_ids = [int(group.id) for group in classes if group.id is not None]
        class_name_to_id = {normalize_name_key(group.name): int(group.id) for group in classes if group.id is not None}
        constraints_by_student = {
            int(item["student_id"]): item for item in class_constraints if item.get("student_id") is not None
        }

        def movable_group(student_id: int) -> list[int]:
            group = sorted(int(member_id) for member_id in group_by_student.get(student_id, [student_id]))
            if any(member_id in locked_students for member_id in group):
                return []
            class_id = current.get(group[0]) if group else None
            if class_id is None or any(current.get(member_id) != class_id for member_id in group):
                return []
            return group

        candidate_class_cache: dict[tuple[int, ...], set[int]] = {}

        def can_move_group_to(group: list[int], class_id: int) -> bool:
            key = tuple(group)
            candidates = candidate_class_cache.get(key)
            if candidates is None:
                candidates = set(
                    self._candidate_classes_for_group(group, class_ids, constraints_by_student, class_name_to_id)
                )
                candidate_class_cache[key] = candidates
            return class_id in candidates

        def groups_overlap(left: list[int], right: list[int]) -> bool:
            return not set(left).isdisjoint(right)

        class_groups: dict[int, list[list[int]]] = defaultdict(list)
        seen_groups: set[tuple[int, ...]] = set()
        for student_id in sorted(current):
            group = movable_group(int(student_id))
            if not group:
                continue
            key = tuple(group)
            if key in seen_groups:
                continue
            seen_groups.add(key)
            class_id = current.get(group[0])
            if class_id is not None:
                class_groups[int(class_id)].append(group)

        best_candidate: dict[int, int] | None = None
        best_candidate_score = best_score
        checked = 0
        candidate_limit = 720 if _friendship_priority_active(settings) and len(students) >= 120 else 320

        def try_score(candidate: dict[int, int]) -> None:
            nonlocal best_candidate, best_candidate_score, checked
            checked += 1
            if not _respects_local_hard_constraints(candidate, students, classes, pair_constraints, settings):
                return
            score = evaluate_assignment(students, classes, candidate, friendships, class_constraints, pair_constraints, settings)
            if _is_better(score, best_candidate_score):
                best_candidate = candidate
                best_candidate_score = score

        def move_candidate(group: list[int], target_class: int) -> dict[int, int] | None:
            source_class = current.get(group[0]) if group else None
            if source_class is None or source_class == target_class:
                return None
            if not can_move_group_to(group, target_class):
                return None
            candidate = dict(current)
            for member_id in group:
                candidate[member_id] = target_class
            return candidate

        def swap_candidate(left_group: list[int], right_group: list[int]) -> dict[int, int] | None:
            if groups_overlap(left_group, right_group):
                return None
            left_class = current.get(left_group[0]) if left_group else None
            right_class = current.get(right_group[0]) if right_group else None
            if left_class is None or right_class is None or left_class == right_class:
                return None
            if not can_move_group_to(left_group, right_class) or not can_move_group_to(right_group, left_class):
                return None
            candidate = dict(current)
            for member_id in left_group:
                candidate[member_id] = right_class
            for member_id in right_group:
                candidate[member_id] = left_class
            return candidate

        missing_limit = 48 if _friendship_priority_active(settings) else 30
        swap_group_span = 18 if len(students) >= 120 else 32
        for item in missing[:missing_limit]:
            if _deadline_reached(deadline):
                return best_candidate, best_candidate_score, True
            try:
                student_id = int(item.get("student_id"))
            except (TypeError, ValueError):
                continue
            student_group = movable_group(student_id)
            if not student_group:
                continue
            student_class = current.get(student_id)
            if not student_class:
                continue
            slots = sorted(item.get("slots", []) or [], key=lambda slot: int(slot.get("priority", 9) or 9))
            for slot in slots:
                if checked >= candidate_limit:
                    return best_candidate, best_candidate_score, False
                if slot.get("received"):
                    continue
                try:
                    friend_id = int(slot.get("friend_id"))
                except (TypeError, ValueError):
                    continue
                friend_group = movable_group(friend_id)
                if not friend_group:
                    continue
                friend_class = current.get(friend_id)
                if not friend_class or friend_class == student_class:
                    continue

                for candidate in (
                    move_candidate(student_group, int(friend_class)),
                    move_candidate(friend_group, int(student_class)),
                ):
                    if checked >= candidate_limit:
                        return best_candidate, best_candidate_score, False
                    if candidate is not None:
                        try_score(candidate)

                for other_group in class_groups.get(int(friend_class), [])[:swap_group_span]:
                    if checked >= candidate_limit:
                        return best_candidate, best_candidate_score, False
                    candidate = swap_candidate(student_group, other_group)
                    if candidate is None:
                        continue
                    try_score(candidate)
                for other_group in class_groups.get(int(student_class), [])[:swap_group_span]:
                    if checked >= candidate_limit:
                        return best_candidate, best_candidate_score, False
                    candidate = swap_candidate(friend_group, other_group)
                    if candidate is None:
                        continue
                    try_score(candidate)
        return best_candidate, best_candidate_score, False

    def _together_groups(
        self,
        students: list[Student],
        pair_constraints: dict[str, list[dict[str, Any]]],
    ) -> list[list[int]]:
        student_ids = [int(student.id) for student in students if student.id is not None]
        parent = {student_id: student_id for student_id in student_ids}

        def find(value: int) -> int:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left: int, right: int) -> None:
            if left not in parent or right not in parent:
                return
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for item in pair_constraints.get("together", []):
            union(int(item["student_id"]), int(item["other_student_id"]))

        grouped: dict[int, list[int]] = defaultdict(list)
        for student_id in student_ids:
            grouped[find(student_id)].append(student_id)
        return [sorted(group) for group in grouped.values()]

    def _locked_class_for_group(
        self,
        group: list[int],
        constraints_by_student: dict[int, dict[str, Any]],
        locked_assignments: dict[int, int],
    ) -> int | None:
        locks: set[int] = set()
        for student_id in group:
            if student_id in locked_assignments:
                locks.add(int(locked_assignments[student_id]))
            constraint = constraints_by_student.get(student_id, {})
            if constraint.get("locked_class_id"):
                locks.add(int(constraint["locked_class_id"]))
        if len(locks) > 1:
            return min(locks)
        return next(iter(locks), None)

    def _candidate_classes_for_group(
        self,
        group: list[int],
        class_ids: list[int],
        constraints_by_student: dict[int, dict[str, Any]],
        class_name_to_id: dict[str, int],
    ) -> list[int]:
        candidates = set(class_ids)
        for student_id in group:
            constraint = constraints_by_student.get(student_id, {})
            allowed = _resolve_class_refs(constraint.get("allowed_classes", []), class_name_to_id)
            forbidden = _resolve_class_refs(constraint.get("forbidden_classes", []), class_name_to_id)
            if allowed:
                candidates &= allowed
            candidates -= forbidden
        return sorted(candidates)

    def _choose_class(
        self,
        group: list[int],
        candidates: list[int],
        class_loads: dict[int, int],
        class_by_id: dict[int, ClassGroup],
        settings: dict[str, Any],
        assignments: dict[int, int],
        class_students: dict[int, list[int]],
        student_by_id: dict[int, Student],
        friendships_by_student: dict[int, set[int]],
        separation_pairs: set[tuple[int, int]],
    ) -> int:
        gender_cap = _max_students_per_gender(settings)
        viable: list[int] = []
        for class_id in candidates:
            group_class = class_by_id[class_id]
            effective_max = _effective_class_max(group_class, settings)
            if effective_max and class_loads[class_id] + len(group) > effective_max:
                continue
            if gender_cap and _gender_cap_exceeded(
                [*class_students.get(class_id, []), *group],
                student_by_id,
                gender_cap,
            ):
                continue
            if _separation_conflict_count(group, class_students.get(class_id, []), separation_pairs):
                continue
            viable.append(class_id)
        if not viable:
            viable = list(candidates)
        return min(
            viable,
            key=lambda class_id: (
                _candidate_cost(
                    group,
                    class_id,
                    class_loads,
                    class_by_id,
                    settings,
                    assignments,
                    class_students,
                    student_by_id,
                    friendships_by_student,
                    separation_pairs,
                ),
                class_id,
            ),
        )


def _resolve_class_refs(values: list[Any], class_name_to_id: dict[str, int]) -> set[int]:
    resolved: set[int] = set()
    for value in values or []:
        if isinstance(value, int):
            resolved.add(value)
            continue
        text = str(value).strip()
        if text.isdigit():
            resolved.add(int(text))
            continue
        class_id = class_name_to_id.get(normalize_name_key(text))
        if class_id:
            resolved.add(class_id)
    return resolved


def _group_order_key(
    group: list[int],
    mode: str,
    seed: int,
    class_ids: list[int],
    constraints_by_student: dict[int, dict[str, Any]],
    class_name_to_id: dict[str, int],
    student_by_id: dict[int, Student],
) -> tuple[float, ...]:
    candidate_count = len(_candidate_classes_for_group_static(group, class_ids, constraints_by_student, class_name_to_id))
    priority = _group_priority(group, student_by_id, constraints_by_student)
    grades = [student_by_id[student_id].grade_value for student_id in group if student_id in student_by_id and student_by_id[student_id].grade_value is not None]
    avg_grade = mean(grades) if grades else 0.0
    behavior = [
        behavior_to_number(student_by_id[student_id].behavior_score)
        for student_id in group
        if student_id in student_by_id and behavior_to_number(student_by_id[student_id].behavior_score) is not None
    ]
    avg_behavior = mean(behavior) if behavior else 0.0
    dominance = sum(float(student_by_id[student_id].dominance_score or 0) for student_id in group if student_id in student_by_id)
    source_key = min((student_by_id[student_id].source_school or "" for student_id in group if student_id in student_by_id), default="")
    random_value = random.Random(seed + min(group)).random()
    if mode == "grade_high":
        mode_key = -avg_grade
    elif mode == "grade_low":
        mode_key = avg_grade
    elif mode == "behavior":
        mode_key = avg_behavior
    elif mode == "dominance":
        mode_key = -dominance
    elif mode == "source_school":
        mode_key = float(sum(ord(char) for char in source_key))
    elif mode == "random":
        mode_key = random_value
    else:
        mode_key = -priority
    return (candidate_count, -len(group), mode_key, -priority, min(group))


def _candidate_classes_for_group_static(
    group: list[int],
    class_ids: list[int],
    constraints_by_student: dict[int, dict[str, Any]],
    class_name_to_id: dict[str, int],
) -> list[int]:
    candidates = set(class_ids)
    for student_id in group:
        constraint = constraints_by_student.get(student_id, {})
        allowed = _resolve_class_refs(constraint.get("allowed_classes", []), class_name_to_id)
        forbidden = _resolve_class_refs(constraint.get("forbidden_classes", []), class_name_to_id)
        if allowed:
            candidates &= allowed
        candidates -= forbidden
    return sorted(candidates)


def _candidate_cost(
    group: list[int],
    class_id: int,
    class_loads: dict[int, int],
    class_by_id: dict[int, ClassGroup],
    settings: dict[str, Any],
    assignments: dict[int, int],
    class_students: dict[int, list[int]],
    student_by_id: dict[int, Student],
    friendships_by_student: dict[int, set[int]],
    separation_pairs: set[tuple[int, int]],
) -> float:
    current_ids = list(class_students.get(class_id, []))
    candidate_ids = [*current_ids, *group]
    group_students = [student_by_id[student_id] for student_id in group if student_id in student_by_id]
    candidate_students = [student_by_id[student_id] for student_id in candidate_ids if student_id in student_by_id]
    all_students = list(student_by_id.values())
    group_class = class_by_id[class_id]

    target = group_class.target_students or round(len(all_students) / max(1, len(class_by_id)))
    after_size = class_loads.get(class_id, 0) + len(group)
    projected_loads = dict(class_loads)
    projected_loads[class_id] = after_size
    projected_sizes = list(projected_loads.values())
    size_weight = _weight(settings, "class_size_weight", 1.0)
    cost = after_size * 0.4
    if settings.get("balance_class_size", True) and size_weight > 0:
        cost += (max(projected_sizes) - min(projected_sizes)) * 35.0 * size_weight
        cost += abs(after_size - target) * 0.6 * size_weight
        if after_size > target:
            cost += (after_size - target) * 18.0 * size_weight
        cost += _projected_class_size_guard_cost(projected_loads, class_by_id, len(all_students), settings)
    effective_max = _effective_class_max(group_class, settings)
    if effective_max and after_size > effective_max:
        cost += (after_size - effective_max) * 4000.0
    gender_cap = _max_students_per_gender(settings)
    if gender_cap:
        cost += _gender_cap_excess(candidate_ids, student_by_id, gender_cap) * 4000.0
    if group_class.min_students and after_size < group_class.min_students:
        cost += (group_class.min_students - after_size) * 2.0

    for student_id in group:
        for other_id in current_ids:
            if tuple(sorted((student_id, other_id))) in separation_pairs:
                cost += 500.0

    if settings.get("balance_gender", True):
        cost += _gender_cost(candidate_students, all_students) * _weight(settings, "gender_weight", 1.0)
    if settings.get("balance_grades", True):
        cost += _grade_cost(candidate_students, all_students) * _weight(settings, "grade_weight", 1.0)
    if settings.get("balance_behavior", True):
        cost += _behavior_cost(candidate_students, all_students) * _weight(settings, "behavior_weight", 1.0)
    if settings.get("spread_dominant_students", True):
        cost += _dominance_cost(candidate_students, all_students, len(class_by_id)) * _weight(settings, "dominance_weight", 0.8)
    if settings.get("spread_source_school", True):
        cost += _source_school_cost(candidate_students, group_students, all_students, len(class_by_id), settings) * _weight(settings, "source_school_weight", 1.1)
    if settings.get("friendship", True):
        friendship_multiplier = 500.0 if _friendship_priority_active(settings) else 1.0
        cost += (
            _friendship_cost(group, class_id, assignments, friendships_by_student)
            * _weight(settings, "friendship_weight", 2.2)
            * friendship_multiplier
        )
    return cost


def _group_priority(
    group: list[int],
    student_by_id: dict[int, Student],
    constraints_by_student: dict[int, dict[str, Any]],
) -> float:
    priority = 0.0
    for student_id in group:
        student = student_by_id.get(student_id)
        constraint = constraints_by_student.get(student_id, {})
        if constraint.get("allowed_classes") or constraint.get("forbidden_classes") or constraint.get("locked_class_id"):
            priority += 4.0
        if student and student.grade_value is not None:
            priority += abs(float(student.grade_value) - 75.0) / 25.0
        if student and behavior_to_number(student.behavior_score) == 1.0:
            priority += 1.5
        if student and student.dominance_score:
            priority += float(student.dominance_score) / 50.0
    return priority


def _friendship_lookup(friendships: list[dict[str, Any]], priority_mode: bool = False) -> dict[int, dict[int, float]]:
    lookup: dict[int, dict[int, float]] = defaultdict(dict)
    for item in friendships:
        student_id = int(item["student_id"])
        friend_id = int(item["requested_friend_id"])
        weight = _friend_priority_weight(int(item.get("priority", 1) or 1)) if priority_mode else 1.0
        lookup[student_id][friend_id] = lookup[student_id].get(friend_id, 0.0) + weight
        lookup[friend_id][student_id] = lookup[friend_id].get(student_id, 0.0) + weight
    return lookup


def _pair_lookup(rows: list[dict[str, Any]]) -> set[tuple[int, int]]:
    return {tuple(sorted((int(item["student_id"]), int(item["other_student_id"])))) for item in rows}


def _separation_conflict_count(
    group: list[int],
    current_ids: list[int],
    separation_pairs: set[tuple[int, int]],
) -> int:
    if not group or not current_ids or not separation_pairs:
        return 0
    conflicts = 0
    for student_id in group:
        for other_id in current_ids:
            if tuple(sorted((int(student_id), int(other_id)))) in separation_pairs:
                conflicts += 1
    return conflicts


def _respects_local_hard_constraints(
    assignments: dict[int, int],
    students: list[Student],
    classes: list[ClassGroup],
    pair_constraints: dict[str, list[dict[str, Any]]],
    settings: dict[str, Any],
) -> bool:
    class_by_id = {int(group.id): group for group in classes if group.id is not None}
    if not class_by_id:
        return False
    class_students: dict[int, list[Student]] = {class_id: [] for class_id in class_by_id}
    for student in students:
        if student.id is None:
            continue
        class_id = assignments.get(int(student.id))
        if class_id not in class_by_id:
            return False
        class_students[int(class_id)].append(student)

    for class_id, group in class_by_id.items():
        effective_max = _effective_class_max(group, settings)
        if effective_max and len(class_students.get(class_id, [])) > effective_max:
            return False

    gender_cap = _max_students_per_gender(settings)
    if gender_cap:
        for students_in_class in class_students.values():
            boys = sum(1 for student in students_in_class if student.gender == GENDER_MALE)
            girls = sum(1 for student in students_in_class if student.gender == GENDER_FEMALE)
            if boys > gender_cap or girls > gender_cap:
                return False

    for item in pair_constraints.get("separation", []):
        left = int(item["student_id"])
        right = int(item["other_student_id"])
        left_class = assignments.get(left)
        if left_class is not None and left_class == assignments.get(right):
            return False
    return True


def _gender_cost(candidate_students: list[Student], all_students: list[Student]) -> float:
    global_known = [student.gender for student in all_students if student.gender in {"בן", "בת"}]
    local_known = [student.gender for student in candidate_students if student.gender in {"בן", "בת"}]
    if not global_known or not local_known:
        return 0.0
    global_boys_ratio = sum(1 for gender in global_known if gender == "בן") / len(global_known)
    local_boys_ratio = sum(1 for gender in local_known if gender == "בן") / len(local_known)
    return abs(local_boys_ratio - global_boys_ratio) * 14.0


def _grade_cost(candidate_students: list[Student], all_students: list[Student]) -> float:
    local = _mean_or_none([student.grade_value for student in candidate_students])
    global_value = _mean_or_none([student.grade_value for student in all_students])
    if local is None or global_value is None:
        return 0.0
    return abs(local - global_value) * 0.25


def _behavior_cost(candidate_students: list[Student], all_students: list[Student]) -> float:
    local = _mean_or_none([behavior_to_number(student.behavior_score) for student in candidate_students])
    global_value = _mean_or_none([behavior_to_number(student.behavior_score) for student in all_students])
    if local is None or global_value is None:
        return 0.0
    return abs(local - global_value) * 8.0


def _dominance_cost(candidate_students: list[Student], all_students: list[Student], class_count: int) -> float:
    local_total = sum(float(student.dominance_score or 0) for student in candidate_students)
    global_total = sum(float(student.dominance_score or 0) for student in all_students)
    if not global_total or not class_count:
        return 0.0
    return abs(local_total - (global_total / class_count)) * 0.5


def _source_school_cost(
    candidate_students: list[Student],
    group_students: list[Student],
    all_students: list[Student],
    class_count: int,
    settings: dict[str, Any],
) -> float:
    if not class_count:
        return 0.0
    local_counts: dict[str, int] = defaultdict(int)
    global_counts: dict[str, int] = defaultdict(int)
    for student in candidate_students:
        if student.source_school:
            local_counts[student.source_school] += 1
    for student in all_students:
        if student.source_school:
            global_counts[student.source_school] += 1

    priority_mode = _source_school_priority_active(settings)
    cost = 0.0
    for school, local_count in local_counts.items():
        total = global_counts[school]
        expected = total / class_count
        low_target = floor(expected)
        high_target = ceil(expected)
        if not priority_mode:
            if local_count > high_target:
                excess = local_count - high_target
                cost += (excess * excess * 5.0) + (local_count - expected) * 2.0
            elif local_count > expected:
                cost += (local_count - expected) * 0.9
            continue
        if local_count > high_target:
            excess = local_count - high_target
            cost += (excess * excess * 10.0) + (local_count - expected) * 5.0
        elif local_count > expected:
            cost += (local_count - expected) * 2.5
        if low_target > 0 and local_count < low_target:
            cost += (low_target - local_count) * 1.2
    if settings.get("avoid_social_isolation", True):
        isolation_reward = 1.8 if priority_mode else 0.35
        for student in group_students:
            school = student.source_school
            if school and global_counts[school] > 1 and local_counts[school] >= 2:
                cost -= isolation_reward
    return cost


def _source_school_priority_active(settings: dict[str, Any]) -> bool:
    return bool(
        settings.get("spread_source_school", True)
        and (
            settings.get("source_school_first", False)
            or _weight(settings, "source_school_weight", 1.1) >= 2.0
        )
    )


def _friendship_required(settings: dict[str, Any]) -> bool:
    return bool(
        settings.get("friendship", True)
        and (
            settings.get("friendship_required", False)
            or settings.get("friendship_hard", False)
            or settings.get("hard_friendship", False)
        )
    )


def _friendship_priority_active(settings: dict[str, Any]) -> bool:
    return bool(settings.get("friendship", True) and settings.get("friendship_first", False))


def _friendship_cost(
    group: list[int],
    class_id: int,
    assignments: dict[int, int],
    friendships_by_student: dict[int, dict[int, float]],
) -> float:
    cost = 0.0
    group_set = set(group)
    for student_id in group:
        friends = friendships_by_student.get(student_id, {})
        if not friends:
            continue
        received_weight = sum(
            weight
            for friend_id, weight in friends.items()
            if friend_id in group_set or assignments.get(friend_id) == class_id
        )
        assigned_weight = sum(weight for friend_id, weight in friends.items() if friend_id in assignments)
        if received_weight:
            cost -= 10.0 * received_weight
        elif assigned_weight:
            cost += 7.5 * assigned_weight
    return cost


def _friend_priority_weight(priority: int) -> float:
    if priority <= 1:
        return 3.0
    if priority == 2:
        return 1.8
    return 1.0


def _mean_or_none(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return mean(clean) if clean else None


def _is_better(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    return _score_rank(candidate) > _score_rank(current)


def _score_rank(score: dict[str, Any]) -> tuple[int, int, int, int, int, float, float, float]:
    return score_rank(score)


def _quality_target_met(score: dict[str, Any], settings: dict[str, Any], target_score: float) -> bool:
    if score.get("hard_violations"):
        return False
    if float(score.get("total_score", 0) or 0) < target_score:
        return False
    if _friendship_priority_active(settings):
        objective = score.get("objective", {}) or {}
        friendship = score.get("friendship", {}) or {}
        try:
            missing = int(
                objective.get(
                    "students_without_any_requested_friend",
                    len(friendship.get("missing", []) or []),
                )
                or 0
            )
        except (TypeError, ValueError):
            missing = len(friendship.get("missing", []) or [])
        return missing == 0
    return True


def _slow_large_search_allowed(settings: dict[str, Any]) -> bool:
    return bool(settings.get("allow_slow_large_search", False))


def _adaptive_max_iterations(settings: dict[str, Any], requested: int, student_count: int) -> int:
    requested = max(0, int(requested))
    if _slow_large_search_allowed(settings):
        return requested
    if student_count >= 150:
        return min(requested, 36)
    if student_count >= 120:
        return min(requested, 18)
    if student_count >= 80:
        return min(requested, 36)
    return requested


def _adaptive_improve_time_limit_seconds(settings: dict[str, Any], student_count: int) -> float | None:
    explicit = settings.get("local_search_time_limit_seconds")
    if explicit is not None:
        try:
            return max(5.0, float(explicit))
        except (TypeError, ValueError):
            return None
    if _slow_large_search_allowed(settings) or student_count < 80:
        return None
    if student_count >= 150:
        return 60.0
    if student_count >= 120:
        return 70.0
    return 90.0


def _adaptive_move_group_limit(settings: dict[str, Any], group_count: int, student_count: int) -> int:
    if group_count <= 0:
        return 0
    if _slow_large_search_allowed(settings) or student_count < 80:
        return group_count
    if student_count >= 150:
        return min(group_count, 48)
    if student_count >= 120:
        return min(group_count, 64)
    return min(group_count, 90)


def _adaptive_swap_limits(settings: dict[str, Any], group_count: int, student_count: int) -> tuple[int, int]:
    if group_count <= 0:
        return 0, 0
    if _slow_large_search_allowed(settings) or student_count < 80:
        return min(group_count, 80), 120
    if student_count >= 150:
        return min(group_count, 30), 36
    if student_count >= 120:
        return min(group_count, 42), 48
    return min(group_count, 60), 72


def _windowed_groups(groups: list[list[int]], iteration: int, limit: int) -> list[list[int]]:
    if limit <= 0 or limit >= len(groups):
        return groups
    start = (max(0, iteration) * limit) % len(groups)
    end = start + limit
    if end <= len(groups):
        return groups[start:end]
    return [*groups[start:], *groups[: end - len(groups)]]


def _deadline_reached(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _report_progress(progress_callback: ProgressCallback | None, percent: float, message: str) -> None:
    if progress_callback:
        progress_callback(max(0.0, min(100.0, float(percent))), message)


def _progress_range(
    progress_callback: ProgressCallback | None,
    start: float,
    end: float,
) -> ProgressCallback | None:
    if progress_callback is None:
        return None
    span = float(end) - float(start)

    def report(percent: float, message: str) -> None:
        progress_callback(float(start) + (max(0.0, min(100.0, float(percent))) / 100.0) * span, message)

    return report


def _weight(settings: dict[str, Any], key: str, default: float) -> float:
    try:
        return max(0.0, float(settings.get(key, default)))
    except (TypeError, ValueError):
        return default


def _class_size_guard_bounds(
    group: ClassGroup,
    allowed_min: int,
    allowed_max: int,
    settings: dict[str, Any],
) -> tuple[int, int]:
    if not settings.get("balance_class_size", True) or _weight(settings, "class_size_weight", 1.0) <= 0:
        return 0, 1_000_000
    min_bound = int(group.min_students) if group.min_students else int(max(0, allowed_min))
    effective_max = _effective_class_max(group, settings)
    max_bound = min(effective_max, int(allowed_max)) if effective_max else int(allowed_max)
    if max_bound < min_bound:
        max_bound = min_bound
    return min_bound, max_bound


def _projected_class_size_guard_cost(
    projected_loads: dict[int, int],
    class_by_id: dict[int, ClassGroup],
    student_count: int,
    settings: dict[str, Any],
) -> float:
    if not settings.get("balance_class_size", True) or _weight(settings, "class_size_weight", 1.0) <= 0:
        return 0.0
    if not class_by_id:
        return 0.0
    ideal_min = floor(student_count / len(class_by_id))
    ideal_max = ceil(student_count / len(class_by_id))
    target = round(student_count / len(class_by_id))
    issues = 0
    for class_id, group in class_by_id.items():
        group_target = group.target_students or target
        allowed_min = min(group_target, ideal_min) if group.target_students else ideal_min
        allowed_max = max(group_target, ideal_max) if group.target_students else ideal_max
        guard_min, guard_max = _class_size_guard_bounds(group, allowed_min, allowed_max, settings)
        size = int(projected_loads.get(class_id, 0) or 0)
        if size < guard_min:
            issues += guard_min - size
        elif size > guard_max:
            issues += size - guard_max
    return float(issues) * 5000.0 * max(0.2, _weight(settings, "class_size_weight", 1.0))


def _objective_weight(base: float, weight: float) -> int:
    weighted = int(round(base * weight))
    return weighted if weighted > 0 else 0


def _effective_class_max(group: ClassGroup, settings: dict[str, Any]) -> int:
    class_max = int(group.max_students or 0)
    global_max = _settings_int(settings, "max_students_per_class", 0)
    values = [value for value in (class_max, global_max) if value > 0]
    return min(values) if values else 0


def _max_students_per_gender(settings: dict[str, Any]) -> int:
    return _settings_int(settings, "max_students_per_gender", 0)


def _settings_int(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return max(0, int(settings.get(key, default) or 0))
    except (TypeError, ValueError):
        return max(0, int(default))


def _gender_cap_exceeded(student_ids: list[int], student_by_id: dict[int, Student], cap: int) -> bool:
    return _gender_cap_excess(student_ids, student_by_id, cap) > 0


def _gender_cap_excess(student_ids: list[int], student_by_id: dict[int, Student], cap: int) -> int:
    if cap <= 0:
        return 0
    boys = 0
    girls = 0
    for student_id in student_ids:
        student = student_by_id.get(student_id)
        if not student:
            continue
        if student.gender == GENDER_MALE:
            boys += 1
        elif student.gender == GENDER_FEMALE:
            girls += 1
    return max(0, boys - cap) + max(0, girls - cap)
