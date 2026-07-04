from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

from class_balancer.assignment_engine import AssignmentEngine, evaluate_assignment
from class_balancer.assignment_engine.preflight import (
    FeasibilityIssue,
    FeasibilityReport,
    PreflightError,
    preflight_allows_best_effort,
    run_preflight,
)
from class_balancer.assignment_engine.scoring import score_rank
from class_balancer.db import Database
from class_balancer.models.entities import ClassGroup
from class_balancer.validation.normalization import GENDER_FEMALE, GENDER_MALE, normalize_name_key


ProgressCallback = Callable[[float, str], None]


class AssignmentService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.engine = AssignmentEngine()
        self.undo_stack: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.redo_stack: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def run_assignment(
        self,
        project_id: int,
        name: str = "שיבוץ אוטומטי",
        variant_count: int = 1,
        settings_override: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        _report_progress(progress_callback, 1, "שלב 1 מתוך 5: קורא תלמידים, כיתות, חברים ואילוצים.")
        project, students, classes, friendships, class_constraints, pair_constraints = self._load_context(project_id)
        locked_assignments, locked_students, changed_students = self._active_flags(project_id)
        constraint_locked_students = {
            int(item["student_id"])
            for item in class_constraints
            if item.get("student_id") is not None and item.get("locked_class_id")
        }
        base_settings = {**(project.settings if project else {}), **(settings_override or {})}
        if name == "שיבוץ אוטומטי":
            name = self._next_run_name(project_id)
        _report_progress(progress_callback, 4, "שלב 2 מתוך 5: בודק שאין אילוצים בלתי אפשריים לפני ההרצה.")
        validation_report = self._critical_validation_report(project_id)
        if validation_report is not None:
            raise PreflightError(validation_report)
        preflight = run_preflight(
            students=students,
            classes=classes,
            friendships=friendships,
            class_constraints=class_constraints,
            pair_constraints=pair_constraints,
            settings=base_settings,
            locked_assignments=locked_assignments,
        )
        if not preflight.ok and not preflight_allows_best_effort(preflight):
            raise PreflightError(preflight)
        _report_progress(progress_callback, 7, "שלב 3 מתוך 5: מתחיל לבנות כמה סידורי כיתות אפשריים.")
        result = self._best_assignment_variant(
            students,
            classes,
            friendships,
            class_constraints,
            pair_constraints,
            base_settings,
            locked_assignments,
            variant_count,
            progress_callback=_progress_range(progress_callback, 7, 94),
        )
        _report_progress(progress_callback, 96, "שלב 5 מתוך 5: שומר את השיבוץ הטוב ביותר שנמצא.")
        ranked_candidates = list(result.pop("_ranked_candidates", []) or [result])
        for candidate in ranked_candidates:
            candidate.setdefault("score", {}).setdefault("preflight", preflight.to_dict())
        candidates_to_save = _select_candidates_to_save(base_settings, ranked_candidates)
        save_count = len(candidates_to_save)
        overall_ranks = {_assignment_key(candidate): rank for rank, candidate in enumerate(ranked_candidates, start=1)}
        for rank, candidate in reversed(list(enumerate(candidates_to_save, start=1))):
            score = dict(candidate["score"])
            candidate_note = dict(score.get("candidate_note", {}) or {})
            candidate_note["saved_variant_rank"] = rank
            candidate_note["saved_variant_count"] = save_count
            candidate_note["overall_variant_rank"] = overall_ranks.get(_assignment_key(candidate), rank)
            candidate_note["saved_variant_kind"] = (
                "best_effort_with_hard_violations"
                if _has_hard_violations(score)
                else "hard_violation_free"
            )
            candidate_note["selected"] = rank == 1
            score["candidate_note"] = candidate_note
            self._save_version(
                project_id,
                name if rank == 1 else f"{name} #{rank}",
                candidate["assignments"],
                score,
                locked_students=locked_students | constraint_locked_students,
                changed_students=changed_students,
            )
        _report_progress(progress_callback, 100, "הסתיים: השיבוץ נשמר והתוצאות מתרעננות.")
        return result

    def preflight_report(self, project_id: int) -> dict[str, Any]:
        project, students, classes, friendships, class_constraints, pair_constraints = self._load_context(project_id)
        locked_assignments, _, _ = self._active_flags(project_id)
        report = run_preflight(
            students=students,
            classes=classes,
            friendships=friendships,
            class_constraints=class_constraints,
            pair_constraints=pair_constraints,
            settings=project.settings if project else {},
            locked_assignments=locked_assignments,
        )
        validation_report = self._critical_validation_report(project_id)
        if validation_report is not None:
            report.issues = [*validation_report.issues, *report.issues]
            report.ok = False
        return report.to_dict()

    def friendship_diagnostic(
        self,
        project_id: int,
        options: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        project, students, classes, friendships, class_constraints, pair_constraints = self._load_context(project_id)
        locked_assignments, _, _ = self._active_flags(project_id)
        normalized_options = _friendship_diagnostic_options(options)
        _report_progress(progress_callback, 2, "מכין בדיקת חברים לפי האילוצים שסומנו.")

        diagnostic_settings = _friendship_diagnostic_settings(project.settings if project else {}, normalized_options)
        diagnostic_classes = _classes_for_friendship_diagnostic(classes, normalized_options)
        diagnostic_class_constraints = class_constraints if normalized_options["class_constraints"] else []
        diagnostic_locked_assignments = locked_assignments if normalized_options["class_constraints"] else {}
        diagnostic_pair_constraints = {
            "together": pair_constraints.get("together", []) if normalized_options["together"] else [],
            "separation": pair_constraints.get("separation", []) if normalized_options["separation"] else [],
        }
        structural = _friendship_structural_diagnostic(
            students,
            diagnostic_classes,
            friendships,
            diagnostic_class_constraints,
            diagnostic_pair_constraints,
            diagnostic_locked_assignments,
            diagnostic_settings,
            normalized_options,
        )
        stage_reports = _friendship_diagnostic_stage_reports(
            students,
            classes,
            friendships,
            class_constraints,
            pair_constraints,
            locked_assignments,
            project.settings if project else {},
            normalized_options,
        )
        proven_stage = _final_proven_stage(stage_reports)
        if proven_stage is not None:
            _report_progress(progress_callback, 100, "בדיקת החברים הסתיימה.")
            return _friendship_response_from_stage(proven_stage, normalized_options, stage_reports)
        _report_progress(progress_callback, 10, "בודק אם יש תלמידים שאין להם אף חבר אפשרי לפי הסימונים.")

        attempts = max(1, min(12, int(normalized_options.get("attempts", 6) or 6)))
        base_seed = int(diagnostic_settings.get("random_seed", 42) or 42)
        best_result: dict[str, Any] | None = None
        failures: list[str] = []
        for index in range(attempts):
            start = 10 + (index / attempts) * 84
            end = 10 + ((index + 1) / attempts) * 84
            _report_progress(progress_callback, start, f"מריץ ניסיון בדיקת חברים {index + 1} מתוך {attempts}.")
            attempt_settings = dict(diagnostic_settings)
            attempt_settings["random_seed"] = base_seed + (index * 997)
            try:
                result = self.engine.run(
                    students=students,
                    classes=diagnostic_classes,
                    friendships=friendships,
                    class_constraints=diagnostic_class_constraints,
                    pair_constraints=diagnostic_pair_constraints,
                    settings=attempt_settings,
                    locked_assignments=diagnostic_locked_assignments,
                    progress_callback=_progress_range(progress_callback, start, end),
                )
            except ValueError as exc:
                failures.append(str(exc))
                result = _fallback_assignment_result(
                    students,
                    diagnostic_classes,
                    friendships,
                    diagnostic_class_constraints,
                    diagnostic_pair_constraints,
                    attempt_settings,
                    diagnostic_locked_assignments,
                    index,
                    str(exc),
                )
            result["score"]["candidate_note"] = {
                "diagnostic_attempt": index + 1,
                "diagnostic_attempts": attempts,
                "random_seed": attempt_settings["random_seed"],
            }
            if best_result is None or _friendship_diagnostic_rank(result.get("score", {})) > _friendship_diagnostic_rank(best_result.get("score", {})):
                best_result = result

        if best_result is None:
            raise ValueError("בדיקת החברים לא הצליחה ליצור אף שיבוץ לבדיקה.")

        score = best_result.get("score", {}) or {}
        friendship = score.get("friendship", {}) or {}
        missing = list(friendship.get("missing", []) or [])
        hard_violations = list(score.get("hard_violations", []) or [])
        total_with_requests = int(friendship.get("total_with_requests", 0) or 0)
        satisfied_count = len(friendship.get("satisfied", []) or [])
        full_coverage = total_with_requests == 0 or not missing
        legal_full_coverage = full_coverage and not hard_violations
        structural_blocked_count = int(structural.get("requesters_with_no_possible_friend", 0) or 0)
        global_blocked_count = len(structural.get("global_blockers", []) or [])
        if legal_full_coverage:
            verdict = "found_legal_100"
            summary = "נמצא שיבוץ חוקי שבו כל תלמיד עם בקשת חברים קיבל לפחות חבר אחד לפי האילוצים שסומנו."
        elif full_coverage:
            verdict = "found_100_with_selected_rule_breaks"
            summary = f"נמצא 100% חברים, אבל עדיין נשברו {len(hard_violations)} אילוצים שסומנו לבדיקה."
        elif structural_blocked_count or global_blocked_count:
            verdict = "provably_blocked_by_selected_rules"
            if structural_blocked_count:
                summary = f"לפי האילוצים שסומנו יש {structural_blocked_count} תלמידים שאין להם אף חבר אפשרי."
            else:
                summary = "לפי האילוצים שסומנו קיימת חסימת קיבולת/מגדר כללית שמונעת שיבוץ חוקי."
        else:
            verdict = "not_found_by_search"
            summary = "לא נמצאה הוכחה ישירה שאחד האילוצים שסומנו לבדו מונע 100% חברים. מוצגת התוצאה הטובה ביותר שנמצאה לבדיקה."

        _report_progress(progress_callback, 100, "בדיקת החברים הסתיימה.")
        return {
            "status": "done",
            "verdict": verdict,
            "full_friend_coverage": full_coverage,
            "legal_full_friend_coverage": legal_full_coverage,
            "satisfied_percent": _percent_int(satisfied_count, total_with_requests),
            "missing_count": len(missing),
            "satisfied_count": satisfied_count,
            "total_with_requests": total_with_requests,
            "hard_violation_count": len(hard_violations),
            "hard_violations_preview": hard_violations[:8],
            "missing_examples": _friendship_missing_examples(missing),
            "structural_blockers": structural,
            "stages": stage_reports,
            "blocking_stage": _first_blocking_stage(stage_reports),
            "options": normalized_options,
            "attempts": attempts,
            "failures": failures[:3],
            "score": {
                "total_score": score.get("total_score", 0),
                "summary": score.get("summary", ""),
                "engine_note": score.get("engine_note", {}),
            },
            "summary": summary,
            "proof": _diagnostic_proof_label(stage_reports),
        }

    def _best_assignment_variant(
        self,
        students: list[Any],
        classes: list[Any],
        friendships: list[dict[str, Any]],
        class_constraints: list[dict[str, Any]],
        pair_constraints: dict[str, list[dict[str, Any]]],
        settings: dict[str, Any],
        locked_assignments: dict[int, int],
        variant_count: int,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        requested_count = max(1, min(24, int(variant_count or 1)))
        if settings.get("save_top_variants") is None:
            requested_count = max(2, requested_count)
        count = _effective_variant_count(requested_count, len(students), settings)
        base_seed = int(settings.get("random_seed", 42) or 42)
        candidates: list[dict[str, Any]] = []
        for index in range(count):
            variant_start = (index / count) * 100
            variant_end = ((index + 1) / count) * 100
            variant_progress = _progress_range(progress_callback, variant_start, variant_end)
            _report_progress(variant_progress, 0, f"שלב 4 מתוך 5: בודק סידור {index + 1} מתוך {count}.")
            variant_settings = dict(settings)
            variant_settings["random_seed"] = base_seed + (index * 997)
            if _use_quick_regular_variant(settings, len(students), index):
                variant_settings = _quick_regular_variant_settings(variant_settings)
            try:
                result = self.engine.run(
                    students=students,
                    classes=classes,
                    friendships=friendships,
                    class_constraints=class_constraints,
                    pair_constraints=pair_constraints,
                    settings=variant_settings,
                    locked_assignments=locked_assignments,
                    progress_callback=_progress_range(variant_progress, 0, 82),
                )
            except ValueError as exc:
                result = _fallback_assignment_result(
                    students,
                    classes,
                    friendships,
                    class_constraints,
                    pair_constraints,
                    variant_settings,
                    locked_assignments,
                    index,
                    str(exc),
                )
            result = self._advisor_rerun_if_helpful(
                result,
                students,
                classes,
                friendships,
                class_constraints,
                pair_constraints,
                variant_settings,
                locked_assignments,
                progress_callback=_progress_range(variant_progress, 82, 98),
            )
            result["score"]["candidate_note"] = {
                "variant_index": index + 1,
                "variants_checked": count,
                "variants_requested": requested_count,
                "variants_capped_by_runtime_guard": count < requested_count,
                "random_seed": variant_settings["random_seed"],
            }
            candidates.append(result)
            _report_progress(variant_progress, 100, f"סידור {index + 1} מתוך {count} נבדק ונוסף להשוואה.")
        _report_progress(progress_callback, 99, "משווה בין כל הסידורים ובוחר את הציון הטוב ביותר.")
        ranked_candidates = _ranked_unique_candidates(candidates)
        best = ranked_candidates[0]
        best["score"]["candidate_note"]["selected"] = True
        best["_ranked_candidates"] = ranked_candidates
        return best

    def _advisor_rerun_if_helpful(
        self,
        result: dict[str, Any],
        students: list[Any],
        classes: list[Any],
        friendships: list[dict[str, Any]],
        class_constraints: list[dict[str, Any]],
        pair_constraints: dict[str, list[dict[str, Any]]],
        settings: dict[str, Any],
        locked_assignments: dict[int, int],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not settings.get("ai_assisted_assignment", True):
            return result
        score = result.get("score", {})
        if score.get("hard_violations"):
            return result
        target_score = float(settings.get("stop_when_score_at_least", 92) or 92)
        current_score = float(score.get("total_score", 0) or 0)
        if current_score >= target_score:
            return result
        if _skip_advisor_for_runtime(len(students), settings):
            result["score"]["advisor_note"] = {
                "used": False,
                "skipped": "large_dataset_runtime_guard",
            }
            return result
        advisor_settings = _advisor_settings(settings, score)
        if advisor_settings == settings:
            return result
        try:
            rerun = self.engine.run(
                students=students,
                classes=classes,
                friendships=friendships,
                class_constraints=class_constraints,
                pair_constraints=pair_constraints,
                settings=advisor_settings,
                locked_assignments=locked_assignments,
                progress_callback=progress_callback,
            )
        except ValueError as exc:
            result["score"]["advisor_note"] = {
                "used": True,
                "kept_original": True,
                "rerun_failed": str(exc),
                "focus": _largest_penalty_key(score),
            }
            return result
        if _score_rank(rerun.get("score", {})) > _score_rank(score):
            rerun["score"]["advisor_note"] = {
                "used": True,
                "previous_score": current_score,
                "focus": _largest_penalty_key(score),
            }
            return rerun
        result["score"]["advisor_note"] = {
            "used": True,
            "previous_score": current_score,
            "kept_original": True,
            "focus": _largest_penalty_key(score),
        }
        return result

    def dashboard(self, project_id: int) -> dict[str, Any]:
        active = self.database.get_active_assignment_version(project_id)
        if not active:
            return {"has_assignment": False, "message": "עדיין לא הורץ שיבוץ."}
        raw_rows = self.database.get_active_assignment_rows(project_id)
        students = self.database.get_students(project_id)
        if len(raw_rows) != len(students) or not students:
            return {
                "has_assignment": False,
                "message": "גרסת השיבוץ הפעילה אינה מלאה. יש להריץ שיבוץ מחדש.",
                "version": active,
                "score": active.get("score", {}),
                "rows": [],
                "versions": self.database.get_assignment_versions(project_id),
            }
        rows = self._enrich_rows(project_id, raw_rows, active.get("score", {}))
        return {
            "has_assignment": True,
            "version": active,
            "score": active.get("score", {}),
            "rows": rows,
            "versions": self.database.get_assignment_versions(project_id),
        }

    def _enrich_rows(self, project_id: int, rows: list[dict[str, Any]], score: dict[str, Any]) -> list[dict[str, Any]]:
        students = {int(student.id): student for student in self.database.get_students(project_id) if student.id is not None}
        assignments = {int(row["student_id"]): int(row["class_id"]) for row in rows}
        friendships = self.database.get_friendships(project_id)
        requested: dict[int, list[tuple[int, int]]] = defaultdict(list)
        requested_by: dict[int, list[int]] = defaultdict(list)
        for item in friendships:
            student_id = int(item["student_id"])
            friend_id = int(item["requested_friend_id"])
            requested[student_id].append((friend_id, int(item.get("priority", 1) or 1)))
            requested_by[friend_id].append(student_id)

        class_constraints = {
            int(item["student_id"]): item
            for item in self.database.get_class_constraints(project_id)
            if item.get("student_id") is not None
        }
        pairs = self.database.get_pair_constraints(project_id)
        together: dict[int, list[int]] = defaultdict(list)
        separation: dict[int, list[int]] = defaultdict(list)
        for item in pairs.get("together", []):
            left = int(item["student_id"])
            right = int(item["other_student_id"])
            together[left].append(right)
            together[right].append(left)
        for item in pairs.get("separation", []):
            left = int(item["student_id"])
            right = int(item["other_student_id"])
            separation[left].append(right)
            separation[right].append(left)

        reasons = score.get("student_reasons", {})
        enriched: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            student_id = int(item["student_id"])
            class_id = int(item["class_id"])
            requested_items = sorted(requested.get(student_id, []), key=lambda item: item[1])
            requested_ids = [friend_id for friend_id, _priority in requested_items]
            received_ids = [friend_id for friend_id in requested_ids if assignments.get(friend_id) == class_id]
            friend_slots = _friend_slots(requested_items, received_ids, students)
            constraint = class_constraints.get(student_id, {})
            constraint_parts: list[str] = []
            if constraint.get("allowed_classes"):
                constraint_parts.append("מותרות: " + ", ".join(str(value) for value in constraint.get("allowed_classes", [])))
            if constraint.get("forbidden_classes"):
                constraint_parts.append("אסורות: " + ", ".join(str(value) for value in constraint.get("forbidden_classes", [])))
            if together.get(student_id):
                constraint_parts.append("יחד עם: " + _student_names(together[student_id], students))
            if separation.get(student_id):
                constraint_parts.append("בנפרד מ: " + _student_names(separation[student_id], students))

            row_reasons = reasons.get(str(student_id), [])
            item.update(
                {
                    "requested_friends": _student_names(requested_ids, students),
                    "requested_by": _student_names(requested_by.get(student_id, []), students),
                    "friends_received": len(received_ids),
                    "friend_slots": friend_slots,
                    "got_friend": bool(received_ids) if requested_ids else True,
                    "got_friends": _student_names(received_ids, students),
                    "notes_summary": _notes_summary(item),
                    "constraints_summary": " | ".join(constraint_parts),
                    "reason_summary": " ".join(str(reason) for reason in row_reasons[:2]),
                }
            )
            enriched.append(item)
        return enriched

    def move_student(self, project_id: int, student_id: int, class_id: int, lock: bool = False) -> dict[str, Any]:
        context = self._assignment_context(project_id)
        assignments = dict(context["assignments"])
        before = context["score"]
        if student_id not in assignments:
            raise ValueError("התלמיד/ה לא נמצא/ת בשיבוץ הפעיל.")
        if student_id in context["locked_students"] and int(assignments[student_id]) != int(class_id):
            raise ValueError("התלמיד/ה נעול/ה לשיבוץ הנוכחי. יש לשחרר נעילה לפני העברה ידנית.")
        assignments[student_id] = class_id
        locked = set(context["locked_students"])
        changed = set(context["changed_students"])
        if lock:
            locked.add(student_id)
        changed.add(student_id)
        score = self._score(project_id, assignments)
        note = _delta_note(before, score)
        self._update_active_version(
            project_id,
            context,
            assignments,
            score,
            notes=note,
            locked_students=locked,
            changed_students=changed,
        )
        return {"before": before, "after": score, "note": note}

    def swap_students(self, project_id: int, left_student_id: int, right_student_id: int) -> dict[str, Any]:
        context = self._assignment_context(project_id)
        assignments = dict(context["assignments"])
        before = context["score"]
        left_class = assignments.get(left_student_id)
        right_class = assignments.get(right_student_id)
        if not left_class or not right_class:
            raise ValueError("אחד התלמידים לא נמצא בשיבוץ הפעיל.")
        if left_student_id in context["locked_students"] or right_student_id in context["locked_students"]:
            raise ValueError("אי אפשר לבצע החלפה עם תלמיד/ה נעול/ה. יש לשחרר נעילה לפני החלפה.")
        assignments[left_student_id] = right_class
        assignments[right_student_id] = left_class
        locked = set(context["locked_students"])
        changed = set(context["changed_students"]) | {left_student_id, right_student_id}
        score = self._score(project_id, assignments)
        note = _delta_note(before, score)
        self._update_active_version(
            project_id,
            context,
            assignments,
            score,
            notes=note,
            locked_students=locked,
            changed_students=changed,
        )
        return {"before": before, "after": score, "note": note}

    def set_lock(self, project_id: int, student_id: int, locked: bool) -> dict[str, Any]:
        context = self._assignment_context(project_id)
        locked_students = set(context["locked_students"])
        if locked:
            locked_students.add(student_id)
        else:
            locked_students.discard(student_id)
        self._update_active_version(
            project_id,
            context,
            context["assignments"],
            context["score"],
            notes="עדכון נעילות ידני.",
            locked_students=locked_students,
            changed_students=set(context["changed_students"]),
        )
        return {"locked": locked, "student_id": student_id}

    def smart_suggestions(self, project_id: int, student_id: int, limit: int = 5) -> list[dict[str, Any]]:
        context = self._assignment_context(project_id)
        suggestions = self._suggestions_for_student(project_id, context, student_id, limit=max(limit, 6))
        suggestions.sort(key=_action_rank, reverse=True)
        return suggestions[:limit]

    def action_suggestions(
        self,
        project_id: int,
        focus_student_ids: list[int] | None = None,
        limit: int = 12,
        exhaustive: bool = False,
    ) -> list[dict[str, Any]]:
        context = self._assignment_context(project_id)
        focus_ids = _focus_student_ids(context["score"], context["assignments"], focus_student_ids)
        if exhaustive:
            seen = set(focus_ids)
            for student_id in context["assignments"]:
                if student_id not in seen:
                    focus_ids.append(int(student_id))
                    seen.add(int(student_id))
        suggestions: list[dict[str, Any]] = []
        scan_limit = min(len(focus_ids), max(8, min(48, int(limit or 12)))) if exhaustive else 8
        per_student_limit = 6 if exhaustive else 8
        for student_id in focus_ids[:scan_limit]:
            if student_id in context["locked_students"]:
                continue
            suggestions.extend(self._suggestions_for_student(project_id, context, student_id, limit=per_student_limit))

        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in suggestions:
            key = (
                item.get("action_type"),
                item.get("student_id"),
                item.get("other_student_id"),
                item.get("target_class_id"),
            )
            previous = unique.get(key)
            if previous is None or _action_rank(item) > _action_rank(previous):
                unique[key] = item

        ranked = list(unique.values())
        safe_improvements = [
            item
            for item in ranked
            if int(item.get("hard_after", 0) or 0) <= int(item.get("hard_before", 0) or 0)
            and (
                float(item.get("delta", 0) or 0) > 0.05
                or int(item.get("hard_after", 0) or 0) < int(item.get("hard_before", 0) or 0)
                or int(item.get("friendship_missing_after", 0) or 0)
                < int(item.get("friendship_missing_before", 0) or 0)
            )
        ]
        ranked = safe_improvements or [
            item for item in ranked if int(item.get("hard_after", 0) or 0) <= int(item.get("hard_before", 0) or 0)
        ] or ranked
        ranked.sort(key=_action_rank, reverse=True)
        return ranked[: max(1, int(limit or 12))]

    def _suggestions_for_student(
        self,
        project_id: int,
        context: dict[str, Any],
        student_id: int,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        assignments = dict(context["assignments"])
        current_score = context["score"]
        current_class = assignments.get(student_id)
        if not current_class:
            return []
        classes = self.database.get_classes(project_id)
        students = self.database.get_students(project_id)
        class_by_id = {int(group.id): group for group in classes if group.id is not None}
        student_by_id = {int(student.id): student for student in students if student.id is not None}
        student = student_by_id.get(student_id)
        student_name = student.display_name if student else str(student_id)
        current_group = class_by_id.get(int(current_class))
        current_class_name = current_group.name if current_group else str(current_class)
        suggestions: list[dict[str, Any]] = []
        for group in classes:
            if group.id and group.id != current_class:
                candidate = dict(assignments)
                candidate[student_id] = int(group.id)
                score = self._score(project_id, candidate)
                suggestions.append(
                    _suggestion(
                        f"העברת {student_name} אל {group.name}",
                        current_score,
                        score,
                        action_type="move",
                        student_id=student_id,
                        student_name=student_name,
                        from_class_id=int(current_class),
                        from_class_name=current_class_name,
                        target_class_id=int(group.id),
                        target_class_name=group.name,
                    )
                )
        swap_candidates = [
            (other_id, other_class)
            for other_id, other_class in assignments.items()
            if other_id != student_id and other_class != current_class and other_id not in context["locked_students"]
        ][:80]
        for other_id, other_class in swap_candidates:
            if other_id == student_id or other_class == current_class:
                continue
            candidate = dict(assignments)
            candidate[student_id] = other_class
            candidate[other_id] = current_class
            score = self._score(project_id, candidate)
            other = student_by_id.get(other_id)
            other_name = other.display_name if other else str(other_id)
            other_group = class_by_id.get(int(other_class))
            other_class_name = other_group.name if other_group else str(other_class)
            suggestions.append(
                _suggestion(
                    f"החלפה בין {student_name} לבין {other_name}",
                    current_score,
                    score,
                    action_type="swap",
                    student_id=student_id,
                    student_name=student_name,
                    other_student_id=int(other_id),
                    other_student_name=other_name,
                    from_class_id=int(current_class),
                    from_class_name=current_class_name,
                    target_class_id=int(other_class),
                    target_class_name=other_class_name,
                    other_from_class_id=int(other_class),
                    other_from_class_name=other_class_name,
                    other_target_class_id=int(current_class),
                    other_target_class_name=current_class_name,
                )
            )
        suggestions.sort(key=_action_rank, reverse=True)
        return suggestions[:limit]

    def undo(self, project_id: int) -> bool:
        if not self.undo_stack[project_id]:
            return False
        current = self._assignment_context(project_id)
        previous = self.undo_stack[project_id].pop()
        self.redo_stack[project_id].append(_snapshot(current))
        self._restore_snapshot(project_id, previous)
        return True

    def redo(self, project_id: int) -> bool:
        if not self.redo_stack[project_id]:
            return False
        current = self._assignment_context(project_id)
        next_state = self.redo_stack[project_id].pop()
        self.undo_stack[project_id].append(_snapshot(current))
        self._restore_snapshot(project_id, next_state)
        return True

    def clear_history(self, project_id: int) -> None:
        self.undo_stack[project_id].clear()
        self.redo_stack[project_id].clear()

    def _load_context(self, project_id: int):
        project = self.database.get_project(project_id)
        students = self.database.get_students(project_id)
        classes = self.database.get_classes(project_id)
        friendships = self.database.get_friendships(project_id)
        class_constraints = self.database.get_class_constraints(project_id)
        pair_constraints = self.database.get_pair_constraints(project_id)
        return project, students, classes, friendships, class_constraints, pair_constraints

    def _assignment_context(self, project_id: int) -> dict[str, Any]:
        active = self.database.get_active_assignment_version(project_id)
        if not active:
            raise ValueError("עדיין אין שיבוץ פעיל.")
        rows = self.database.get_assignments(int(active["id"]))
        students = self.database.get_students(project_id)
        if len(rows) != len(students) or not students:
            raise ValueError("גרסת השיבוץ הפעילה אינה מלאה. יש להריץ שיבוץ מחדש.")
        return {
            "active": active,
            "assignments": {int(row["student_id"]): int(row["class_id"]) for row in rows},
            "locked_students": {int(row["student_id"]) for row in rows if row.get("locked_manually")},
            "changed_students": {int(row["student_id"]) for row in rows if row.get("changed_manually")},
            "score": active.get("score", {}),
        }

    def _active_flags(self, project_id: int) -> tuple[dict[int, int], set[int], set[int]]:
        try:
            context = self._assignment_context(project_id)
        except ValueError:
            return {}, set(), set()
        locked_assignments = {
            student_id: context["assignments"][student_id]
            for student_id in context["locked_students"]
            if student_id in context["assignments"]
        }
        return locked_assignments, set(context["locked_students"]), set(context["changed_students"])

    def _critical_validation_report(self, project_id: int) -> FeasibilityReport | None:
        critical_issues = [
            issue
            for issue in self.database.get_validation_issues(project_id)
            if issue.severity == "critical"
        ]
        if not critical_issues:
            return None
        return FeasibilityReport(
            ok=False,
            issues=[
                FeasibilityIssue(
                    code="IMPORT_VALIDATION_CRITICAL",
                    severity="critical",
                    message_he=issue.message,
                    student_ids=[int(issue.student_id)] if issue.student_id else [],
                    actions=[{"type": "open_validation"}],
                    details={"field_name": issue.field_name, "validation_issue_id": issue.id},
                )
                for issue in critical_issues
            ],
            metadata={"validation_critical_count": len(critical_issues)},
        )

    def _score(self, project_id: int, assignments: dict[int, int]) -> dict[str, Any]:
        project, students, classes, friendships, class_constraints, pair_constraints = self._load_context(project_id)
        return evaluate_assignment(
            students=students,
            classes=classes,
            assignments=assignments,
            friendships=friendships,
            class_constraints=class_constraints,
            pair_constraints=pair_constraints,
            settings=project.settings if project else {},
        )

    def _save_version(
        self,
        project_id: int,
        name: str,
        assignments: dict[int, int],
        score: dict[str, Any],
        notes: str = "",
        locked_students: set[int] | None = None,
        changed_students: set[int] | None = None,
    ) -> int:
        version_id = self.database.save_assignment_version(
            project_id=project_id,
            name=name,
            assignments=assignments,
            score=score,
            notes=notes,
            locked_student_ids=locked_students or set(),
            changed_student_ids=changed_students or set(),
        )
        self.clear_history(project_id)
        return version_id

    def _update_active_version(
        self,
        project_id: int,
        context: dict[str, Any],
        assignments: dict[int, int],
        score: dict[str, Any],
        notes: str = "",
        locked_students: set[int] | None = None,
        changed_students: set[int] | None = None,
        push_undo: bool = True,
    ) -> None:
        if push_undo:
            self.undo_stack[project_id].append(_snapshot(context))
            self.redo_stack[project_id].clear()
        self.database.update_assignment_version(
            project_id=project_id,
            version_id=int(context["active"]["id"]),
            assignments=assignments,
            score=score,
            notes=notes,
            locked_student_ids=locked_students or set(),
            changed_student_ids=changed_students or set(),
        )

    def _restore_snapshot(self, project_id: int, snapshot: dict[str, Any]) -> None:
        active = self.database.get_active_assignment_version(project_id)
        if not active or int(active["id"]) != int(snapshot["version_id"]):
            self.database.set_active_assignment_version(project_id, int(snapshot["version_id"]))
        self.database.update_assignment_version(
            project_id=project_id,
            version_id=int(snapshot["version_id"]),
            assignments={int(key): int(value) for key, value in snapshot["assignments"].items()},
            score=dict(snapshot["score"]),
            notes=str(snapshot.get("notes", "")),
            locked_student_ids=set(int(value) for value in snapshot.get("locked_students", set())),
            changed_student_ids=set(int(value) for value in snapshot.get("changed_students", set())),
        )

    def _next_run_name(self, project_id: int) -> str:
        run_count = 0
        for item in self.database.get_assignment_versions(project_id):
            score = item.get("score", {}) or {}
            candidate_note = score.get("candidate_note", {}) or {}
            try:
                saved_variant_rank = int(candidate_note.get("saved_variant_rank", 1) or 1)
            except (AttributeError, TypeError, ValueError):
                saved_variant_rank = 1
            if saved_variant_rank != 1:
                continue
            if candidate_note or str(item.get("name", "")).startswith("הרצה "):
                run_count += 1
        return f"הרצה {run_count + 1}"


def _friendship_diagnostic_options(options: dict[str, Any] | None) -> dict[str, Any]:
    raw = options or {}
    return {
        "class_size": _option_bool(raw, "class_size", "include_class_size", default=True),
        "gender": _option_bool(raw, "gender", "include_gender", default=True),
        "class_constraints": _option_bool(raw, "class_constraints", "include_class_constraints", default=True),
        "together": _option_bool(raw, "together", "must_be_with", "include_together", default=True),
        "separation": _option_bool(raw, "separation", "must_not_be_with", "include_separation", default=True),
        "attempts": max(1, min(12, _option_int(raw, "attempts", default=6))),
    }


def _option_bool(raw: dict[str, Any], *keys: str, default: bool = True) -> bool:
    for key in keys:
        if key in raw:
            return bool(raw.get(key))
    return default


def _option_int(raw: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(raw.get(key, default) or default)
    except (TypeError, ValueError):
        return int(default)


def _friendship_diagnostic_settings(base_settings: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    settings = dict(base_settings or {})
    settings.update(
        {
            "friendship": True,
            "friendship_first": True,
            "friendship_required": True,
            "friendship_weight": max(8.0, float(settings.get("friendship_weight", 2.2) or 2.2)),
            "balance_grades": False,
            "balance_behavior": False,
            "spread_dominant_students": False,
            "spread_source_school": False,
            "avoid_social_isolation": False,
            "ai_assisted_assignment": False,
            "ai_auto_review": False,
            "allow_slow_large_search": True,
            "search_restarts": max(8, min(10, int(settings.get("search_restarts", 6) or 6))),
            "max_iterations": max(650, int(settings.get("max_iterations", 220) or 220)),
            "local_search_time_limit_seconds": max(25.0, float(settings.get("local_search_time_limit_seconds", 25) or 25)),
            "optimizer_time_limit_seconds": max(12.0, float(settings.get("optimizer_time_limit_seconds", 8) or 8)),
            "stop_when_score_at_least": 99.9,
            "swap_search_min_score": 0,
        }
    )
    if not options["class_size"]:
        settings["balance_class_size"] = False
        settings["class_size_weight"] = 0
        settings["hard_class_capacity"] = False
        settings["max_students_per_class"] = 0
    if not options["gender"]:
        settings["balance_gender"] = False
        settings["gender_weight"] = 0
        settings["max_students_per_gender"] = 0
    return settings


def _classes_for_friendship_diagnostic(classes: list[Any], options: dict[str, Any]) -> list[Any]:
    if options["class_size"]:
        return classes
    relaxed: list[Any] = []
    for group in classes:
        relaxed.append(
            ClassGroup(
                id=group.id,
                project_id=group.project_id,
                name=group.name,
                min_students=0,
                max_students=0,
                target_students=0,
                created_at=group.created_at,
                updated_at=group.updated_at,
            )
        )
    return relaxed


def _friendship_diagnostic_rank(score: dict[str, Any]) -> tuple[int, int, float]:
    friendship = score.get("friendship", {}) or {}
    missing = len(friendship.get("missing", []) or [])
    hard = len(score.get("hard_violations", []) or [])
    return (-missing, -hard, float(score.get("total_score", 0) or 0))


def _friendship_diagnostic_stage_reports(
    students: list[Any],
    classes: list[Any],
    friendships: list[dict[str, Any]],
    class_constraints: list[dict[str, Any]],
    pair_constraints: dict[str, list[dict[str, Any]]],
    locked_assignments: dict[int, int],
    base_settings: dict[str, Any],
    selected_options: dict[str, Any],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for stage in _friendship_diagnostic_stages(selected_options):
        options = dict(stage["options"])
        settings = _friendship_diagnostic_settings(base_settings, options)
        diagnostic_classes = _classes_for_friendship_diagnostic(classes, options)
        diagnostic_class_constraints = class_constraints if options["class_constraints"] else []
        diagnostic_locked_assignments = locked_assignments if options["class_constraints"] else {}
        diagnostic_pair_constraints = {
            "together": pair_constraints.get("together", []) if options["together"] else [],
            "separation": pair_constraints.get("separation", []) if options["separation"] else [],
        }
        structural = _friendship_structural_diagnostic(
            students,
            diagnostic_classes,
            friendships,
            diagnostic_class_constraints,
            diagnostic_pair_constraints,
            diagnostic_locked_assignments,
            settings,
            options,
        )
        report = {
            "key": stage["key"],
            "label": stage["label"],
            "added_constraint": stage.get("added_constraint", ""),
            "options": options,
            "proof_status": "not_proven",
            "solver_status": "",
            "exact_solver_available": False,
            "full_friend_coverage": False,
            "legal_full_friend_coverage": False,
            "satisfied_percent": 0,
            "missing_count": 0,
            "total_with_requests": _requesters_with_friend_count(friendships, students),
            "missing_students": [],
            "structural_blockers": structural,
            "summary": "",
        }
        if structural.get("requesters_with_no_possible_friend") or structural.get("global_blockers"):
            missing_students = _structural_missing_students(structural)
            report.update(
                {
                    "proof_status": "proven_blocked_direct",
                    "missing_count": max(len(missing_students), int(structural.get("requesters_with_no_possible_friend", 0) or 0)),
                    "missing_students": missing_students,
                    "summary": f"הוכחה ישירה: {stage['label']} משאיר תלמידים בלי אף חבר אפשרי.",
                }
            )
            reports.append(report)
            continue

        exact = _exact_friendship_min_missing(
            students,
            diagnostic_classes,
            friendships,
            diagnostic_class_constraints,
            diagnostic_pair_constraints,
            settings,
            diagnostic_locked_assignments,
        )
        report["exact_solver_available"] = bool(exact.get("available"))
        report["solver_status"] = str(exact.get("status", ""))
        if exact.get("available"):
            report.update(
                {
                    "missing_count": int(exact.get("missing_count", 0) or 0),
                    "missing_students": exact.get("missing_students", []),
                    "satisfied_percent": int(exact.get("satisfied_percent", 0) or 0),
                    "full_friend_coverage": bool(exact.get("missing_count", 0) == 0),
                    "legal_full_friend_coverage": bool(exact.get("missing_count", 0) == 0 and exact.get("hard_feasible", True)),
                }
            )
            if exact.get("hard_feasible") is False:
                report["proof_status"] = "hard_rules_infeasible"
                report["summary"] = f"הוכח שהאילוצים בשלב {stage['label']} לא מאפשרים שיבוץ חוקי."
            elif exact.get("proven_optimal"):
                if int(exact.get("missing_count", 0) or 0) == 0:
                    report["proof_status"] = "proven_feasible"
                    report["summary"] = f"הוכח שבשלב {stage['label']} אפשר להגיע ל-100% חברים."
                else:
                    report["proof_status"] = "proven_blocked_exact"
                    report["summary"] = f"הוכח שבשלב {stage['label']} חייבים להישאר לפחות {exact.get('missing_count')} תלמידים בלי חבר."
            elif int(exact.get("missing_count", 0) or 0) == 0:
                report["proof_status"] = "proven_feasible"
                report["summary"] = f"נמצא שיבוץ עם 100% חברים בשלב {stage['label']}."
            else:
                report["proof_status"] = "not_proven"
                report["summary"] = f"לא נמצאה הוכחה שהשלב {stage['label']} לבדו חוסם 100% חברים."
        else:
            report["proof_status"] = "exact_solver_unavailable"
            report["summary"] = "מנוע ההוכחה המדויק OR-Tools אינו זמין, לכן נבדקה רק חסימה ישירה."
        reports.append(report)
    return reports


def _friendship_diagnostic_stages(selected_options: dict[str, Any]) -> list[dict[str, Any]]:
    base = {
        "class_size": False,
        "gender": False,
        "class_constraints": False,
        "together": False,
        "separation": False,
        "attempts": selected_options.get("attempts", 6),
    }
    stages = [
        {
            "key": "friends_only",
            "label": "חברים בלבד",
            "added_constraint": "",
            "options": dict(base),
        }
    ]
    current = dict(base)
    for key, label in (
        ("class_constraints", "כיתות/נעילות"),
        ("together", "חייב להיות עם"),
        ("separation", "לא לשבץ עם"),
        ("class_size", "גודל כיתות"),
        ("gender", "מגדר"),
    ):
        if not selected_options.get(key):
            continue
        current[key] = True
        stages.append(
            {
                "key": key,
                "label": f"אחרי {label}",
                "added_constraint": label,
                "options": dict(current),
            }
        )
    return stages


def _exact_friendship_min_missing(
    students: list[Any],
    classes: list[Any],
    friendships: list[dict[str, Any]],
    class_constraints: list[dict[str, Any]],
    pair_constraints: dict[str, list[dict[str, Any]]],
    settings: dict[str, Any],
    locked_assignments: dict[int, int],
) -> dict[str, Any]:
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return {"available": False, "status": "missing_ortools"}

    student_by_id = {int(student.id): student for student in students if student.id is not None}
    class_ids = [int(group.id) for group in classes if group.id is not None]
    if not student_by_id or not class_ids:
        return {"available": True, "status": "INFEASIBLE", "hard_feasible": False, "missing_count": 0}

    domains = _student_class_domains(students, classes, class_constraints, locked_assignments)
    parent = {student_id: student_id for student_id in student_by_id}
    for item in pair_constraints.get("together", []):
        left = _safe_int(item.get("student_id"))
        right = _safe_int(item.get("other_student_id"))
        if left in parent and right in parent:
            _union(parent, left, right)

    component_members: dict[int, list[int]] = defaultdict(list)
    for student_id in student_by_id:
        component_members[_find(parent, student_id)].append(student_id)
    component_ids = sorted(component_members)
    component_by_student = {
        student_id: component_id
        for component_id, members in component_members.items()
        for student_id in members
    }
    class_by_id = {int(group.id): group for group in classes if group.id is not None}
    component_domains: dict[int, set[int]] = {}
    for component_id, members in component_members.items():
        domain = set(class_ids)
        for student_id in members:
            domain &= set(domains.get(student_id, set(class_ids)))
        domain = _apply_component_capacity_domain(domain, members, student_by_id, classes, settings, {
            "class_size": settings.get("max_students_per_class", 0) or any(getattr(group, "max_students", 0) for group in classes),
            "gender": bool(_diagnostic_gender_cap(settings)),
        }, set())
        if not domain:
            return {
                "available": True,
                "status": "INFEASIBLE",
                "hard_feasible": False,
                "missing_count": len(_requesters_with_friends(friendships, student_by_id)),
                "missing_students": _student_summary_rows(_requesters_with_friends(friendships, student_by_id), student_by_id),
            }
        component_domains[component_id] = domain

    model = cp_model.CpModel()
    x: dict[tuple[int, int], Any] = {}
    for component_id in component_ids:
        candidates = sorted(component_domains[component_id])
        for class_id in candidates:
            x[(component_id, class_id)] = model.NewBoolVar(f"c{component_id}_k{class_id}")
        model.Add(sum(x[(component_id, class_id)] for class_id in candidates) == 1)

    if settings.get("max_students_per_class", 0) or any(getattr(group, "max_students", 0) for group in classes):
        for class_id in class_ids:
            terms = [
                len(component_members[component_id]) * x[(component_id, class_id)]
                for component_id in component_ids
                if (component_id, class_id) in x
            ]
            class_max = _diagnostic_effective_class_max(class_by_id[class_id], settings)
            if class_max and terms:
                model.Add(sum(terms) <= class_max)

    gender_cap = _diagnostic_gender_cap(settings)
    if gender_cap:
        for class_id in class_ids:
            boys_terms = []
            girls_terms = []
            for component_id in component_ids:
                if (component_id, class_id) not in x:
                    continue
                members = component_members[component_id]
                boys = sum(1 for student_id in members if _student_gender(student_by_id.get(student_id)) == GENDER_MALE)
                girls = sum(1 for student_id in members if _student_gender(student_by_id.get(student_id)) == GENDER_FEMALE)
                if boys:
                    boys_terms.append(boys * x[(component_id, class_id)])
                if girls:
                    girls_terms.append(girls * x[(component_id, class_id)])
            if boys_terms:
                model.Add(sum(boys_terms) <= gender_cap)
            if girls_terms:
                model.Add(sum(girls_terms) <= gender_cap)

    for item in pair_constraints.get("separation", []):
        left = _safe_int(item.get("student_id"))
        right = _safe_int(item.get("other_student_id"))
        left_component = component_by_student.get(left or 0)
        right_component = component_by_student.get(right or 0)
        if left_component is None or right_component is None or left_component == right_component:
            continue
        for class_id in component_domains[left_component] & component_domains[right_component]:
            model.Add(x[(left_component, class_id)] + x[(right_component, class_id)] <= 1)

    requesters = _requesters_with_friends(friendships, student_by_id)
    requests_by_student: dict[int, list[int]] = defaultdict(list)
    for item in friendships:
        student_id = _safe_int(item.get("student_id"))
        friend_id = _safe_int(item.get("requested_friend_id"))
        if student_id in student_by_id and friend_id in student_by_id and student_id != friend_id:
            requests_by_student[int(student_id)].append(int(friend_id))

    missing_vars: dict[int, Any] = {}
    same_var_index = 0
    for student_id in sorted(requesters):
        source_component = component_by_student.get(student_id)
        if source_component is None:
            continue
        same_terms = []
        already_covered = False
        for friend_id in requests_by_student.get(student_id, []):
            friend_component = component_by_student.get(friend_id)
            if friend_component is None:
                continue
            if source_component == friend_component:
                already_covered = True
                break
            for class_id in component_domains[source_component] & component_domains[friend_component]:
                same = model.NewBoolVar(f"same_{same_var_index}")
                same_var_index += 1
                model.Add(same <= x[(source_component, class_id)])
                model.Add(same <= x[(friend_component, class_id)])
                model.Add(same >= x[(source_component, class_id)] + x[(friend_component, class_id)] - 1)
                same_terms.append(same)
        if already_covered:
            continue
        missing = model.NewBoolVar(f"missing_{student_id}")
        missing_vars[student_id] = missing
        if same_terms:
            covered = model.NewBoolVar(f"covered_{student_id}")
            model.AddMaxEquality(covered, same_terms)
            model.Add(missing + covered == 1)
        else:
            model.Add(missing == 1)

    model.Minimize(sum(missing_vars.values()) if missing_vars else 0)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(5.0, float(settings.get("friendship_proof_time_limit_seconds", 35) or 35))
    solver.parameters.random_seed = int(settings.get("random_seed", 42) or 42)
    solver.parameters.num_search_workers = max(1, int(settings.get("optimizer_workers", 1) or 1))
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    if status == cp_model.INFEASIBLE:
        return {"available": True, "status": status_name, "hard_feasible": False, "missing_count": len(requesters)}
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"available": True, "status": status_name, "proven_optimal": False, "missing_count": 0}

    missing_ids = [
        student_id
        for student_id, var in missing_vars.items()
        if solver.BooleanValue(var)
    ]
    assignment: dict[int, int] = {}
    for component_id, members in component_members.items():
        chosen = next(
            (class_id for class_id in component_domains[component_id] if solver.BooleanValue(x[(component_id, class_id)])),
            None,
        )
        if chosen is None:
            continue
        for student_id in members:
            assignment[int(student_id)] = int(chosen)
    score = evaluate_assignment(students, classes, assignment, friendships, class_constraints, pair_constraints, settings)
    missing = list((score.get("friendship", {}) or {}).get("missing", []) or [])
    missing_ids = [int(item.get("student_id", 0) or 0) for item in missing] or missing_ids
    return {
        "available": True,
        "status": status_name,
        "hard_feasible": True,
        "proven_optimal": status == cp_model.OPTIMAL,
        "missing_count": len(missing_ids),
        "missing_students": _friendship_missing_examples(missing) if missing else _student_summary_rows(missing_ids, student_by_id),
        "satisfied_percent": _percent_int(max(0, len(requesters) - len(missing_ids)), len(requesters)),
        "objective_value": float(solver.ObjectiveValue()),
    }


def _requesters_with_friend_count(friendships: list[dict[str, Any]], students: list[Any]) -> int:
    student_by_id = {int(student.id): student for student in students if student.id is not None}
    return len(_requesters_with_friends(friendships, student_by_id))


def _requesters_with_friends(friendships: list[dict[str, Any]], student_by_id: dict[int, Any]) -> set[int]:
    requesters: set[int] = set()
    for item in friendships:
        student_id = _safe_int(item.get("student_id"))
        friend_id = _safe_int(item.get("requested_friend_id"))
        if student_id in student_by_id and friend_id in student_by_id and student_id != friend_id:
            requesters.add(int(student_id))
    return requesters


def _structural_missing_students(structural: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in structural.get("examples", []) or []:
        rows.append(
            {
                "student_id": item.get("student_id"),
                "student_name": item.get("student_name", ""),
                "reason_labels": item.get("reason_labels", []),
                "blocked_friends": item.get("blocked_friends", []),
            }
        )
    return rows


def _student_summary_rows(student_ids: set[int] | list[int], student_by_id: dict[int, Any]) -> list[dict[str, Any]]:
    return [
        {"student_id": int(student_id), "student_name": _display_name(student_by_id.get(int(student_id)), int(student_id))}
        for student_id in sorted(int(value) for value in student_ids if value)
    ]


def _final_proven_stage(stage_reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not stage_reports:
        return None
    final = stage_reports[-1]
    if final.get("proof_status") in {
        "proven_feasible",
        "proven_blocked_exact",
        "proven_blocked_direct",
        "hard_rules_infeasible",
    }:
        return final
    return None


def _first_blocking_stage(stage_reports: list[dict[str, Any]]) -> dict[str, Any]:
    previous_missing = 0
    for report in stage_reports:
        missing = int(report.get("missing_count", 0) or 0)
        if report.get("proof_status") in {"proven_blocked_exact", "proven_blocked_direct", "hard_rules_infeasible"} and missing > previous_missing:
            return report
        previous_missing = missing
    return {}


def _friendship_response_from_stage(
    stage: dict[str, Any],
    options: dict[str, Any],
    stage_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    missing_count = int(stage.get("missing_count", 0) or 0)
    total = int(stage.get("total_with_requests", 0) or 0)
    hard_infeasible = stage.get("proof_status") == "hard_rules_infeasible"
    legal_full = missing_count == 0 and not hard_infeasible
    if legal_full:
        verdict = "proven_legal_100"
        summary = "הוכח שאפשר להגיע ל-100% חברים לפי האילוצים שסומנו."
    elif hard_infeasible:
        verdict = "proven_hard_rules_infeasible"
        summary = str(stage.get("summary") or "הוכח שהאילוצים שסומנו לא מאפשרים שיבוץ חוקי.")
    else:
        verdict = "proven_blocked_by_selected_rules"
        blocking = _first_blocking_stage(stage_reports) or stage
        summary = (
            f"הוכח ש-{blocking.get('label', stage.get('label'))} שובר 100% חברים: "
            f"{missing_count} תלמידים נשארים בלי חבר."
        )
    return {
        "status": "done",
        "verdict": verdict,
        "full_friend_coverage": legal_full,
        "legal_full_friend_coverage": legal_full,
        "satisfied_percent": _percent_int(max(0, total - missing_count), total),
        "missing_count": missing_count,
        "satisfied_count": max(0, total - missing_count),
        "total_with_requests": total,
        "hard_violation_count": 0 if legal_full else missing_count,
        "hard_violations_preview": [],
        "missing_examples": stage.get("missing_students", [])[:10],
        "structural_blockers": stage.get("structural_blockers", {}),
        "stages": stage_reports,
        "blocking_stage": _first_blocking_stage(stage_reports),
        "options": options,
        "attempts": 0,
        "failures": [],
        "score": {
            "total_score": 100 if legal_full else 0,
            "summary": stage.get("summary", ""),
            "engine_note": {"assignment_source": "friendship_proof", "exact_optimizer_available": bool(stage.get("exact_solver_available"))},
        },
        "summary": summary,
        "proof": _diagnostic_proof_label(stage_reports),
    }


def _diagnostic_proof_label(stage_reports: list[dict[str, Any]]) -> str:
    if any(report.get("exact_solver_available") for report in stage_reports):
        if all(
            report.get("proof_status") in {"proven_feasible", "proven_blocked_exact", "proven_blocked_direct", "hard_rules_infeasible"}
            for report in stage_reports
        ):
            return "exact_cp_sat"
        return "exact_cp_sat_partial"
    if any(report.get("proof_status") == "proven_blocked_direct" for report in stage_reports):
        return "direct_structural_proof"
    return "direct_structural_checks"


def _friendship_structural_diagnostic(
    students: list[Any],
    classes: list[Any],
    friendships: list[dict[str, Any]],
    class_constraints: list[dict[str, Any]],
    pair_constraints: dict[str, list[dict[str, Any]]],
    locked_assignments: dict[int, int],
    settings: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    student_by_id = {int(student.id): student for student in students if student.id is not None}
    class_ids = [int(group.id) for group in classes if group.id is not None]
    if not student_by_id or not class_ids:
        return {
            "requesters_with_no_possible_friend": 0,
            "examples": [],
            "columns": [],
            "global_blockers": [],
            "checked_friendship_requests": 0,
        }

    domains = _student_class_domains(students, classes, class_constraints, locked_assignments)
    parent = {student_id: student_id for student_id in student_by_id}
    if options["together"]:
        for item in pair_constraints.get("together", []):
            left = _safe_int(item.get("student_id"))
            right = _safe_int(item.get("other_student_id"))
            if left in parent and right in parent:
                _union(parent, left, right)

    component_members: dict[int, list[int]] = defaultdict(list)
    for student_id in student_by_id:
        component_members[_find(parent, student_id)].append(student_id)
    component_by_student = {
        student_id: component_id
        for component_id, members in component_members.items()
        for student_id in members
    }
    component_domains: dict[int, set[int]] = {}
    component_reasons: dict[int, set[str]] = defaultdict(set)
    for component_id, members in component_members.items():
        domain = set(class_ids)
        for student_id in members:
            before = set(domain)
            domain &= set(domains.get(student_id, set(class_ids)))
            if before != domain:
                component_reasons[component_id].add("class_constraints")
        domain = _apply_component_capacity_domain(domain, members, student_by_id, classes, settings, options, component_reasons[component_id])
        component_domains[component_id] = domain

    separation_pairs = {
        tuple(sorted((int(item["student_id"]), int(item["other_student_id"]))))
        for item in pair_constraints.get("separation", [])
        if item.get("student_id") is not None and item.get("other_student_id") is not None
    } if options["separation"] else set()
    requested_by_student: dict[int, list[int]] = defaultdict(list)
    checked_requests = 0
    for item in friendships:
        student_id = _safe_int(item.get("student_id"))
        friend_id = _safe_int(item.get("requested_friend_id"))
        if (
            student_id is None
            or friend_id is None
            or student_id not in student_by_id
            or friend_id not in student_by_id
            or student_id == friend_id
        ):
            continue
        requested_by_student[student_id].append(friend_id)
        checked_requests += 1

    examples: list[dict[str, Any]] = []
    column_counts: Counter[str] = Counter()
    for student_id, friend_ids in requested_by_student.items():
        possible = False
        reasons_for_student: Counter[str] = Counter()
        blocked_friends: list[dict[str, Any]] = []
        for friend_id in friend_ids:
            ok, reasons = _friend_pair_possible(
                student_id,
                friend_id,
                component_by_student,
                component_domains,
                component_reasons,
                separation_pairs,
            )
            if ok:
                possible = True
                break
            if not reasons:
                reasons = {"unknown"}
            for reason in reasons:
                reasons_for_student[reason] += 1
            blocked_friends.append(
                {
                    "friend_id": friend_id,
                    "friend_name": _display_name(student_by_id.get(friend_id), friend_id),
                    "reasons": sorted(reasons),
                }
            )
        if possible:
            continue
        for reason in reasons_for_student:
            column_counts[reason] += 1
        examples.append(
            {
                "student_id": student_id,
                "student_name": _display_name(student_by_id.get(student_id), student_id),
                "requested_friend_ids": friend_ids,
                "blocked_friends": blocked_friends[:3],
                "reasons": sorted(reasons_for_student),
                "reason_labels": [_friendship_blocker_label(reason) for reason in sorted(reasons_for_student)],
            }
        )

    global_blockers = _global_friendship_capacity_blockers(students, classes, settings, options)
    for item in global_blockers:
        key = str(item.get("key", "unknown"))
        column_counts[key] += 1

    return {
        "requesters_with_no_possible_friend": len(examples),
        "examples": examples[:10],
        "columns": [
            {"key": key, "label": _friendship_blocker_label(key), "count": count}
            for key, count in column_counts.most_common()
        ],
        "global_blockers": global_blockers,
        "checked_friendship_requests": checked_requests,
        "requesters_with_friend_requests": len(requested_by_student),
    }


def _student_class_domains(
    students: list[Any],
    classes: list[Any],
    class_constraints: list[dict[str, Any]],
    locked_assignments: dict[int, int],
) -> dict[int, set[int]]:
    class_ids = {int(group.id) for group in classes if group.id is not None}
    class_name_to_id = {normalize_name_key(group.name): int(group.id) for group in classes if group.id is not None}
    constraints_by_student = {
        int(item["student_id"]): item for item in class_constraints if item.get("student_id") is not None
    }
    domains: dict[int, set[int]] = {}
    for student in students:
        if student.id is None:
            continue
        student_id = int(student.id)
        domain = set(class_ids)
        constraint = constraints_by_student.get(student_id, {})
        allowed = _resolve_class_refs_diagnostic(constraint.get("allowed_classes", []), class_name_to_id)
        forbidden = _resolve_class_refs_diagnostic(constraint.get("forbidden_classes", []), class_name_to_id)
        if allowed:
            domain &= allowed
        domain -= forbidden
        locked_class_id = _safe_int(constraint.get("locked_class_id")) or _safe_int(locked_assignments.get(student_id))
        if locked_class_id:
            domain &= {locked_class_id}
        domains[student_id] = domain
    return domains


def _apply_component_capacity_domain(
    domain: set[int],
    members: list[int],
    student_by_id: dict[int, Any],
    classes: list[Any],
    settings: dict[str, Any],
    options: dict[str, Any],
    reasons: set[str],
) -> set[int]:
    class_by_id = {int(group.id): group for group in classes if group.id is not None}
    filtered = set(domain)
    if options["class_size"]:
        removed = set()
        for class_id in list(filtered):
            class_max = _diagnostic_effective_class_max(class_by_id[class_id], settings)
            if class_max and len(members) > class_max:
                removed.add(class_id)
        if removed:
            reasons.add("class_size")
            filtered -= removed
    if options["gender"]:
        gender_cap = _diagnostic_gender_cap(settings)
        if gender_cap:
            boys = sum(1 for student_id in members if _student_gender(student_by_id.get(student_id)) == GENDER_MALE)
            girls = sum(1 for student_id in members if _student_gender(student_by_id.get(student_id)) == GENDER_FEMALE)
            if boys > gender_cap or girls > gender_cap:
                reasons.add("gender")
                filtered.clear()
    return filtered


def _friend_pair_possible(
    student_id: int,
    friend_id: int,
    component_by_student: dict[int, int],
    component_domains: dict[int, set[int]],
    component_reasons: dict[int, set[str]],
    separation_pairs: set[tuple[int, int]],
) -> tuple[bool, set[str]]:
    source_component = component_by_student.get(student_id)
    friend_component = component_by_student.get(friend_id)
    if source_component is None or friend_component is None:
        return False, {"missing_friend_row"}
    pair = tuple(sorted((student_id, friend_id)))
    if pair in separation_pairs:
        return False, {"separation"}
    if source_component == friend_component:
        return True, set()
    source_domain = component_domains.get(source_component, set())
    friend_domain = component_domains.get(friend_component, set())
    if source_domain & friend_domain:
        return True, set()
    reasons = set(component_reasons.get(source_component, set())) | set(component_reasons.get(friend_component, set()))
    if not source_domain or not friend_domain:
        reasons.add("class_constraints")
    return False, reasons or {"class_constraints"}


def _global_friendship_capacity_blockers(
    students: list[Any],
    classes: list[Any],
    settings: dict[str, Any],
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if options["class_size"]:
        max_values = [_diagnostic_effective_class_max(group, settings) for group in classes]
        if max_values and all(value > 0 for value in max_values):
            total_capacity = sum(max_values)
            if total_capacity < len(students):
                blockers.append(
                    {
                        "key": "class_size",
                        "label": _friendship_blocker_label("class_size"),
                        "message": f"קיבולת הכיתות הכוללת היא {total_capacity}, אבל יש {len(students)} תלמידים.",
                    }
                )
    if options["gender"]:
        gender_cap = _diagnostic_gender_cap(settings)
        if gender_cap:
            gender_counts = Counter(_student_gender(student) for student in students)
            class_count = max(1, len(classes))
            for gender in (GENDER_MALE, GENDER_FEMALE):
                total_capacity = gender_cap * class_count
                count = int(gender_counts.get(gender, 0) or 0)
                if count > total_capacity:
                    blockers.append(
                        {
                            "key": "gender",
                            "label": _friendship_blocker_label("gender"),
                            "message": f"מגבלת המגדר מאפשרת {total_capacity} תלמידים/ות ממגדר {gender}, אבל יש {count}.",
                        }
                    )
    return blockers


def _resolve_class_refs_diagnostic(values: list[Any], class_name_to_id: dict[str, int]) -> set[int]:
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


def _diagnostic_effective_class_max(group: Any, settings: dict[str, Any]) -> int:
    class_max = int(getattr(group, "max_students", 0) or 0)
    global_max = _diagnostic_setting_int(settings, "max_students_per_class", 0)
    values = [value for value in (class_max, global_max) if value > 0]
    return min(values) if values else 0


def _diagnostic_gender_cap(settings: dict[str, Any]) -> int:
    return _diagnostic_setting_int(settings, "max_students_per_gender", 0)


def _diagnostic_setting_int(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return max(0, int(settings.get(key, default) or 0))
    except (TypeError, ValueError):
        return max(0, int(default))


def _student_gender(student: Any) -> str:
    value = str(getattr(student, "gender", "") or "")
    if value in {GENDER_MALE, "M", "m", "male", "boy", "זכר", "בן"}:
        return GENDER_MALE
    if value in {GENDER_FEMALE, "F", "f", "female", "girl", "נקבה", "בת"}:
        return GENDER_FEMALE
    return value


def _friendship_missing_examples(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for item in missing[:10]:
        slots = []
        for slot in item.get("slots", []) or []:
            if slot.get("received"):
                continue
            slots.append(
                {
                    "friend_id": slot.get("friend_id"),
                    "priority": slot.get("priority"),
                }
            )
        examples.append(
            {
                "student_id": item.get("student_id"),
                "student_name": item.get("student_name", ""),
                "missing_slots": slots,
            }
        )
    return examples


def _friendship_blocker_label(key: str) -> str:
    labels = {
        "class_constraints": "כיתות מותרות/נעילות",
        "separation": "לא לשבץ עם",
        "together": "חייב להיות עם",
        "class_size": "גודל כיתות",
        "gender": "מגדר",
        "missing_friend_row": "בקשת חבר לא תקינה",
        "unknown": "שילוב אילוצים",
    }
    return labels.get(key, key)


def _percent_int(value: int, total: int) -> int:
    if total <= 0:
        return 100
    percent = int((float(value) / float(total)) * 100)
    if value < total:
        return min(99, percent)
    return 100


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _display_name(student: Any, fallback_id: int) -> str:
    if student is not None and getattr(student, "display_name", ""):
        return str(student.display_name)
    return str(fallback_id)


def _find(parent: dict[int, int], value: int) -> int:
    root = value
    while parent[root] != root:
        root = parent[root]
    while parent[value] != value:
        next_value = parent[value]
        parent[value] = root
        value = next_value
    return root


def _union(parent: dict[int, int], left: int, right: int) -> None:
    left_root = _find(parent, left)
    right_root = _find(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


def _student_names(student_ids: list[int], students: dict[int, Any]) -> str:
    names = []
    for student_id in student_ids:
        student = students.get(int(student_id))
        if student:
            names.append(student.display_name)
    return ", ".join(names)


def _snapshot(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_id": int(context["active"]["id"]),
        "assignments": dict(context["assignments"]),
        "score": dict(context["score"]),
        "notes": context["active"].get("notes", ""),
        "locked_students": set(context["locked_students"]),
        "changed_students": set(context["changed_students"]),
    }


def _friend_slots(
    requested_items: list[tuple[int, int]],
    received_ids: list[int],
    students: dict[int, Any],
) -> list[dict[str, Any]]:
    by_priority = {int(priority): int(friend_id) for friend_id, priority in requested_items}
    received = {int(friend_id) for friend_id in received_ids}
    slots: list[dict[str, Any]] = []
    for priority in (1, 2, 3):
        friend_id = by_priority.get(priority)
        student = students.get(friend_id) if friend_id else None
        requested = bool(friend_id and student)
        slots.append(
            {
                "priority": priority,
                "friend_id": friend_id or 0,
                "name": student.display_name if student else "",
                "requested": requested,
                "received": bool(friend_id in received),
                "status": "received" if friend_id in received else ("missing" if requested else "not_requested"),
            }
        )
    return slots


def _notes_summary(row: dict[str, Any]) -> str:
    parts = []
    labels = {
        "parent_notes": "הורים",
        "teacher_notes": "מורה",
        "interview_notes": "ראיון",
    }
    for key, label in labels.items():
        value = str(row.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    return " | ".join(parts)


def _delta_note(before: dict[str, Any], after: dict[str, Any]) -> str:
    delta = float(after.get("total_score", 0)) - float(before.get("total_score", 0))
    if delta > 0:
        return f"הציון השתפר ב-{delta:.2f} נקודות."
    if delta < 0:
        return f"הציון ירד ב-{abs(delta):.2f} נקודות."
    return "הציון נותר ללא שינוי."


def _suggestion(action: str, before: dict[str, Any], after: dict[str, Any], **extra: Any) -> dict[str, Any]:
    delta = round(float(after.get("total_score", 0)) - float(before.get("total_score", 0)), 2)
    friendship_before = len((before.get("friendship", {}) or {}).get("missing", []))
    friendship_after = len((after.get("friendship", {}) or {}).get("missing", []))
    item = {
        "action": action,
        "delta": delta,
        "improvement": "משפרת את ציון השיבוץ" if delta > 0 else "לא משפרת את הציון",
        "cost": _cost_text(before, after),
        "score_before": before.get("total_score", 0),
        "score_after": after.get("total_score", 0),
        "hard_before": len(before.get("hard_violations", [])),
        "hard_after": len(after.get("hard_violations", [])),
        "friendship_missing_before": friendship_before,
        "friendship_missing_after": friendship_after,
        "friendship_gain": friendship_before - friendship_after,
        "score": after.get("total_score", 0),
    }
    item.update(extra)
    return item


def _action_rank(item: dict[str, Any]) -> tuple[int, int, float, float]:
    hard_gain = int(item.get("hard_before", 0) or 0) - int(item.get("hard_after", 0) or 0)
    friendship_gain = int(item.get("friendship_missing_before", 0) or 0) - int(
        item.get("friendship_missing_after", 0) or 0
    )
    return (
        hard_gain,
        friendship_gain,
        float(item.get("delta", 0) or 0),
        float(item.get("score_after", item.get("score", 0)) or 0),
    )


def _focus_student_ids(
    score: dict[str, Any],
    assignments: dict[int, int],
    explicit_ids: list[int] | None = None,
) -> list[int]:
    focus: list[int] = []
    for student_id in explicit_ids or []:
        if student_id and student_id not in focus:
            focus.append(int(student_id))
    for item in score.get("friendship", {}).get("missing", [])[:8]:
        student_id = int(item.get("student_id", 0) or 0)
        if student_id and student_id not in focus:
            focus.append(student_id)
        for slot in item.get("slots", []):
            if slot.get("received"):
                continue
            friend_id = int(slot.get("friend_id", 0) or 0)
            if friend_id and friend_id not in focus:
                focus.append(friend_id)
    for item in score.get("social_isolation", {}).get("isolated", [])[:6]:
        student_id = int(item.get("student_id", 0) or 0)
        if student_id and student_id not in focus:
            focus.append(student_id)
    weak_class_ids = [
        int(item.get("class_id", 0) or 0)
        for item in sorted(
            score.get("class_stats", []),
            key=lambda row: float(row.get("quality_score", 100) or 100),
        )[:2]
        if float(item.get("quality_score", 100) or 100) < 82
    ]
    for student_id, class_id in assignments.items():
        if len(focus) >= 12:
            break
        if int(class_id) in weak_class_ids and int(student_id) not in focus:
            focus.append(int(student_id))
    if not focus:
        focus.extend(int(student_id) for student_id in list(assignments)[:8])
    return focus


def _largest_penalty_key(score: dict[str, Any]) -> str:
    penalties = score.get("penalties", {}) or {}
    return max(penalties, key=lambda key: float(penalties.get(key, 0) or 0), default="")


def _advisor_settings(settings: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    focus = _largest_penalty_key(score)
    tuned = dict(settings)
    tuned["search_restarts"] = max(2, min(4, int(settings.get("search_restarts", 6) or 6)))
    tuned["max_iterations"] = max(120, min(260, int(settings.get("max_iterations", 220) or 220)))
    multipliers = {
        "class_size": ("class_size_weight", 1.25),
        "gender_balance": ("gender_weight", 1.35),
        "academic_balance": ("grade_weight", 1.35),
        "subject_balance": ("subject_weight", 1.35),
        "behavior_balance": ("behavior_weight", 1.35),
        "dominance_spread": ("dominance_weight", 1.3),
        "friendship": ("friendship_weight", 1.45),
        "source_school": ("source_school_weight", 1.35),
    }
    key, multiplier = multipliers.get(focus, ("class_size_weight", 1.0))
    try:
        tuned[key] = round(float(settings.get(key, 1.0) or 1.0) * multiplier, 2)
    except (TypeError, ValueError):
        tuned[key] = multiplier
    if focus in {"academic_balance", "subject_balance"}:
        tuned["grade_tolerance"] = max(1, int(float(settings.get("grade_tolerance", 4) or 4)) - 1)
    if focus == "gender_balance":
        tuned["gender_tolerance"] = max(0, int(float(settings.get("gender_tolerance", 10) or 10)) - 2)
    return tuned


def _slow_large_search_allowed(settings: dict[str, Any]) -> bool:
    return bool(settings.get("allow_slow_large_search", False))


def _effective_variant_count(requested_count: int, student_count: int, settings: dict[str, Any]) -> int:
    return max(1, min(24, int(requested_count or 1)))


def _use_quick_regular_variant(settings: dict[str, Any], student_count: int, variant_index: int) -> bool:
    return (
        variant_index > 0
        and student_count >= 160
        and settings.get("save_top_variants") is None
        and not _slow_large_search_allowed(settings)
    )


def _quick_regular_variant_settings(settings: dict[str, Any]) -> dict[str, Any]:
    quick = dict(settings)
    quick["search_restarts"] = max(1, min(3, int(quick.get("search_restarts", 6) or 6)))
    quick["max_iterations"] = max(40, min(120, int(quick.get("max_iterations", 220) or 220)))
    quick["optimizer_time_limit_seconds"] = max(1.0, min(3.0, float(quick.get("optimizer_time_limit_seconds", 8) or 8)))
    current_limit = quick.get("local_search_time_limit_seconds")
    if current_limit is None:
        quick["local_search_time_limit_seconds"] = 8.0
    else:
        quick["local_search_time_limit_seconds"] = max(5.0, min(8.0, float(current_limit or 8)))
    return quick


def _save_top_variant_count(settings: dict[str, Any]) -> int:
    try:
        return max(1, min(5, int(settings.get("save_top_variants", 5) or 5)))
    except (TypeError, ValueError):
        return 5


def _select_candidates_to_save(settings: dict[str, Any], ranked_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not ranked_candidates:
        return []
    if settings.get("save_top_variants") is not None:
        return ranked_candidates[: min(_save_top_variant_count(settings), len(ranked_candidates))]

    best_clean = next(
        (candidate for candidate in ranked_candidates if not _has_hard_violations(candidate.get("score", {}))),
        None,
    )
    best_effort = next(
        (candidate for candidate in ranked_candidates if _has_hard_violations(candidate.get("score", {}))),
        None,
    )
    selected: list[dict[str, Any]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for candidate in (best_clean, best_effort):
        if candidate is None:
            continue
        key = _assignment_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
    if not selected:
        selected.append(ranked_candidates[0])
    rank_by_key = {_assignment_key(candidate): index for index, candidate in enumerate(ranked_candidates)}
    selected.sort(key=lambda candidate: rank_by_key.get(_assignment_key(candidate), len(ranked_candidates)))
    return selected


def _ranked_unique_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=lambda item: _score_rank(item.get("score", {})), reverse=True)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for candidate in ranked:
        key = _assignment_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique or ranked


def _assignment_key(candidate: dict[str, Any]) -> tuple[tuple[int, int], ...]:
    assignments = candidate.get("assignments", {}) or {}
    return tuple(sorted((int(student_id), int(class_id)) for student_id, class_id in assignments.items()))


def _has_hard_violations(score: dict[str, Any]) -> bool:
    if score.get("hard_violations"):
        return True
    objective = score.get("objective", {}) or {}
    try:
        return int(objective.get("hard_violation_count", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _fallback_assignment_result(
    students: list[Any],
    classes: list[Any],
    friendships: list[dict[str, Any]],
    class_constraints: list[dict[str, Any]],
    pair_constraints: dict[str, list[dict[str, Any]]],
    settings: dict[str, Any],
    locked_assignments: dict[int, int],
    variant_index: int,
    error: str,
) -> dict[str, Any]:
    class_ids = [int(group.id) for group in classes if group.id is not None]
    if not class_ids:
        raise ValueError(error or "No classes available for fallback assignment.")
    locked_by_student = {
        int(item["student_id"]): int(item["locked_class_id"])
        for item in class_constraints
        if item.get("student_id") is not None and item.get("locked_class_id") in class_ids
    }
    assignments: dict[int, int] = {}
    offset = max(0, int(variant_index or 0))
    for index, student in enumerate(students):
        if student.id is None:
            continue
        student_id = int(student.id)
        assignments[student_id] = int(
            locked_assignments.get(student_id)
            or locked_by_student.get(student_id)
            or class_ids[(index + offset) % len(class_ids)]
        )
    score = evaluate_assignment(
        students=students,
        classes=classes,
        assignments=assignments,
        friendships=friendships,
        class_constraints=class_constraints,
        pair_constraints=pair_constraints,
        settings=settings,
    )
    fallback_note = dict(score.get("engine_note", {}) or {})
    fallback_note.update({"assignment_source": "best_effort_fallback", "fallback_reason": error})
    score["engine_note"] = fallback_note
    return {"assignments": assignments, "score": score}


def _skip_advisor_for_runtime(student_count: int, settings: dict[str, Any]) -> bool:
    return student_count >= 120 and not _slow_large_search_allowed(settings)


def _score_rank(score: dict[str, Any]) -> tuple[int, int, int, int, int, float, float, float]:
    return score_rank(score)


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


def _cost_text(before: dict[str, Any], after: dict[str, Any]) -> str:
    before_penalties = before.get("penalties", {})
    after_penalties = after.get("penalties", {})
    worsened = []
    for key, after_value in after_penalties.items():
        before_value = before_penalties.get(key, 0)
        if float(after_value) > float(before_value) + 0.1:
            worsened.append(key)
    if not worsened:
        return "אין מחיר משמעותי במדדים האחרים."
    labels = {
        "class_size": "גודל כיתות",
        "gender_balance": "מגדר",
        "academic_balance": "ציונים",
        "subject_balance": "מקצועות",
        "behavior_balance": "התנהגות",
        "dominance_spread": "דומיננטיות",
        "friendship": "חברים",
        "source_school": "בית ספר מקור",
        "hard_constraints": "אילוצים קשיחים",
    }
    return "עלולה לפגוע ב" + ", ".join(labels.get(key, key) for key in worsened[:3])
