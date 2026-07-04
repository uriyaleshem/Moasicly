from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Property, QThread, QTimer, QUrl, Signal, Slot

from class_balancer.ai import AiClient, anonymize_assignment_payload, load_ai_settings, save_ai_preferences, save_ai_token
from class_balancer.ai.settings import PROVIDER_ENV_VARS, provider_model_candidates, save_ai_model
from class_balancer.db import Database
from class_balancer.importers import load_table, suggest_mapping
from class_balancer.importers.templates import latest_rule_template, latest_template, save_rule_template, save_template
from class_balancer.models.fields import DEFAULT_RULE_SETTINGS, SYSTEM_FIELDS
from class_balancer.services import AssignmentService, ExportService, ImportService, ProjectService, ReportService
from class_balancer.validation import validate_students
from class_balancer.validation.normalization import (
    BEHAVIOR_MAP,
    GENDER_MAP,
    clean_text,
    normalize_behavior,
    normalize_gender,
    normalize_name_key,
    parse_grade,
    split_multi_value,
)


MAX_ASSIGNMENT_VARIANTS = 8


def _empty_conflicts_report(status: str = "not_loaded", message: str = "", project_id: int | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": status,
        "message": message,
        "conflicts": [],
        "suggested_actions": [],
        "action_candidates": [],
    }
    if project_id is not None:
        report["project_id"] = project_id
    return report


def _with_conflicts_report_status(
    report: dict[str, Any] | None,
    status: str,
    message: str = "",
    project_id: int | None = None,
) -> dict[str, Any]:
    data = dict(report or {})
    data.setdefault("conflicts", [])
    data.setdefault("suggested_actions", [])
    data.setdefault("action_candidates", [])
    data["status"] = status
    data["message"] = message
    if project_id is not None:
        data["project_id"] = project_id
    return data


def _build_conflicts_report(database: Database, project_id: int, action_limit: int = 14) -> dict[str, Any]:
    report = dict(ReportService(database).conflicts_report(project_id) or {})
    focus_ids = [
        int(item.get("student_id", 0) or 0)
        for item in report.get("conflicts", [])
        if int(item.get("student_id", 0) or 0) > 0
    ]
    try:
        report["action_candidates"] = AssignmentService(database).action_suggestions(
            project_id,
            focus_student_ids=list(dict.fromkeys(focus_ids)),
            limit=action_limit,
        )
    except ValueError:
        report["action_candidates"] = []
    return report


def _build_assistant_payload(
    database: Database,
    project_id: int,
    request_id: str,
    task: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "privacy": {
            "contains_student_names": False,
            "contains_notes": False,
            "student_references_are_anonymous": True,
        },
        "quality_report": {
            "total_score": report.get("total_score"),
            "summary": report.get("summary"),
            "manager_text": report.get("manager_text"),
            "teacher_summary": report.get("teacher_summary"),
            "penalties": report.get("penalties"),
            "hard_violations_count": len(report.get("hard_violations", [])),
            "missing_friends_count": len(report.get("missing_friends", [])),
            "isolated_students_count": len(report.get("isolated_students", [])),
            "global_stats": report.get("global_stats", {}),
            "class_stats": _safe_class_stats(report.get("class_stats", [])),
        },
    }
    if request_id == "conflicts" or "אילוצים" in task:
        payload["conflicts"] = _build_conflict_payload(database, project_id)
    return payload


def _build_conflict_payload(database: Database, project_id: int) -> dict[str, Any]:
    report_service = ReportService(database)
    assignment_service = AssignmentService(database)
    conflicts = report_service.conflicts_report(project_id)
    students = database.get_students(project_id)
    student_refs = {
        int(student.id): f"S{index:03d}"
        for index, student in enumerate(sorted(students, key=lambda item: int(item.id or 0)), start=1)
        if student.id is not None
    }
    name_to_ref = {student.display_name: student_refs[int(student.id)] for student in students if student.id is not None}
    school_refs: dict[str, str] = {}
    safe_conflicts = []
    for item in conflicts.get("conflicts", [])[:12]:
        school = str(item.get("school", "") or "")
        if school and school not in school_refs:
            school_refs[school] = f"School{len(school_refs) + 1:03d}"
        student_id = int(item.get("student_id", 0) or 0)
        safe_conflicts.append(
            {
                "type": item.get("type", ""),
                "severity": item.get("severity", ""),
                "student_ref": student_refs.get(student_id, ""),
                "class_name": item.get("class_name", ""),
                "school_ref": school_refs.get(school, ""),
                "reason_he": _replace_names(str(item.get("reason", "")), name_to_ref, school_refs),
            }
        )
    local_candidates = []
    focus_ids = [
        int(item.get("student_id", 0) or 0)
        for item in conflicts.get("conflicts", [])
        if int(item.get("student_id", 0) or 0) > 0
    ]
    for student_id in dict.fromkeys(focus_ids[:5]):
        try:
            suggestions = assignment_service.smart_suggestions(project_id, student_id, limit=3)
        except ValueError:
            suggestions = []
        for suggestion in suggestions:
            local_candidates.append(
                {
                    "student_ref": student_refs.get(student_id, ""),
                    "action_he": _replace_names(str(suggestion.get("action", "")), name_to_ref, school_refs),
                    "score_delta": suggestion.get("delta", 0),
                    "score_after": suggestion.get("score", 0),
                    "cost_he": str(suggestion.get("cost", "")),
                }
            )
    return {
        "conflicts": safe_conflicts,
        "suggested_actions_he": [
            _replace_names(str(item), name_to_ref, school_refs)
            for item in conflicts.get("suggested_actions", [])[:8]
        ],
        "local_move_or_swap_candidates": local_candidates[:12],
    }


def _build_local_ai_fallback(database: Database, project_id: int, task: str, report: dict[str, Any]) -> str:
    report_service = ReportService(database)
    assignment_service = AssignmentService(database)
    conflicts = report_service.conflicts_report(project_id)
    lines = [
        "הצעה מקומית ללא AI:",
        f"ציון השיבוץ: {report.get('total_score', '-')}.",
        f"כללים מחייבים שנשברו: {len(report.get('hard_violations', []))}.",
        f"תלמידים ללא חבר מבוקש: {len(report.get('missing_friends', []))}.",
        "פעולות עם ציון מחושב:",
    ]
    focus_ids = [
        int(item.get("student_id", 0) or 0)
        for item in conflicts.get("conflicts", [])
        if int(item.get("student_id", 0) or 0) > 0
    ]
    try:
        actions = assignment_service.action_suggestions(project_id, focus_ids, limit=6)
    except ValueError:
        actions = []
    if actions:
        for action in actions:
            lines.append(
                f"- {action.get('action')}: לפני {action.get('score_before')}, אחרי {action.get('score_after')}, שינוי {action.get('delta')} נק׳; {action.get('cost')}"
            )
    else:
        lines.extend(f"- {item}" for item in conflicts.get("suggested_actions", []))
    if "אילוצים" in task or "תיקון" in task:
        for student_id in dict.fromkeys(focus_ids[:5]):
            details = _student_details(database, project_id, student_id)
            student = details.get("student", {})
            suggestions = details.get("suggestions", [])
            if not student or not suggestions:
                continue
            lines.append("")
            lines.append(f"הצעות עבור {student.get('full_name') or student.get('first_name') or student_id}:")
            for suggestion in suggestions[:3]:
                lines.append(
                    f"- {suggestion.get('action')}: שינוי ציון {suggestion.get('delta')} נק׳; {suggestion.get('cost')}"
                )
    if "מנהלת" in task or "דוח" in task:
        lines.append("")
        lines.append(report.get("teacher_summary") or report.get("manager_text", ""))
    return "\n".join(lines)


class AssignmentWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(float, str)

    def __init__(
        self,
        database_path: str,
        project_id: int,
        variant_count: int = 1,
        run_name: str = "שיבוץ אוטומטי",
        settings_override: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.database_path = database_path
        self.project_id = project_id
        self.variant_count = variant_count
        self.run_name = run_name
        self.settings_override = settings_override or {}
        self._last_progress = -1.0

    @Slot()
    def run(self) -> None:
        database = Database(self.database_path)
        try:
            result = AssignmentService(database).run_assignment(
                self.project_id,
                name=self.run_name,
                variant_count=self.variant_count,
                settings_override=self.settings_override,
                progress_callback=self._report_progress,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            database.close()

    def _report_progress(self, percent: float, message: str) -> None:
        clean_percent = max(0.0, min(100.0, float(percent)))
        if clean_percent >= 100 or clean_percent - self._last_progress >= 0.35:
            self._last_progress = clean_percent
            self.progress.emit(clean_percent, message)


class FriendshipDiagnosticWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(float, str)

    def __init__(
        self,
        database_path: str,
        project_id: int,
        options: dict[str, Any],
    ) -> None:
        super().__init__()
        self.database_path = database_path
        self.project_id = project_id
        self.options = options
        self._last_progress = -1.0

    @Slot()
    def run(self) -> None:
        database = Database(self.database_path)
        try:
            result = AssignmentService(database).friendship_diagnostic(
                self.project_id,
                self.options,
                progress_callback=self._report_progress,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            database.close()

    def _report_progress(self, percent: float, message: str) -> None:
        clean_percent = max(0.0, min(100.0, float(percent)))
        if clean_percent >= 100 or clean_percent - self._last_progress >= 0.35:
            self._last_progress = clean_percent
            self.progress.emit(clean_percent, message)


class ConflictsReportWorker(QObject):
    finished = Signal(int, int, object)
    failed = Signal(int, int, str)

    def __init__(self, database_path: str, project_id: int, request_token: int) -> None:
        super().__init__()
        self.database_path = database_path
        self.project_id = project_id
        self.request_token = request_token

    @Slot()
    def run(self) -> None:
        database = Database(self.database_path)
        try:
            report = _build_conflicts_report(database, self.project_id)
            self.finished.emit(self.project_id, self.request_token, report)
        except Exception as exc:
            self.failed.emit(self.project_id, self.request_token, str(exc))
        finally:
            database.close()


class ManualAssignmentActionWorker(QObject):
    finished = Signal(str, object)
    failed = Signal(str)

    def __init__(
        self,
        database_path: str,
        project_id: int,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        super().__init__()
        self.database_path = database_path
        self.project_id = project_id
        self.action = action
        self.payload = payload

    @Slot()
    def run(self) -> None:
        database = Database(self.database_path)
        try:
            service = AssignmentService(database)
            if self.action == "move":
                result = service.move_student(
                    self.project_id,
                    int(self.payload["student_id"]),
                    int(self.payload["class_id"]),
                    bool(self.payload.get("lock", False)),
                )
            elif self.action == "swap":
                result = service.swap_students(
                    self.project_id,
                    int(self.payload["left_student_id"]),
                    int(self.payload["right_student_id"]),
                )
            else:
                raise ValueError("Unknown manual assignment action.")
            self.finished.emit(self.action, result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            database.close()


class StudentDetailsWorker(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, database_path: str, project_id: int, student_id: int) -> None:
        super().__init__()
        self.database_path = database_path
        self.project_id = project_id
        self.student_id = student_id

    @Slot()
    def run(self) -> None:
        database = Database(self.database_path)
        try:
            details = _student_details(database, self.project_id, self.student_id)
            self.finished.emit(self.student_id, details)
        except Exception as exc:
            self.failed.emit(self.student_id, str(exc))
        finally:
            database.close()


class PreviewWorker(QObject):
    finished = Signal(object, object)
    failed = Signal(str)

    def __init__(self, path: str, sheet_name: str = "") -> None:
        super().__init__()
        self.path = path
        self.sheet_name = sheet_name

    @Slot()
    def run(self) -> None:
        try:
            table = load_table(self.path, sheet_name=self.sheet_name or None, preview_limit=20)
            mapping = suggest_mapping(table.headers, table.rows)
            self.finished.emit(table, mapping)
        except Exception as exc:
            self.failed.emit(str(exc))


class ImportSaveWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, database_path: str, project_id: int, path: str, sheet_name: str, mapping: dict[str, str]) -> None:
        super().__init__()
        self.database_path = database_path
        self.project_id = project_id
        self.path = path
        self.sheet_name = sheet_name
        self.mapping = mapping

    @Slot()
    def run(self) -> None:
        database = Database(self.database_path)
        try:
            service = ImportService(database)
            service.load_preview(self.path, self.sheet_name or None)
            service.current_mapping = dict(self.mapping)
            self.finished.emit(service.save_imported_students(self.project_id, service.current_mapping))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            database.close()


class ExportWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, database_path: str, project_id: int, path: str, validation_only: bool = False) -> None:
        super().__init__()
        self.database_path = database_path
        self.project_id = project_id
        self.path = path
        self.validation_only = validation_only

    @Slot()
    def run(self) -> None:
        database = Database(self.database_path)
        try:
            service = ExportService(database)
            if self.validation_only:
                path = service.export_validation_issues(self.project_id, self.path)
            else:
                path = service.export_project(self.project_id, self.path)
            self.finished.emit(str(path))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            database.close()


class AiReviewWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        task: str,
        payload: dict[str, Any],
        fallback: str,
        allow_external: bool,
        provider_limit: int,
    ) -> None:
        super().__init__()
        self.task = task
        self.payload = payload
        self.fallback = fallback
        self.allow_external = allow_external
        self.provider_limit = provider_limit

    @Slot()
    def run(self) -> None:
        try:
            result = AiClient(timeout_seconds=25).complete_structured_all(
                self.task,
                self.payload,
                self.fallback,
                allow_external=self.allow_external,
                provider_limit=self.provider_limit,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class AssistantWorker(QObject):
    finished = Signal(str, object)
    failed = Signal(str, str)

    def __init__(
        self,
        request_id: str,
        task: str,
        payload: dict[str, Any] | None,
        fallback: str | None,
        allow_external: bool,
        database_path: str = "",
        project_id: int = 0,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.task = task
        self.payload = payload or {}
        self.fallback = fallback or ""
        self.allow_external = allow_external
        self.database_path = database_path
        self.project_id = project_id

    @Slot()
    def run(self) -> None:
        try:
            payload = dict(self.payload)
            fallback = self.fallback
            if self.database_path and self.project_id and (not payload or not fallback):
                database = Database(self.database_path)
                try:
                    report = ReportService(database).quality_report(self.project_id)
                    payload = _build_assistant_payload(database, self.project_id, self.request_id, self.task, report)
                    fallback = _build_local_ai_fallback(database, self.project_id, self.task, report)
                finally:
                    database.close()
            if not self.allow_external:
                result = {
                    "used_ai": False,
                    "text": fallback + "\n\nשליחה ל-AI אינה מאושרת בפרויקט הזה, לכן נוצר הסבר מקומי בלבד.",
                    "source": "local",
                    "payload": payload,
                }
            else:
                result = AiClient(timeout_seconds=25).complete(self.task, payload, fallback)
            self.finished.emit(self.request_id, result)
        except Exception as exc:
            self.failed.emit(self.request_id, str(exc))


class AiActionSuggestionWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        task: str,
        payload: dict[str, Any],
        candidates: list[dict[str, Any]],
        allow_external: bool,
        provider_limit: int,
    ) -> None:
        super().__init__()
        self.task = task
        self.payload = payload
        self.candidates = candidates
        self.allow_external = allow_external
        self.provider_limit = provider_limit

    @Slot()
    def run(self) -> None:
        try:
            fallback = "לא נמצאו הצעות AI לשיפור. ייתכן שהשיבוץ הנוכחי הוא הטוב ביותר מבין הפעולות שנבדקו."
            result = AiClient(timeout_seconds=25).complete_action_selection(
                self.task,
                self.payload,
                fallback,
                allow_external=self.allow_external,
                provider_limit=self.provider_limit,
            )
            by_id = {str(item.get("candidate_id")): dict(item) for item in self.candidates}
            selection = result.get("selection", {}) or {}
            notes = {
                str(item.get("candidate_id")): str(item.get("reason_he", ""))
                for item in selection.get("notes", [])
                if isinstance(item, dict)
            }
            actions = []
            for candidate_id in selection.get("selected_candidate_ids", []):
                action = by_id.get(str(candidate_id))
                if not action:
                    continue
                improves_score = float(action.get("delta", 0) or 0) > 0.05
                improves_hard_rules = int(action.get("hard_after", 0) or 0) < int(action.get("hard_before", 0) or 0)
                improves_friendship = int(action.get("friendship_missing_after", 0) or 0) < int(
                    action.get("friendship_missing_before", 0) or 0
                )
                does_not_add_hard_rules = int(action.get("hard_after", 0) or 0) <= int(action.get("hard_before", 0) or 0)
                if not does_not_add_hard_rules or not (improves_score or improves_hard_rules or improves_friendship):
                    continue
                action["ai_reason"] = notes.get(str(candidate_id), "")
                actions.append(action)
            message = selection.get("summary_he") or result.get("text") or fallback
            if not actions and not result.get("used_ai"):
                for candidate in self.candidates[:5]:
                    local_action = dict(candidate)
                    improves_score = float(local_action.get("delta", 0) or 0) > 0.05
                    improves_hard_rules = int(local_action.get("hard_after", 0) or 0) < int(
                        local_action.get("hard_before", 0) or 0
                    )
                    improves_friendship = int(local_action.get("friendship_missing_after", 0) or 0) < int(
                        local_action.get("friendship_missing_before", 0) or 0
                    )
                    does_not_add_hard_rules = int(local_action.get("hard_after", 0) or 0) <= int(
                        local_action.get("hard_before", 0) or 0
                    )
                    if does_not_add_hard_rules and (improves_score or improves_hard_rules or improves_friendship):
                        actions.append(local_action)
                if actions:
                    if result.get("status") == "local_only":
                        message = "נבחרו הצעות מקומיות מתוך פעולות ההעברה וההחלפה שנוקדו. לא נשלחה בקשת AI חיצונית."
                    else:
                        message = "AI לא החזיר בחירה תקינה, לכן מוצגות ההצעות המקומיות הטובות ביותר."
            if not actions and result.get("used_ai"):
                message = message or "ה-AI לא מצא העברה או החלפה שמשפרת את הציון מתוך המועמדים שנבדקו."
            self.finished.emit(
                {
                    "status": result.get("status", "ai_failed"),
                    "used_ai": bool(result.get("used_ai")),
                    "source": result.get("source", "local"),
                    "message": message,
                    "actions": actions,
                    "providers": result.get("providers", []),
                    "selection": selection,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class AiRuleRecommendationWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        task: str,
        payload: dict[str, Any],
        fallback: str,
        allow_external: bool,
        provider_limit: int,
    ) -> None:
        super().__init__()
        self.task = task
        self.payload = payload
        self.fallback = fallback
        self.allow_external = allow_external
        self.provider_limit = provider_limit

    @Slot()
    def run(self) -> None:
        try:
            result = AiClient(timeout_seconds=60).complete_rule_recommendation(
                self.task,
                self.payload,
                self.fallback,
                allow_external=self.allow_external,
                provider_limit=self.provider_limit,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class AiConnectionTestWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, mode: str, provider: str = "") -> None:
        super().__init__()
        self.mode = mode
        self.provider = provider

    @Slot()
    def run(self) -> None:
        try:
            if self.mode == "single":
                result = AiClient(timeout_seconds=12).test_connection(self.provider)
                self.finished.emit(
                    {
                        "mode": self.mode,
                        "ok_count": 1 if result.get("ok") else 0,
                        "total": 1,
                        "providers": [{"provider": self.provider, **result}],
                        "message": result.get("message", ""),
                        "settings": load_ai_settings(),
                    }
                )
                return

            if self.mode == "all":
                results = []
                settings = load_ai_settings()
                client = AiClient(timeout_seconds=12)
                for provider in settings.get("providers", []):
                    provider_name = provider.get("provider", "")
                    if not provider.get("configured"):
                        results.append(
                            {
                                "provider": provider_name,
                                "ok": False,
                                "message": "לא מוגדר מפתח AI לספק.",
                                "source": "",
                            }
                        )
                        continue
                    result = client.test_connection(provider_name)
                    results.append({"provider": provider_name, **result})
                ok_count = sum(1 for item in results if item.get("ok"))
                self.finished.emit(
                    {
                        "mode": self.mode,
                        "ok_count": ok_count,
                        "total": len(results),
                        "providers": results,
                        "message": f"בדיקת AI הסתיימה: {ok_count}/{len(results)} שירותי AI זמינים.",
                        "settings": load_ai_settings(),
                    }
                )
                return

            if self.mode == "models":
                results = []
                settings = load_ai_settings()
                client = AiClient(timeout_seconds=12)
                for provider in settings.get("providers", []):
                    provider_name = provider.get("provider", "")
                    if not provider.get("configured"):
                        results.append(
                            {
                                "provider": provider_name,
                                "ok": False,
                                "used": False,
                                "message": "לא מוגדר מפתח AI לספק.",
                                "attempts": [],
                            }
                        )
                        continue

                    attempts = []
                    selected: str | None = None
                    selected_message = ""
                    for model in provider_model_candidates(provider_name):
                        result = client.test_connection(provider_name, model=model)
                        attempts.append(
                            {
                                "model": model,
                                "ok": bool(result.get("ok")),
                                "message": result.get("message", ""),
                            }
                        )
                        if result.get("ok"):
                            selected = model
                            selected_message = result.get("message", "OK")
                            save_ai_model(provider_name, model, mirror_to_project=False)
                            break

                    results.append(
                        {
                            "provider": provider_name,
                            "ok": bool(selected),
                            "used": True,
                            "model": selected or provider.get("model", ""),
                            "message": f"נמצא מודל מתאים: {selected}" if selected else "לא נמצא מודל זמין ברשימת המועמדים.",
                            "test_message": selected_message,
                            "attempts": attempts,
                        }
                    )

                ok_count = sum(1 for item in results if item.get("ok"))
                total_configured = sum(1 for item in results if item.get("used"))
                self.finished.emit(
                    {
                        "mode": self.mode,
                        "ok_count": ok_count,
                        "total_configured": total_configured,
                        "providers": results,
                        "message": f"איתור מודלים הסתיים: {ok_count}/{total_configured} ספקים מוגדרים עברו בהצלחה.",
                        "settings": load_ai_settings(),
                    }
                )
                return

            raise ValueError(f"Unknown AI connection test mode: {self.mode}")
        except Exception as exc:
            self.failed.emit(str(exc))


class AppBridge(QObject):
    statusChanged = Signal()
    busyChanged = Signal()
    aiReviewChanged = Signal()
    aiActionSuggestionsChanged = Signal()
    aiRuleRecommendationChanged = Signal()
    friendshipDiagnosticChanged = Signal()
    conflictsReportChanged = Signal()
    aiConnectionTestFinished = Signal(object)
    assistantFinished = Signal(str)
    assignmentFinished = Signal()
    dataChanged = Signal()
    previewChanged = Signal()
    currentProjectChanged = Signal()
    studentDetailsLoaded = Signal(int, object)
    studentDetailsFailed = Signal(int, str)

    def __init__(
        self,
        database: Database,
        project_service: ProjectService,
        import_service: ImportService,
        assignment_service: AssignmentService,
        export_service: ExportService,
        report_service: ReportService,
    ) -> None:
        super().__init__()
        self.database = database
        self.project_service = project_service
        self.import_service = import_service
        self.assignment_service = assignment_service
        self.export_service = export_service
        self.report_service = report_service
        self.ai_client = AiClient()
        self.current_project_id: int | None = None
        self._status = "מוכן"
        self._busy = False
        self._busy_text = ""
        self._busy_progress = 0.0
        self._busy_progress_text = ""
        self._busy_started_at: float | None = None
        self._busy_last_progress_message = ""
        self._busy_initial_estimate_seconds: float | None = None
        self._busy_eta_seconds: float | None = None
        self._busy_progress_samples: list[tuple[float, float]] = []
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(1000)
        self._busy_timer.timeout.connect(self._tick_busy_eta)
        self._threads: list[tuple[QThread, QObject]] = []
        self._assistant_results: dict[str, dict[str, Any]] = {}
        self._dashboard_cache: dict[int, dict[str, Any]] = {}
        self._quality_report_cache: dict[int, dict[str, Any]] = {}
        self._conflicts_report_cache: dict[int, dict[str, Any]] = {}
        self._conflicts_report_status = _empty_conflicts_report(
            "not_loaded",
            "דוח האילוצים עדיין לא נטען.",
        )
        self._conflicts_report_loading_project_id: int | None = None
        self._conflicts_report_request_token = 0
        self._ai_action_suggestions: dict[str, Any] = {
            "status": "not_run",
            "used_ai": False,
            "source": "",
            "message": "AI עדיין לא בדק הצעות העברה או החלפה לשיבוץ הפעיל.",
            "actions": [],
            "providers": [],
        }
        self._ai_rule_recommendation: dict[str, Any] = {
            "status": "not_run",
            "used_ai": False,
            "source": "",
            "message": "AI עדיין לא המליץ על כללים לפרויקט הזה.",
            "providers": [],
            "recommendation": {},
        }
        self._force_ai_review_after_assignment = False
        self._request_ai_actions_after_assignment = False
        self._current_assignment_run_mode = "regular"
        self._ai_review_provider_limit = 3
        self._last_ai_review: dict[str, Any] = {
            "status": "not_run",
            "used_ai": False,
            "text": "AI עדיין לא הופעל בפרויקט הזה.",
            "providers": [],
            "best": {},
        }
        self._friendship_diagnostic: dict[str, Any] = {
            "status": "not_run",
            "message": "בדיקת חברים עדיין לא הורצה.",
            "result": {},
        }
        projects = self.database.list_projects()
        if projects:
            self.current_project_id = projects[0].id
        self.dataChanged.connect(self._clear_ui_caches)
        self.currentProjectChanged.connect(self._clear_ui_caches)

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=busyChanged)
    def busyText(self) -> str:
        return self._busy_text

    @Property(float, notify=busyChanged)
    def busyProgress(self) -> float:
        return self._busy_progress

    @Property(str, notify=busyChanged)
    def busyProgressText(self) -> str:
        return self._busy_progress_text

    @Property(str, constant=True)
    def databasePath(self) -> str:
        return str(self.database.path)

    @Slot(result="QVariant")
    def recentProjects(self) -> list[dict[str, Any]]:
        return [project.to_dict() for project in self.database.list_projects()]

    @Slot(result="QVariant")
    def currentProject(self) -> dict[str, Any]:
        if not self.current_project_id:
            return {}
        project = self.database.get_project(self.current_project_id)
        return project.to_dict() if project else {}

    @Slot(str, str, str, int, str, str, result=int)
    def createProject(self, name: str, grade: str, year: str, class_count: int, class_names: str, notes: str) -> int:
        project_id = self.project_service.create_project(name, grade, year, class_count, class_names, notes)
        self.current_project_id = project_id
        self._set_status("נוצר פרויקט חדש.")
        self.currentProjectChanged.emit()
        self.dataChanged.emit()
        return project_id

    @Slot(int, result=bool)
    def openProject(self, project_id: int) -> bool:
        if not self.database.get_project(project_id):
            self._set_status("הפרויקט לא נמצא.")
            return False
        self.current_project_id = project_id
        self._set_status("הפרויקט נפתח.")
        self.currentProjectChanged.emit()
        self.dataChanged.emit()
        return True

    @Slot(int, result=bool)
    def deleteProject(self, project_id: int) -> bool:
        deleted = self.project_service.delete_project(project_id)
        if not deleted:
            self._set_status("הפרויקט לא נמצא למחיקה.")
            return False
        if self.current_project_id == project_id:
            projects = self.database.list_projects()
            self.current_project_id = projects[0].id if projects else None
            self.currentProjectChanged.emit()
        self._set_status("הפרויקט נמחק.")
        self.dataChanged.emit()
        return True

    @Slot(result=bool)
    def projectAllowsExternalAi(self) -> bool:
        if not self.current_project_id:
            return False
        project = self.database.get_project(self.current_project_id)
        return bool(project and project.settings.get("ai_external_allowed", False))

    @Slot(bool, result=bool)
    def setProjectAllowsExternalAi(self, allowed: bool) -> bool:
        project_id = self._require_project()
        self.project_service.update_settings(project_id, {"ai_external_allowed": bool(allowed)})
        self._set_status("הרשאת AI לפרויקט עודכנה.")
        self.dataChanged.emit()
        return bool(allowed)

    @Slot(str, str, result="QVariant")
    def previewFile(self, file_url: str, sheet_name: str = "") -> dict[str, Any]:
        path = _path_from_url(file_url)
        table = self.import_service.load_preview(path, sheet_name or None)
        self._set_status(f"נטען קובץ עם {table.row_count} שורות.")
        self.previewChanged.emit()
        return self.previewData()

    @Slot(str, str, result="QVariant")
    def previewFileAsync(self, file_url: str, sheet_name: str = "") -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        path = _path_from_url(file_url)
        self._set_busy(
            True,
            "טוען קובץ תלמידים...",
            progress=0,
            progress_text="קורא את הקובץ ומכין תצוגה מקדימה.",
            estimated_seconds=20,
        )
        thread = QThread(self)
        worker = PreviewWorker(path, sheet_name)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._preview_worker_finished)
        worker.failed.connect(self._preview_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()
        return {"started": True, "message": "טעינת הקובץ התחילה."}

    @Slot(result="QVariant")
    def previewData(self) -> dict[str, Any]:
        table = self.import_service.current_table
        if not table:
            return {"headers": [], "rows": [], "sheet_names": [], "row_count": 0, "selected_sheet": ""}
        return {
            "headers": table.headers,
            "rows": table.preview(20),
            "sheet_names": table.sheet_names,
            "row_count": table.row_count,
            "selected_sheet": table.selected_sheet,
            "encoding": table.encoding,
        }

    @Slot(result="QVariant")
    def mappingRows(self) -> list[dict[str, Any]]:
        mapping = self.import_service.current_mapping
        return [
            {
                "field": field.name,
                "label": field.label_he,
                "source": mapping.get(field.name, ""),
                "help": field.help_text,
                "required": field.required,
            }
            for field in SYSTEM_FIELDS
        ]

    @Slot(str, str)
    def setMapping(self, field_name: str, source_column: str) -> None:
        self.import_service.current_mapping[field_name] = source_column
        self.previewChanged.emit()

    @Slot(result="QVariant")
    def autoMapping(self) -> list[dict[str, Any]]:
        table = self.import_service.current_table
        if table:
            from class_balancer.importers.mapping import suggest_mapping

            self.import_service.current_mapping = suggest_mapping(table.headers)
        return self.mappingRows()

    @Slot(bool, result="QVariant")
    def aiSuggestMapping(self, approved_to_send: bool = False) -> dict[str, Any]:
        table = self.import_service.current_table
        if not table:
            return {"used_ai": False, "text": "אין קובץ טעון.", "payload": {}}
        payload = {"headers": table.headers, "allowed_fields": [field.name for field in SYSTEM_FIELDS]}
        fallback = "הצעת מיפוי מקומית הופעלה. השתמשו בכפתור 'מיפוי אוטומטי' כדי ליישם אותה."
        if not self._project_allows_external_ai():
            return {"used_ai": False, "text": fallback + "\n\nשליחה ל-AI אינה מאושרת בפרויקט הזה, לכן לא נשלחו שמות עמודות.", "payload": payload}
        result = self.ai_client.complete(
            "הצע מיפוי עמודות לשדות המערכת. אל תבקש נתוני תלמידים, השתמש רק בשמות העמודות.",
            payload,
            fallback,
        )
        self._set_status("התקבלה הצעת מיפוי.")
        return result

    @Slot(str, bool, result="QVariant")
    def aiSuggestMappingAsync(self, request_id: str, approved_to_send: bool = False) -> dict[str, Any]:
        table = self.import_service.current_table
        if not table:
            result = {"used_ai": False, "text": "אין קובץ טעון.", "payload": {}, "source": "local"}
            self._assistant_results[request_id] = result
            self.assistantFinished.emit(request_id)
            return {"started": False, "message": result["text"]}
        payload = {"headers": table.headers, "allowed_fields": [field.name for field in SYSTEM_FIELDS]}
        fallback = "הצעת מיפוי מקומית הופעלה. השתמשו בכפתור 'מיפוי אוטומטי' כדי ליישם אותה."
        task = "הצע מיפוי עמודות לשדות המערכת. אל תבקש נתוני תלמידים, השתמש רק בשמות העמודות."
        self._start_assistant_request(request_id, task, payload, fallback, bool(approved_to_send and self._project_allows_external_ai()))
        return {"started": True, "message": "בקשת מיפוי AI התחילה."}

    @Slot(str, result=str)
    def saveMappingTemplate(self, name: str) -> str:
        table = self.import_service.current_table
        if not table:
            self._set_status("אין קובץ טעון לשמירת תבנית.")
            return ""
        path = save_template(name or "תבנית אחרונה", self.import_service.current_mapping, table.headers)
        self._set_status("תבנית המיפוי נשמרה.")
        return str(path)

    @Slot(result="QVariant")
    def loadLatestMappingTemplate(self) -> list[dict[str, Any]]:
        template = latest_template()
        if not template:
            self._set_status("לא נמצאה תבנית מיפוי שמורה.")
            return self.mappingRows()
        self.import_service.current_mapping = dict(template.get("mapping", {}))
        self._set_status(f"נטענה תבנית מיפוי: {template.get('name', '')}")
        self.previewChanged.emit()
        return self.mappingRows()

    @Slot(result="QVariant")
    def saveImportedStudents(self) -> dict[str, Any]:
        project_id = self._require_project()
        result = self.import_service.save_imported_students(project_id, self.import_service.current_mapping)
        self._set_status(f"יובאו {result['students_count']} תלמידים.")
        self.dataChanged.emit()
        return result

    @Slot(result="QVariant")
    def saveImportedStudentsAsync(self) -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        project_id = self._require_project()
        table = self.import_service.current_table
        if not table:
            return {"started": False, "message": "לא נטען קובץ תלמידים."}
        self._set_busy(
            True,
            "מייבא תלמידים...",
            progress=0,
            progress_text="קורא את כל השורות, שומר תלמידים, חברים ואילוצים ובודק נתונים.",
            estimated_seconds=45,
        )
        thread = QThread(self)
        worker = ImportSaveWorker(
            str(self.database.path),
            project_id,
            str(table.path),
            table.selected_sheet,
            dict(self.import_service.current_mapping),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._import_worker_finished)
        worker.failed.connect(self._import_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()
        return {"started": True, "message": "ייבוא התלמידים התחיל."}

    @Slot(result="QVariant")
    def validationIssues(self) -> list[dict[str, Any]]:
        if not self.current_project_id:
            return []
        return [issue.to_dict() for issue in self.database.get_validation_issues(self.current_project_id)]

    @Slot(result="QVariant")
    def normalizationRules(self) -> list[dict[str, str]]:
        rows = []
        for source, target in sorted(GENDER_MAP.items()):
            rows.append({"field": "מגדר", "source": source, "target": target})
        for source, target in sorted(BEHAVIOR_MAP.items()):
            rows.append({"field": "התנהגות", "source": source, "target": target})
        return rows

    @Slot(result=int)
    def applyStandardNormalizations(self) -> int:
        project_id = self._require_project()
        changed = 0
        for student in self.database.get_students(project_id):
            if student.id is None:
                continue
            normalized_gender = normalize_gender(student.gender)
            normalized_behavior = normalize_behavior(student.behavior_score)
            fields: dict[str, Any] = {}
            if normalized_gender != student.gender:
                fields["gender"] = normalized_gender
            if normalized_behavior != student.behavior_score:
                fields["behavior_score"] = normalized_behavior
            if fields:
                self.database.update_student_fields(project_id, student.id, fields)
                changed += 1
        self._refresh_validation(project_id)
        self._set_status(f"הוחל נרמול ערכים על {changed} תלמידים.")
        self.dataChanged.emit()
        return changed

    @Slot(str)
    def updateClasses(self, class_names: str) -> None:
        project_id = self._require_project()
        self.project_service.update_classes(project_id, class_names)
        self._set_status("רשימת הכיתות עודכנה.")
        self.dataChanged.emit()

    @Slot("QVariant", "QVariant", result=int)
    def applyAllowedClassesToStudents(self, student_ids: Any, allowed_classes: Any) -> int:
        project_id = self._require_project()
        selected_ids = [int(value) for value in _plain_variant(student_ids) or [] if int(value) > 0]
        allowed = [str(value).strip() for value in _plain_variant(allowed_classes) or [] if str(value).strip()]
        if not selected_ids or not allowed:
            self._set_status("בחרו תלמידים וכיתות מותרות לפני שמירה.")
            return 0
        existing = {
            int(item["student_id"]): dict(item)
            for item in self.database.get_class_constraints(project_id)
            if item.get("student_id") is not None
        }
        for student_id in selected_ids:
            current = existing.get(
                student_id,
                {"student_id": student_id, "allowed_classes": [], "forbidden_classes": [], "locked_class_id": None},
            )
            current["allowed_classes"] = allowed
            existing[student_id] = current
        self.database.replace_class_constraints(project_id, existing.values())
        self._refresh_validation(project_id)
        self._set_status(f"עודכנו כיתות מותרות ל-{len(selected_ids)} תלמידים.")
        self.dataChanged.emit()
        return len(selected_ids)

    @Slot(result="QVariant")
    def classList(self) -> list[dict[str, Any]]:
        if not self.current_project_id:
            return []
        return [group.to_dict() for group in self.database.get_classes(self.current_project_id)]

    @Slot(result="QVariant")
    def studentList(self) -> list[dict[str, Any]]:
        if not self.current_project_id:
            return []
        return [student.to_dict() for student in self.database.get_students(self.current_project_id)]

    @Slot("QVariant")
    def saveRuleSettings(self, settings: Any) -> None:
        project_id = self._require_project()
        project = self.database.get_project(project_id)
        clean = {**DEFAULT_RULE_SETTINGS, **(project.settings if project else {}), **_variant_dict(settings)}
        clean["hard_class_capacity"] = True
        clean["max_students_per_class"] = max(1, int(clean.get("max_students_per_class", 40) or 40))
        clean["max_students_per_gender"] = max(1, int(clean.get("max_students_per_gender", 20) or 20))
        self.project_service.update_settings(project_id, clean)
        self._reset_friendship_diagnostic("בדיקת החברים אופסה כי הכללים השתנו.")
        self._set_status("כללי השיבוץ נשמרו.")
        self.dataChanged.emit()

    @Slot(str, result=str)
    def saveRuleTemplate(self, name: str) -> str:
        self._require_project()
        settings = dict(self.ruleSettings())
        settings.pop("ai_external_allowed", None)
        path = save_rule_template(name or "תבנית כללים אחרונה", settings)
        self._set_status("תבנית הכללים נשמרה.")
        return str(path)

    @Slot(result="QVariant")
    def loadLatestRuleTemplate(self) -> dict[str, Any]:
        template = latest_rule_template()
        if not template:
            self._set_status("לא נמצאה תבנית כללים שמורה.")
            return self.ruleSettings()
        project_id = self._require_project()
        settings = {**DEFAULT_RULE_SETTINGS, **dict(template.get("settings", {}))}
        settings["hard_class_capacity"] = True
        settings["max_students_per_class"] = max(1, int(settings.get("max_students_per_class", 40) or 40))
        settings["max_students_per_gender"] = max(1, int(settings.get("max_students_per_gender", 20) or 20))
        current = self.database.get_project(project_id)
        if current:
            settings["ai_external_allowed"] = bool(current.settings.get("ai_external_allowed", False))
        self.project_service.update_settings(project_id, settings)
        self._set_status(f"נטענה תבנית כללים: {template.get('name', '')}")
        self.dataChanged.emit()
        return settings

    @Slot(result="QVariant")
    def ruleSettings(self) -> dict[str, Any]:
        if not self.current_project_id:
            return DEFAULT_RULE_SETTINGS
        project = self.database.get_project(self.current_project_id)
        return project.settings if project else DEFAULT_RULE_SETTINGS

    @Slot(result="QVariant")
    def aiRuleRecommendationData(self) -> dict[str, Any]:
        return self._ai_rule_recommendation

    @Slot(result="QVariant")
    def requestAiRuleRecommendationsAsync(self) -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        project_id = self._require_project()
        payload = self._ai_rule_recommendation_payload(project_id)
        fallback = "נוצרה המלצת כללים מקומית לפי מצב הנתונים והשיבוץ הנוכחי."
        allow_external = self._project_allows_external_ai()
        self._ai_rule_recommendation = {
            "status": "running",
            "used_ai": False,
            "source": "pending",
            "message": "AI בודק את הנתונים האנונימיים ומכין סט כללים מומלץ.",
            "providers": [],
            "recommendation": {},
        }
        self.aiRuleRecommendationChanged.emit()
        self._start_ai_rule_recommendation_request(
            (
                "נתח את כל הדאטה האנונימי של הפרויקט והמלץ על סט כללי שיבוץ מיטבי לקובץ הזה ספציפית. "
                "מקסימום תלמידים בכיתה ומקסימום מכל מגדר הם חוקי ברזל. "
                "פיזור בתי ספר מקור צריך להיות שווה ככל האפשר לפי floor/ceil לכל בית ספר. "
                "החזר סט הגדרות מלא ובר ביצוע בלבד, עם הסבר קצר למה הוא מתאים."
            ),
            payload,
            fallback,
            allow_external,
        )
        return {"started": True, "message": "בקשת המלצת הכללים התחילה."}

    @Slot(result="QVariant")
    def applyAiRuleRecommendation(self) -> dict[str, Any]:
        project_id = self._require_project()
        recommendation = dict(self._ai_rule_recommendation.get("recommendation", {}) or {})
        settings = recommendation.get("settings", {})
        if not isinstance(settings, dict) or not settings:
            return {"ok": False, "message": "אין המלצת כללים שאפשר להחיל כרגע."}
        current = dict(self.ruleSettings())
        clean = {**DEFAULT_RULE_SETTINGS, **current, **settings}
        clean["ai_external_allowed"] = bool(current.get("ai_external_allowed", False))
        clean["hard_class_capacity"] = True
        clean["max_students_per_class"] = max(1, int(clean.get("max_students_per_class", 40) or 40))
        clean["max_students_per_gender"] = max(1, int(clean.get("max_students_per_gender", 20) or 20))
        self.project_service.update_settings(project_id, clean)
        self._set_status("המלצת הכללים הוחלה ונשמרה בפרויקט.")
        self.dataChanged.emit()
        return {"ok": True, "message": "המלצת הכללים הוחלה."}

    @Slot(result="QVariant")
    def runAssignment(self) -> dict[str, Any]:
        project_id = self._require_project()
        result = self.assignment_service.run_assignment(project_id)
        self._set_status(f"השיבוץ הסתיים. ציון: {result['score']['total_score']}")
        self.dataChanged.emit()
        return result

    @Slot(result="QVariant")
    @Slot(int, bool, int, result="QVariant")
    def runAssignmentAsync(
        self,
        variant_count: int = 1,
        force_ai_review: bool = False,
        ai_provider_limit: int = 3,
    ) -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        project_id = self._require_project()
        requested_variant_count = max(1, min(24, int(variant_count or 1)))
        variant_count = self._effective_assignment_variant_count(project_id, requested_variant_count)
        return self._start_assignment_async(
            project_id=project_id,
            requested_variant_count=requested_variant_count,
            variant_count=variant_count,
            force_ai_review=force_ai_review,
            ai_provider_limit=ai_provider_limit,
            run_name="שיבוץ אוטומטי",
            run_mode="regular",
            settings_override=None,
            request_ai_actions=False,
        )

    @Slot(result="QVariant")
    def runMaxAssignmentAsync(self) -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        project_id = self._require_project()
        settings_override = _max_assignment_settings(self.ruleSettings())
        return self._start_assignment_async(
            project_id=project_id,
            requested_variant_count=MAX_ASSIGNMENT_VARIANTS,
            variant_count=MAX_ASSIGNMENT_VARIANTS,
            force_ai_review=True,
            ai_provider_limit=len(PROVIDER_ENV_VARS),
            run_name="הרצת MAX",
            run_mode="max",
            settings_override=settings_override,
            request_ai_actions=True,
        )

    def _start_assignment_async(
        self,
        *,
        project_id: int,
        requested_variant_count: int,
        variant_count: int,
        force_ai_review: bool,
        ai_provider_limit: int,
        run_name: str,
        run_mode: str,
        settings_override: dict[str, Any] | None,
        request_ai_actions: bool,
    ) -> dict[str, Any]:
        self._force_ai_review_after_assignment = bool(force_ai_review)
        self._request_ai_actions_after_assignment = bool(request_ai_actions)
        self._current_assignment_run_mode = run_mode
        self._ai_review_provider_limit = self._provider_limit(ai_provider_limit)
        self._last_ai_review = {
            "status": "waiting_for_assignment",
            "used_ai": False,
            "text": "ממתין לסיום השיבוץ לפני בדיקת AI.",
            "providers": [],
            "best": {},
        }
        self.aiReviewChanged.emit()
        cap_note = f"מנסה {variant_count} סידורים אפשריים."
        if run_mode == "max":
            cap_note = f"הרצת MAX: מנסה {variant_count} סידורים חזקים, שומרת את חמשת המובילים ומריצה סקירת AI אחרי התוצאה."
        self._set_busy(
            True,
            f"מריץ שיבוץ אוטומטי. {cap_note}",
            progress=0,
            progress_text="מתחיל להכין את נתוני השיבוץ.",
            estimated_seconds=self._estimated_assignment_seconds(project_id, variant_count, settings_override),
        )
        self._set_status("הרצת MAX רצה ברקע." if run_mode == "max" else "השיבוץ רץ ברקע.")
        thread = QThread(self)
        worker = AssignmentWorker(
            str(self.database.path),
            project_id,
            variant_count,
            run_name=run_name,
            settings_override=settings_override,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._assignment_worker_progress)
        worker.finished.connect(self._assignment_worker_finished)
        worker.failed.connect(self._assignment_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()
        return {"started": True, "message": "השיבוץ התחיל."}

    @Slot(result="QVariant")
    def dashboardData(self) -> dict[str, Any]:
        if not self.current_project_id:
            return {"has_assignment": False}
        return self._dashboard_data(self.current_project_id)

    @Slot(int, result="QVariant")
    def studentsForClass(self, class_id: int) -> list[dict[str, Any]]:
        if not self.current_project_id:
            return []
        dashboard = self._dashboard_data(self.current_project_id)
        rows = dashboard.get("rows", [])
        if int(class_id) <= 0:
            return rows
        return [row for row in rows if int(row["class_id"]) == int(class_id)]

    @Slot(int, result="QVariant")
    def studentDetails(self, student_id: int) -> dict[str, Any]:
        if not self.current_project_id:
            return {}
        return _student_details(
            self.database,
            self.current_project_id,
            student_id,
            dashboard=self._dashboard_data(self.current_project_id),
        )

    @Slot(int, result="QVariant")
    def studentDetailsAsync(self, student_id: int) -> dict[str, Any]:
        if not self.current_project_id:
            return {"started": False, "message": "אין פרויקט פעיל."}
        thread = QThread(self)
        worker = StudentDetailsWorker(str(self.database.path), self.current_project_id, int(student_id))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.studentDetailsLoaded.emit)
        worker.failed.connect(self.studentDetailsFailed.emit)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()
        return {"started": True}

    @Slot(int, "QVariant", result="QVariant")
    def updateStudent(self, student_id: int, fields: Any) -> dict[str, Any]:
        project_id = self._require_project()
        raw_fields = _variant_dict(fields)
        clean_fields = {
            "first_name": clean_text(raw_fields.get("first_name", "")),
            "last_name": clean_text(raw_fields.get("last_name", "")),
            "full_name": clean_text(raw_fields.get("full_name", "")),
            "gender": normalize_gender(raw_fields.get("gender", "")),
            "source_school": clean_text(raw_fields.get("source_school", "")),
            "average_grade": parse_grade(raw_fields.get("average_grade")),
            "behavior_score": normalize_behavior(raw_fields.get("behavior_score", "")),
            "dominance_score": parse_grade(raw_fields.get("dominance_score")),
            "teacher_notes": clean_text(raw_fields.get("teacher_notes", "")),
        }
        if not clean_fields["full_name"]:
            clean_fields["full_name"] = f"{clean_fields['first_name']} {clean_fields['last_name']}".strip()
        self.database.update_student_fields(project_id, student_id, clean_fields)
        self._refresh_validation(project_id)
        self._set_status("פרטי התלמיד/ה עודכנו ונבדקו מחדש.")
        self.dataChanged.emit()
        return self.studentDetails(student_id)

    @Slot(int, str, str, str, str, result="QVariant")
    def updateStudentConstraints(
        self,
        student_id: int,
        allowed_classes: str,
        forbidden_classes: str,
        must_be_with: str,
        must_not_be_with: str,
    ) -> dict[str, Any]:
        project_id = self._require_project()
        constraints = [
            item
            for item in self.database.get_class_constraints(project_id)
            if int(item["student_id"]) != student_id
        ]
        allowed = split_multi_value(allowed_classes)
        forbidden = split_multi_value(forbidden_classes)
        if allowed or forbidden:
            constraints.append(
                {
                    "student_id": student_id,
                    "allowed_classes": allowed,
                    "forbidden_classes": forbidden,
                    "locked_class_id": None,
                }
            )
        self.database.replace_class_constraints(project_id, constraints)

        pairs = self.database.get_pair_constraints(project_id)
        together = [
            (int(item["student_id"]), int(item["other_student_id"]), item.get("reason", ""))
            for item in pairs.get("together", [])
            if int(item["student_id"]) != student_id and int(item["other_student_id"]) != student_id
        ]
        separation = [
            (int(item["student_id"]), int(item["other_student_id"]), item.get("reason", ""))
            for item in pairs.get("separation", [])
            if int(item["student_id"]) != student_id and int(item["other_student_id"]) != student_id
        ]
        students = self.database.get_students(project_id)
        by_name = {normalize_name_key(student.display_name): student for student in students if student.id is not None}
        for name in split_multi_value(must_be_with):
            other = by_name.get(normalize_name_key(name))
            if other and other.id and other.id != student_id:
                together.append((student_id, other.id, "עריכה ידנית"))
        for name in split_multi_value(must_not_be_with):
            other = by_name.get(normalize_name_key(name))
            if other and other.id and other.id != student_id:
                separation.append((student_id, other.id, "עריכה ידנית"))
        self.database.replace_pair_constraints(project_id, together, separation)
        self._refresh_validation(project_id)
        self._set_status("אילוצי התלמיד/ה עודכנו.")
        self.dataChanged.emit()
        return self.studentDetails(student_id)

    @Slot(int, int, bool, result="QVariant")
    def moveStudent(self, student_id: int, class_id: int, lock: bool = False) -> dict[str, Any]:
        return self._start_manual_assignment_action(
            "move",
            {"student_id": student_id, "class_id": class_id, "lock": lock},
            "מעביר תלמיד/ה בין כיתות...",
        )

    @Slot(int, int, result="QVariant")
    def swapStudents(self, left_student_id: int, right_student_id: int) -> dict[str, Any]:
        return self._start_manual_assignment_action(
            "swap",
            {"left_student_id": left_student_id, "right_student_id": right_student_id},
            "מחליף/ה בין תלמידים...",
        )

    @Slot(int, bool, result=bool)
    def setStudentLock(self, student_id: int, locked: bool) -> bool:
        project_id = self._require_project()
        self.assignment_service.set_lock(project_id, student_id, locked)
        self._set_status("נעילת התלמיד/ה עודכנה.")
        self.dataChanged.emit()
        return True

    @Slot(result=bool)
    def undo(self) -> bool:
        project_id = self._require_project()
        ok = self.assignment_service.undo(project_id)
        self._set_status("Undo בוצע." if ok else "אין פעולה לביטול.")
        self.dataChanged.emit()
        return ok

    @Slot(result=bool)
    def redo(self) -> bool:
        project_id = self._require_project()
        ok = self.assignment_service.redo(project_id)
        self._set_status("Redo בוצע." if ok else "אין פעולה לשחזור.")
        self.dataChanged.emit()
        return ok

    @Slot(int, result=bool)
    def selectAssignmentVersion(self, version_id: int) -> bool:
        project_id = self._require_project()
        self.database.set_active_assignment_version(project_id, version_id)
        self.assignment_service.clear_history(project_id)
        self._set_status("גרסת השיבוץ הפעילה עודכנה.")
        self._reset_ai_action_suggestions("גרסת השיבוץ השתנתה. אפשר לבקש שוב הצעות AI לגרסה הפעילה.")
        self.dataChanged.emit()
        return True

    @Slot(int, str, result="QVariant")
    def renameAssignmentVersion(self, version_id: int, name: str) -> dict[str, Any]:
        project_id = self._require_project()
        try:
            self.database.rename_assignment_version(project_id, version_id, name)
        except Exception as exc:
            return {"ok": False, "message": str(exc)}
        self._set_status("שם ההרצה עודכן.")
        self.dataChanged.emit()
        return {"ok": True, "message": "שם ההרצה עודכן."}

    @Slot(result="QVariant")
    def qualityReport(self) -> dict[str, Any]:
        if not self.current_project_id:
            return {"has_assignment": False}
        return self._quality_report(self.current_project_id)

    @Slot(result="QVariant")
    def conflictsReport(self) -> dict[str, Any]:
        if not self.current_project_id:
            return _empty_conflicts_report("not_loaded", "אין פרויקט פתוח.")
        cached = self._conflicts_report_cache.get(self.current_project_id)
        if cached is not None:
            return _with_conflicts_report_status(cached, "ready", project_id=self.current_project_id)
        if self._conflicts_report_status.get("project_id") == self.current_project_id:
            return dict(self._conflicts_report_status)
        return _empty_conflicts_report("not_loaded", "דוח האילוצים עדיין לא נטען.", self.current_project_id)

    @Slot(result="QVariant")
    def loadConflictsReportAsync(self) -> dict[str, Any]:
        if not self.current_project_id:
            return {"started": False, "message": "אין פרויקט פתוח."}
        project_id = self._require_project()
        cached = self._conflicts_report_cache.get(project_id)
        if cached is not None:
            self._conflicts_report_status = _with_conflicts_report_status(cached, "ready", project_id=project_id)
            self.conflictsReportChanged.emit()
            return {"started": False, "message": "דוח האילוצים כבר מוכן."}
        if (
            self._conflicts_report_loading_project_id == project_id
            and self._conflicts_report_status.get("status") == "running"
        ):
            return {"started": False, "message": "דוח האילוצים כבר נטען ברקע."}
        self._start_conflicts_report_worker(project_id)
        return {"started": True, "message": "טעינת דוח האילוצים התחילה."}

    @Slot(result="QVariant")
    def aiActionSuggestionsData(self) -> dict[str, Any]:
        return self._ai_action_suggestions

    @Slot(result="QVariant")
    def requestAiActionSuggestionsAsync(self) -> dict[str, Any]:
        project_id = self._require_project()
        if not self._dashboard_data(project_id).get("has_assignment"):
            return {"started": False, "message": "אין שיבוץ פעיל לבדיקה."}
        try:
            candidates = self.assignment_service.action_suggestions(project_id, limit=72, exhaustive=True)
        except ValueError as exc:
            return {"started": False, "message": str(exc)}
        return self._request_ai_action_suggestions(
            project_id,
            candidates,
            no_candidates_message="אין כרגע העברה או החלפה מקומית שנראית משפרת. נראה שזה הכי טוב מבין הפעולות שנבדקו.",
            task="בחר עד 5 העברות או החלפות שמשפרות את ציון השיבוץ. בחר רק מזהים מתוך candidate_actions.",
        )

    @Slot(result="QVariant")
    def requestConflictAiActionSuggestionsAsync(self) -> dict[str, Any]:
        project_id = self._require_project()
        if not self._dashboard_data(project_id).get("has_assignment"):
            return {"started": False, "message": "אין שיבוץ פעיל לבדיקה."}
        report = self._conflicts_report_cache.get(project_id)
        if report is None:
            if (
                self._conflicts_report_loading_project_id != project_id
                or self._conflicts_report_status.get("status") != "running"
            ):
                self._start_conflicts_report_worker(project_id)
            return {"started": False, "message": "דוח האילוצים עדיין נטען. נסה שוב בעוד רגע."}
        focus_ids = [
            int(item.get("student_id", 0) or 0)
            for item in report.get("conflicts", [])
            if int(item.get("student_id", 0) or 0) > 0
        ]
        try:
            candidates = self.assignment_service.action_suggestions(
                project_id,
                focus_student_ids=list(dict.fromkeys(focus_ids)),
                limit=72,
                exhaustive=True,
            )
        except ValueError as exc:
            return {"started": False, "message": str(exc)}
        return self._request_ai_action_suggestions(
            project_id,
            candidates,
            no_candidates_message="אין כרגע העברה או החלפה ממוקדת אילוצים שנראית משפרת בלי להוסיף בעיות.",
            task=(
                "בחר עד 5 העברות או החלפות שמטפלות באילוצים או משפרות את ציון השיבוץ. "
                "בחר רק מזהים מתוך candidate_actions ואל תמציא פעולה חדשה."
            ),
        )

    def _request_ai_action_suggestions(
        self,
        project_id: int,
        candidates: list[dict[str, Any]],
        no_candidates_message: str,
        task: str,
    ) -> dict[str, Any]:
        improving = [
            _with_candidate_id(index, action)
            for index, action in enumerate(candidates, start=1)
            if int(action.get("hard_after", 0) or 0) <= int(action.get("hard_before", 0) or 0)
            and (
                float(action.get("delta", 0) or 0) > 0.05
                or int(action.get("hard_after", 0) or 0) < int(action.get("hard_before", 0) or 0)
                or int(action.get("friendship_missing_after", 0) or 0)
                < int(action.get("friendship_missing_before", 0) or 0)
            )
        ]
        if not improving:
            self._ai_action_suggestions = {
                "status": "no_candidates",
                "used_ai": False,
                "source": "local",
                "message": no_candidates_message,
                "actions": [],
                "providers": [],
            }
            self.aiActionSuggestionsChanged.emit()
            return {"started": False, "message": self._ai_action_suggestions["message"]}
        payload = self._ai_action_suggestions_payload(project_id, improving)
        allow_external = self._project_allows_external_ai()
        self._ai_action_suggestions = {
            "status": "running",
            "used_ai": False,
            "source": "pending",
            "message": "AI בודק את פעולות ההעברה וההחלפה המנוקדות.",
            "actions": [],
            "providers": [],
        }
        self.aiActionSuggestionsChanged.emit()
        self._start_ai_action_suggestions_request(
            task,
            payload,
            improving,
            allow_external,
        )
        return {"started": True, "message": "בקשת הצעות AI התחילה."}

    @Slot("QVariant", result="QVariant")
    def applySuggestedAction(self, action: Any) -> dict[str, Any]:
        if hasattr(action, "toVariant"):
            action = action.toVariant()
        if not isinstance(action, dict):
            return {"ok": False, "message": "הפעולה שנבחרה אינה תקינה."}
        project_id = self._require_project()
        action_type = str(action.get("action_type") or "")
        try:
            if action_type == "move":
                result = self.assignment_service.move_student(
                    project_id,
                    int(action.get("student_id", 0)),
                    int(action.get("target_class_id", 0)),
                    False,
                )
            elif action_type == "swap":
                result = self.assignment_service.swap_students(
                    project_id,
                    int(action.get("student_id", 0)),
                    int(action.get("other_student_id", 0)),
                )
            else:
                return {"ok": False, "message": "סוג פעולה לא נתמך."}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}
        self._set_status(result.get("note", "הפעולה בוצעה והשיבוץ עודכן."))
        self._reset_ai_action_suggestions("השיבוץ השתנה אחרי פעולה ידנית. אפשר לבקש שוב הצעות AI להרצה הפעילה.")
        self.dataChanged.emit()
        return {"ok": True, "message": result.get("note", "הפעולה בוצעה."), "result": result}

    @Slot(result="QVariant")
    def preflightReport(self) -> dict[str, Any]:
        if not self.current_project_id:
            return {"ok": False, "issues": [{"code": "NO_PROJECT", "severity": "critical", "message_he": "יש לפתוח פרויקט לפני בדיקת מוכנות."}]}
        return self.assignment_service.preflight_report(self.current_project_id)

    @Slot(result="QVariant")
    def friendshipDiagnosticData(self) -> dict[str, Any]:
        return self._friendship_diagnostic

    @Slot("QVariant", result="QVariant")
    def runFriendshipDiagnosticAsync(self, options: Any) -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        project_id = self._require_project()
        clean_options = _variant_dict(options)
        self._friendship_diagnostic = {
            "status": "running",
            "message": "בדיקת חברים רצה עכשיו.",
            "options": clean_options,
            "result": {},
        }
        self.friendshipDiagnosticChanged.emit()
        self._set_busy(
            True,
            "מריץ בדיקת חברים...",
            progress=0,
            progress_text="בודק אם אפשר להגיע ל-100% חברים לפי האילוצים שסומנו.",
            estimated_seconds=60,
        )
        self._set_status("בדיקת חברים רצה ברקע.")
        thread = QThread(self)
        worker = FriendshipDiagnosticWorker(str(self.database.path), project_id, clean_options)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._assignment_worker_progress)
        worker.finished.connect(self._friendship_diagnostic_finished)
        worker.failed.connect(self._friendship_diagnostic_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()
        return {"started": True, "message": "בדיקת החברים התחילה."}

    @Slot(int, int, result="QVariant")
    def compareVersions(self, left_version_id: int, right_version_id: int) -> dict[str, Any]:
        project_id = self._require_project()
        return self.report_service.compare_versions(project_id, left_version_id, right_version_id)

    @Slot(str, result=str)
    def exportExcel(self, file_url: str) -> str:
        project_id = self._require_project()
        path = self.export_service.export_project(project_id, _path_from_url(file_url))
        self._set_status("קובץ הייצוא נשמר.")
        return str(path)

    @Slot(str, result="QVariant")
    def exportExcelAsync(self, file_url: str) -> dict[str, Any]:
        return self._start_export_worker(file_url, validation_only=False)

    @Slot(str, result=str)
    def exportValidationIssuesExcel(self, file_url: str) -> str:
        project_id = self._require_project()
        path = self.export_service.export_validation_issues(project_id, _path_from_url(file_url))
        self._set_status("קובץ שגיאות בדיקת הנתונים נשמר.")
        return str(path)

    @Slot(str, result="QVariant")
    def exportValidationIssuesExcelAsync(self, file_url: str) -> dict[str, Any]:
        return self._start_export_worker(file_url, validation_only=True)

    def _start_export_worker(self, file_url: str, validation_only: bool = False) -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        project_id = self._require_project()
        path = _path_from_url(file_url)
        self._set_busy(
            True,
            "מייצא קובץ Excel...",
            progress=0,
            progress_text="מכין גיליונות, נתוני כיתות ודוחות.",
            estimated_seconds=30,
        )
        thread = QThread(self)
        worker = ExportWorker(str(self.database.path), project_id, path, validation_only)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._export_worker_finished)
        worker.failed.connect(self._export_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()
        return {"started": True, "message": "הייצוא התחיל."}

    @Slot(result=str)
    def anonymizedPayloadPreview(self) -> str:
        project_id = self._require_project()
        students = self.database.get_students(project_id)
        active = self.database.get_active_assignment_version(project_id)
        assignments: dict[int, int] = {}
        if active:
            assignments = {
                int(row["student_id"]): int(row["class_id"])
                for row in self.database.get_assignments(int(active["id"]))
            }
        class_names = {int(group.id): group.name for group in self.database.get_classes(project_id) if group.id}
        payload = anonymize_assignment_payload(students, assignments, class_names)
        return json.dumps(payload["payload"], ensure_ascii=False, indent=2)

    @Slot(result="QVariant")
    def aiSettings(self) -> dict[str, Any]:
        return load_ai_settings()

    @Slot(str, str, result=str)
    def saveAiToken(self, provider: str, token: str) -> str:
        if not token.strip():
            self._set_status("לא נשמר מפתח ריק.")
            return ""
        save_ai_token(provider, token, mirror_to_project=False)
        self._set_status("מפתח ה-AI נשמר מקומית במחשב הזה ולא בתוך תיקיית הפרויקט.")
        return "המפתח נשמר מקומית במחשב הזה. אפשר עכשיו ללחוץ על בדיקת חיבור."

    @Slot(bool, str, result=str)
    def saveAiPreferences(self, enabled: bool, provider: str) -> str:
        save_ai_preferences(enabled, provider, mirror_to_project=False)
        self._set_status("העדפות AI נשמרו מקומית.")
        return "העדפות AI נשמרו."

    @Slot(str, result="QVariant")
    def testAiConnection(self, provider: str) -> dict[str, Any]:
        result = self.ai_client.test_connection(provider)
        self._set_status(result.get("message", "בדיקת חיבור הסתיימה."))
        self._record_ai_connection_results([{"provider": provider, **result}])
        return result

    @Slot(result="QVariant")
    def testAllAiConnections(self) -> dict[str, Any]:
        settings = load_ai_settings()
        results = []
        for provider in settings.get("providers", []):
            provider_name = provider.get("provider", "")
            if not provider.get("configured"):
                results.append(
                    {
                        "provider": provider_name,
                        "ok": False,
                        "message": "לא מוגדר מפתח AI לספק.",
                        "source": "",
                    }
                )
                continue
            result = self.ai_client.test_connection(provider_name)
            results.append({"provider": provider_name, **result})
        ok_count = sum(1 for item in results if item.get("ok"))
        self._set_status(f"בדיקת AI הסתיימה: {ok_count}/{len(results)} שירותי AI זמינים.")
        self._record_ai_connection_results(results)
        return {"ok_count": ok_count, "total": len(results), "providers": results}

    @Slot(result="QVariant")
    def findSuitableAiModels(self) -> dict[str, Any]:
        settings = load_ai_settings()
        results = []
        client = AiClient(timeout_seconds=12)
        for provider in settings.get("providers", []):
            provider_name = provider.get("provider", "")
            if not provider.get("configured"):
                results.append(
                    {
                        "provider": provider_name,
                        "ok": False,
                        "used": False,
                        "message": "לא מוגדר מפתח AI לספק.",
                        "attempts": [],
                    }
                )
                continue

            attempts = []
            selected: str | None = None
            selected_message = ""
            for model in provider_model_candidates(provider_name):
                result = client.test_connection(provider_name, model=model)
                attempts.append(
                    {
                        "model": model,
                        "ok": bool(result.get("ok")),
                        "message": result.get("message", ""),
                    }
                )
                if result.get("ok"):
                    selected = model
                    selected_message = result.get("message", "OK")
                    save_ai_model(provider_name, model, mirror_to_project=False)
                    break

            results.append(
                {
                    "provider": provider_name,
                    "ok": bool(selected),
                    "used": True,
                    "model": selected or provider.get("model", ""),
                    "message": (
                        f"נמצא מודל מתאים: {selected}" if selected else "לא נמצא מודל זמין ברשימת המועמדים."
                    ),
                    "test_message": selected_message,
                    "attempts": attempts,
                }
            )

        ok_count = sum(1 for item in results if item.get("ok"))
        total_configured = sum(1 for item in results if item.get("used"))
        self._set_status(f"איתור מודלים הסתיים: {ok_count}/{total_configured} ספקים מוגדרים עברו בהצלחה.")
        self._record_ai_connection_results(results)
        return {
            "ok_count": ok_count,
            "total_configured": total_configured,
            "providers": results,
            "settings": load_ai_settings(),
        }

    @Slot(str, result="QVariant")
    def testAiConnectionAsync(self, provider: str) -> dict[str, Any]:
        return self._start_ai_connection_test("single", provider)

    @Slot(result="QVariant")
    def testAllAiConnectionsAsync(self) -> dict[str, Any]:
        return self._start_ai_connection_test("all")

    @Slot(result="QVariant")
    def findSuitableAiModelsAsync(self) -> dict[str, Any]:
        return self._start_ai_connection_test("models")

    def _start_ai_connection_test(self, mode: str, provider: str = "") -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        labels = {
            "single": "בודק חיבור AI.",
            "all": "בודק את כל חיבורי ה-AI.",
            "models": "בודק מודלים זמינים אצל ספקי AI.",
        }
        self._set_busy(
            True,
            labels.get(mode, "בודק AI."),
            progress=0,
            progress_text="שולח בדיקת חיבור ברקע.",
            estimated_seconds=75 if mode == "models" else 30,
        )
        self._set_status(labels.get(mode, "בודק AI."))
        thread = QThread(self)
        worker = AiConnectionTestWorker(mode, provider)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._ai_connection_test_finished)
        worker.failed.connect(self._ai_connection_test_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()
        return {"started": True, "message": labels.get(mode, "בדיקת AI התחילה.")}

    def _ai_connection_test_finished(self, result: Any) -> None:
        clean = dict(result)
        providers = clean.get("providers", []) if isinstance(clean.get("providers", []), list) else []
        self._record_ai_connection_results(providers)
        message = clean.get("message") or "בדיקת AI הסתיימה."
        self._set_status(str(message))
        self._set_busy(False, "")
        self.aiConnectionTestFinished.emit(clean)

    def _ai_connection_test_failed(self, message: str) -> None:
        result = {
            "mode": "failed",
            "ok_count": 0,
            "total": 0,
            "providers": [],
            "message": f"בדיקת AI נכשלה: {message}",
            "settings": load_ai_settings(),
        }
        self._set_status(result["message"])
        self._set_busy(False, "")
        self.aiConnectionTestFinished.emit(result)

    @Slot(str, bool, result="QVariant")
    def askAiAssistant(self, task: str, approved_to_send: bool = False) -> dict[str, Any]:
        project_id = self._require_project()
        report = self.report_service.quality_report(project_id)
        payload = self._assistant_payload(project_id, "assistant", task, report)
        fallback = self._local_ai_fallback(task, report)
        if not self._project_allows_external_ai():
            return {"used_ai": False, "text": fallback + "\n\nשליחה ל-AI אינה מאושרת בפרויקט הזה, לכן נוצר הסבר מקומי בלבד.", "source": "local", "payload": payload}
        result = self.ai_client.complete(task, payload, fallback)
        self._set_status("התקבלה תשובת AI." if result.get("used_ai") else "נוצרה תשובה מקומית.")
        return result

    @Slot(str, str, bool, result="QVariant")
    def askAiAssistantAsync(self, request_id: str, task: str, approved_to_send: bool = False) -> dict[str, Any]:
        project_id = self._require_project()
        self._start_assistant_request(
            request_id,
            task,
            None,
            None,
            bool(approved_to_send and self._project_allows_external_ai()),
            project_id=project_id,
        )
        return {"started": True, "message": "בקשת AI התחילה."}

    @Slot(str, result="QVariant")
    def assistantResult(self, request_id: str) -> dict[str, Any]:
        return self._assistant_results.get(request_id, {})

    @Slot(result="QVariant")
    def aiReviewData(self) -> dict[str, Any]:
        return self._last_ai_review

    def _assistant_payload(
        self,
        project_id: int,
        request_id: str,
        task: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        return _build_assistant_payload(self.database, project_id, request_id, task, report)

    def _conflict_payload(self, project_id: int) -> dict[str, Any]:
        return _build_conflict_payload(self.database, project_id)

    def _ai_action_suggestions_payload(self, project_id: int, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        report = self._quality_report(project_id)
        students = self.database.get_students(project_id)
        student_refs = {
            int(student.id): f"S{index:03d}"
            for index, student in enumerate(sorted(students, key=lambda item: int(item.id or 0)), start=1)
            if student.id is not None
        }
        safe_candidates = []
        for item in candidates:
            safe_candidates.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "action_type": item.get("action_type"),
                    "student_ref": student_refs.get(int(item.get("student_id", 0) or 0), ""),
                    "other_student_ref": student_refs.get(int(item.get("other_student_id", 0) or 0), ""),
                    "from_class": item.get("from_class_name", ""),
                    "target_class": item.get("target_class_name", ""),
                    "other_from_class": item.get("other_from_class_name", ""),
                    "other_target_class": item.get("other_target_class_name", ""),
                    "score_before": item.get("score_before"),
                    "score_after": item.get("score_after"),
                    "delta": item.get("delta"),
                    "hard_before": item.get("hard_before"),
                    "hard_after": item.get("hard_after"),
                    "friendship_missing_before": item.get("friendship_missing_before"),
                    "friendship_missing_after": item.get("friendship_missing_after"),
                    "friendship_gain": item.get("friendship_gain"),
                    "cost_he": item.get("cost", ""),
                }
            )
        return {
            "privacy": {
                "contains_student_names": False,
                "contains_notes": False,
                "student_references_are_anonymous": True,
            },
            "current_assignment": {
                "total_score": report.get("total_score"),
                "penalties": report.get("penalties", {}),
                "hard_violations_count": len(report.get("hard_violations", [])),
                "missing_friends_count": len(report.get("missing_friends", [])),
                "isolated_students_count": len(report.get("isolated_students", [])),
                "class_stats": _safe_class_stats(report.get("class_stats", [])),
            },
            "candidate_actions": safe_candidates,
            "instruction": "בחר רק candidate_id מתוך candidate_actions. אל תמציא פעולה חדשה.",
        }

    def _ai_rule_recommendation_payload(self, project_id: int) -> dict[str, Any]:
        settings = self.ruleSettings()
        students = self.database.get_students(project_id)
        classes = self.database.get_classes(project_id)
        try:
            report = self._quality_report(project_id)
        except Exception:
            report = {"has_assignment": False}
        try:
            preflight = self.assignment_service.preflight_report(project_id)
        except Exception as exc:
            preflight = {"ok": False, "issues": [{"code": "PREFLIGHT_ERROR", "severity": "critical", "message_he": str(exc)}]}
        missing_gender = sum(1 for student in students if not student.gender)
        missing_grade = sum(1 for student in students if student.grade_value is None)
        missing_behavior = sum(1 for student in students if not student.behavior_score)
        missing_school = sum(1 for student in students if not student.source_school)
        issues = preflight.get("issues", []) if isinstance(preflight, dict) else []
        friendships = self.database.get_friendships(project_id)
        class_constraints = {
            int(item["student_id"]): item
            for item in self.database.get_class_constraints(project_id)
            if item.get("student_id") is not None
        }
        pair_constraints = self.database.get_pair_constraints(project_id)
        student_refs = {
            int(student.id): f"S{index:03d}"
            for index, student in enumerate(sorted(students, key=lambda item: int(item.id or 0)), start=1)
            if student.id is not None
        }
        school_counts = Counter(student.source_school for student in students if student.source_school)
        school_refs = {
            school: f"School{index:03d}"
            for index, (school, _count) in enumerate(sorted(school_counts.items(), key=lambda item: (-item[1], item[0])), start=1)
        }
        requested_by_student: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in friendships:
            student_id = int(item.get("student_id", 0) or 0)
            friend_id = int(item.get("requested_friend_id", 0) or 0)
            if student_id in student_refs and friend_id in student_refs:
                requested_by_student[student_id].append(
                    {"friend_ref": student_refs[friend_id], "priority": int(item.get("priority", 1) or 1)}
                )
        together_by_student: dict[int, list[str]] = defaultdict(list)
        separation_by_student: dict[int, list[str]] = defaultdict(list)
        for item in pair_constraints.get("together", []):
            left = int(item.get("student_id", 0) or 0)
            right = int(item.get("other_student_id", 0) or 0)
            if left in student_refs and right in student_refs:
                together_by_student[left].append(student_refs[right])
                together_by_student[right].append(student_refs[left])
        for item in pair_constraints.get("separation", []):
            left = int(item.get("student_id", 0) or 0)
            right = int(item.get("other_student_id", 0) or 0)
            if left in student_refs and right in student_refs:
                separation_by_student[left].append(student_refs[right])
                separation_by_student[right].append(student_refs[left])
        active = self.database.get_active_assignment_version(project_id)
        active_assignments: dict[int, int] = {}
        if active:
            active_assignments = {
                int(row["student_id"]): int(row["class_id"])
                for row in self.database.get_assignments(int(active["id"]))
            }
        class_name_by_id = {int(group.id): group.name for group in classes if group.id}
        anonymized_students = []
        for student in sorted(students, key=lambda item: int(item.id or 0)):
            if student.id is None:
                continue
            student_id = int(student.id)
            constraint = class_constraints.get(student_id, {})
            anonymized_students.append(
                {
                    "student_ref": student_refs[student_id],
                    "gender": student.gender,
                    "source_school_ref": school_refs.get(student.source_school, ""),
                    "average_grade": _round_optional(student.grade_value),
                    "math_grade": _round_optional(student.math_grade),
                    "english_grade": _round_optional(student.english_grade),
                    "hebrew_grade": _round_optional(student.hebrew_grade),
                    "behavior": normalize_behavior(student.behavior_score) if student.behavior_score else "",
                    "dominance_score": _round_optional(student.dominance_score),
                    "friend_requests": requested_by_student.get(student_id, []),
                    "allowed_classes": constraint.get("allowed_classes", []),
                    "forbidden_classes": constraint.get("forbidden_classes", []),
                    "locked_class_id": constraint.get("locked_class_id"),
                    "must_be_with": together_by_student.get(student_id, []),
                    "must_not_be_with": separation_by_student.get(student_id, []),
                    "current_class": class_name_by_id.get(active_assignments.get(student_id), ""),
                }
            )
        source_school_distribution = [
            {
                "school_ref": school_refs[school],
                "student_count": count,
                "ideal_per_class": round(count / max(1, len(classes)), 2),
                "target_floor": count // max(1, len(classes)),
                "target_ceiling": ceil_div(count, max(1, len(classes))),
            }
            for school, count in sorted(school_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        return {
            "schema_version": 1,
            "task": "rule_recommendation",
            "system_explanation": {
                "product": "Mosaicly creates class assignments for a school grade.",
                "hard_rules": [
                    "Never exceed max_students_per_class when configured.",
                    "Never exceed max_students_per_gender per class when configured.",
                    "Respect locked classes, allowed classes, forbidden classes, must-be-with and must-not-be-with constraints.",
                ],
                "soft_goals": [
                    "Keep class sizes as equal as possible.",
                    "Balance gender ratios.",
                    "Balance average grades and subject grades.",
                    "Distribute behavior and dominance load.",
                    "Give students at least one requested friend when possible.",
                    "Distribute source schools evenly: if 12 students come from one school and there are 6 classes, aim for about 2 per class, not 6 and 6.",
                ],
                "requested_output": "Recommend practical settings only. Do not invent students or classes. Prefer settings that are feasible for this exact anonymized dataset.",
            },
            "privacy": {
                "contains_student_names": False,
                "contains_notes": False,
                "contains_raw_rows": False,
                "student_references_are_anonymous": True,
            },
            "class_names": [group.name for group in classes],
            "current_settings": _settings_for_ai_rules(settings),
            "data_summary": {
                "student_count": len(students),
                "class_count": len(classes),
                "missing_gender_count": missing_gender,
                "missing_grade_count": missing_grade,
                "missing_behavior_count": missing_behavior,
                "missing_source_school_count": missing_school,
                "friendship_request_count": len(self.database.get_friendships(project_id)),
            },
            "source_school_distribution": source_school_distribution,
            "students_anonymized": anonymized_students,
            "assignment_summary": {
                "has_assignment": bool(report.get("has_assignment", False)),
                "total_score": report.get("total_score"),
                "penalties": report.get("penalties", {}),
                "hard_violations_count": len(report.get("hard_violations", [])),
                "missing_friends_count": len(report.get("missing_friends", [])),
                "isolated_students_count": len(report.get("isolated_students", [])),
                "class_stats": _safe_class_stats(report.get("class_stats", [])),
            },
            "preflight_summary": {
                "ok": bool(preflight.get("ok", False)) if isinstance(preflight, dict) else False,
                "critical_count": sum(1 for issue in issues if issue.get("severity") == "critical"),
                "warning_count": sum(1 for issue in issues if issue.get("severity") == "warning"),
                "issue_codes": [str(issue.get("code", "")) for issue in issues[:12]],
            },
        }

    def _clear_ui_caches(self) -> None:
        self._dashboard_cache.clear()
        self._quality_report_cache.clear()
        self._conflicts_report_cache.clear()
        self._conflicts_report_request_token += 1
        self._conflicts_report_loading_project_id = None
        self._conflicts_report_status = _empty_conflicts_report(
            "not_loaded",
            "דוח האילוצים עדיין לא נטען.",
            self.current_project_id,
        )

    def _dashboard_data(self, project_id: int) -> dict[str, Any]:
        cached = self._dashboard_cache.get(project_id)
        if cached is None:
            cached = self.assignment_service.dashboard(project_id)
            self._dashboard_cache[project_id] = cached
        return cached

    def _quality_report(self, project_id: int) -> dict[str, Any]:
        cached = self._quality_report_cache.get(project_id)
        if cached is None:
            cached = self.report_service.quality_report(project_id)
            self._quality_report_cache[project_id] = cached
        return cached

    def _conflicts_report(self, project_id: int) -> dict[str, Any]:
        cached = self._conflicts_report_cache.get(project_id)
        if cached is not None:
            return cached
        report = _build_conflicts_report(self.database, project_id)
        self._conflicts_report_cache[project_id] = report
        return report

    def _project_allows_external_ai(self) -> bool:
        if not self.current_project_id:
            return False
        project = self.database.get_project(self.current_project_id)
        return bool(project and project.settings.get("ai_external_allowed", False))

    def _require_project(self) -> int:
        if not self.current_project_id:
            raise ValueError("יש ליצור או לפתוח פרויקט קודם.")
        return self.current_project_id

    def _set_status(self, status: str) -> None:
        self._status = status
        self.statusChanged.emit()

    def _set_busy(
        self,
        busy: bool,
        text: str = "",
        progress: float = 0.0,
        progress_text: str = "",
        estimated_seconds: float | None = None,
    ) -> None:
        self._busy = busy
        self._busy_text = text
        self._busy_progress = max(0.0, min(100.0, float(progress))) if busy else 0.0
        self._busy_started_at = time.monotonic() if busy else None
        self._busy_last_progress_message = progress_text or text
        self._busy_initial_estimate_seconds = max(1.0, float(estimated_seconds)) if busy and estimated_seconds else None
        self._busy_eta_seconds = self._busy_initial_estimate_seconds
        self._busy_progress_samples = [(self._busy_started_at, self._busy_progress)] if busy and self._busy_started_at is not None else []
        self._busy_progress_text = self._progress_text_with_eta(self._busy_last_progress_message, self._busy_progress) if busy else ""
        if busy:
            self._busy_timer.start()
        else:
            self._busy_timer.stop()
        self.busyChanged.emit()

    def _set_busy_progress(self, progress: float, text: str = "") -> None:
        now = time.monotonic()
        clean_progress = max(0.0, min(100.0, float(progress)))
        self._busy_progress = clean_progress
        if text:
            self._busy_last_progress_message = text
        if self._busy:
            if not self._busy_progress_samples or now - self._busy_progress_samples[-1][0] >= 0.5:
                self._busy_progress_samples.append((now, clean_progress))
                self._busy_progress_samples = self._busy_progress_samples[-12:]
        self._busy_progress_text = self._progress_text_with_eta(self._busy_last_progress_message, self._busy_progress)
        self.busyChanged.emit()

    def _tick_busy_eta(self) -> None:
        if not self._busy:
            return
        self._busy_progress_text = self._progress_text_with_eta(self._busy_last_progress_message, self._busy_progress)
        self.busyChanged.emit()

    def _forget_thread(self, thread: QThread) -> None:
        self._threads = [(active_thread, worker) for active_thread, worker in self._threads if active_thread is not thread]

    def _start_conflicts_report_worker(self, project_id: int) -> None:
        self._conflicts_report_request_token += 1
        request_token = self._conflicts_report_request_token
        self._conflicts_report_loading_project_id = project_id
        self._conflicts_report_status = _empty_conflicts_report(
            "running",
            "טוען דוח אילוצים והצעות תיקון...",
            project_id,
        )
        self.conflictsReportChanged.emit()
        thread = QThread(self)
        worker = ConflictsReportWorker(str(self.database.path), project_id, request_token)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._conflicts_report_worker_finished)
        worker.failed.connect(self._conflicts_report_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()

    def _conflicts_report_worker_finished(self, project_id: int, request_token: int, result: Any) -> None:
        if request_token != self._conflicts_report_request_token:
            return
        report = dict(result or {})
        self._conflicts_report_cache[project_id] = report
        if project_id == self.current_project_id:
            self._conflicts_report_status = _with_conflicts_report_status(report, "ready", project_id=project_id)
            self._conflicts_report_loading_project_id = None
            self.conflictsReportChanged.emit()

    def _conflicts_report_worker_failed(self, project_id: int, request_token: int, message: str) -> None:
        if request_token != self._conflicts_report_request_token:
            return
        if project_id == self.current_project_id:
            self._conflicts_report_status = _empty_conflicts_report(
                "failed",
                f"טעינת דוח האילוצים נכשלה: {message}",
                project_id,
            )
            self._conflicts_report_loading_project_id = None
            self.conflictsReportChanged.emit()

    def _start_manual_assignment_action(self, action: str, payload: dict[str, Any], busy_text: str) -> dict[str, Any]:
        if self._busy:
            return {"started": False, "message": "כבר מתבצעת פעולה. נא להמתין לסיום."}
        project_id = self._require_project()
        self._set_busy(
            True,
            busy_text,
            progress=0,
            progress_text="מחשב מחדש את ציון השיבוץ ושומר את השינוי ברקע.",
            estimated_seconds=18,
        )
        self._set_status(busy_text)
        thread = QThread(self)
        worker = ManualAssignmentActionWorker(str(self.database.path), project_id, action, payload)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._manual_assignment_action_finished)
        worker.failed.connect(self._manual_assignment_action_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()
        return {"started": True, "message": busy_text}

    def _manual_assignment_action_finished(self, action: str, result: Any) -> None:
        data = dict(result or {})
        self._set_status(str(data.get("note") or "השיבוץ עודכן."))
        self._reset_ai_action_suggestions("השיבוץ השתנה אחרי פעולה ידנית. אפשר לבקש שוב הצעות AI להרצה הפעילה.")
        self.dataChanged.emit()
        self.assignmentFinished.emit()
        self._set_busy(False, "")

    def _manual_assignment_action_failed(self, message: str) -> None:
        self._set_status(f"הפעולה הידנית נכשלה: {message}")
        self._set_busy(False, "")
        self.dataChanged.emit()

    def _reset_friendship_diagnostic(self, message: str = "בדיקת חברים עדיין לא הורצה.") -> None:
        if getattr(self, "_friendship_diagnostic", {}).get("status") == "running":
            return
        self._friendship_diagnostic = {
            "status": "not_run",
            "message": message,
            "result": {},
        }
        self.friendshipDiagnosticChanged.emit()

    def _friendship_diagnostic_finished(self, result: Any) -> None:
        data = dict(result or {})
        self._friendship_diagnostic = {
            "status": "done",
            "message": str(data.get("summary") or "בדיקת החברים הסתיימה."),
            "result": data,
            "options": data.get("options", {}),
        }
        self._set_status(self._friendship_diagnostic["message"])
        self.friendshipDiagnosticChanged.emit()
        self._set_busy(False, "")

    def _friendship_diagnostic_failed(self, message: str) -> None:
        self._friendship_diagnostic = {
            "status": "failed",
            "message": message,
            "result": {},
        }
        self._set_status(f"בדיקת החברים נכשלה: {message}")
        self.friendshipDiagnosticChanged.emit()
        self._set_busy(False, "")

    def _assignment_worker_progress(self, percent: float, message: str) -> None:
        self._set_busy_progress(percent, message)

    def _progress_text_with_eta(self, text: str, progress: float) -> str:
        message = text or self._busy_last_progress_message or "הפעולה מתבצעת ברקע."
        if not self._busy_started_at:
            return message
        elapsed = max(0.0, time.monotonic() - self._busy_started_at)
        remaining = self._estimated_remaining_seconds(elapsed, progress)
        lines = [message, f"עברו {_format_duration(elapsed)}"]
        if remaining is None:
            lines.append("מחשב זמן משוער לפי קצב ההתקדמות בפועל.")
        elif remaining <= 1:
            lines.append("כמעט הסתיים.")
        else:
            finish_at = datetime.now() + timedelta(seconds=remaining)
            lines.append(f"נשאר בערך {_format_duration(remaining)} · סיום משוער {finish_at.strftime('%H:%M')}")
        return "\n".join(lines)

    def _estimated_remaining_seconds(self, elapsed: float, progress: float) -> float | None:
        if progress >= 99.5:
            return 0.0
        estimates: list[float] = []
        if self._busy_initial_estimate_seconds is not None:
            initial_remaining = max(0.0, self._busy_initial_estimate_seconds - elapsed)
            estimates.append(initial_remaining)
            if elapsed < 10 or progress < 14:
                current = initial_remaining
                if self._busy_eta_seconds is not None:
                    current = min(current, max(0.0, self._busy_eta_seconds - 0.25))
                self._busy_eta_seconds = max(0.0, current)
                return self._busy_eta_seconds
        if progress > 8 and elapsed >= 8:
            total_estimate = elapsed / max(progress / 100.0, 0.01)
            estimates.append(max(0.0, total_estimate - elapsed))
        if len(self._busy_progress_samples) >= 2 and elapsed >= 10:
            older_time, older_progress = self._busy_progress_samples[0]
            newer_time, newer_progress = self._busy_progress_samples[-1]
            progress_delta = newer_progress - older_progress
            time_delta = newer_time - older_time
            if progress_delta > 0.2 and time_delta > 0:
                speed = progress_delta / time_delta
                estimates.append(max(0.0, (100.0 - progress) / speed))
        if not estimates:
            return None
        ordered = sorted(estimates)
        current = ordered[min(len(ordered) - 1, int(len(ordered) * 0.75))]
        if self._busy_initial_estimate_seconds is not None and progress < 85:
            current = max(current, max(0.0, (self._busy_initial_estimate_seconds - elapsed) * 0.55))
        if self._busy_eta_seconds is not None:
            if current > self._busy_eta_seconds and elapsed < 45:
                current = self._busy_eta_seconds
            current = (self._busy_eta_seconds * 0.45) + (current * 0.55)
        self._busy_eta_seconds = max(0.0, current)
        return self._busy_eta_seconds

    def _start_assistant_request(
        self,
        request_id: str,
        task: str,
        payload: dict[str, Any] | None,
        fallback: str | None,
        allow_external: bool,
        project_id: int | None = None,
    ) -> None:
        self._assistant_results[request_id] = {
            "used_ai": False,
            "source": "pending",
            "text": "מכין תשובה...",
            "payload": payload or {},
        }
        thread = QThread(self)
        worker = AssistantWorker(
            request_id,
            task,
            payload,
            fallback,
            allow_external,
            str(self.database.path) if project_id else "",
            int(project_id or 0),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._assistant_worker_finished)
        worker.failed.connect(self._assistant_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()

    def _assistant_worker_finished(self, request_id: str, result: Any) -> None:
        self._assistant_results[request_id] = dict(result)
        self._set_status("התקבלה תשובת AI." if self._assistant_results[request_id].get("used_ai") else "נוצרה תשובה מקומית.")
        self.assistantFinished.emit(request_id)

    def _assistant_worker_failed(self, request_id: str, message: str) -> None:
        self._assistant_results[request_id] = {
            "used_ai": False,
            "source": "local",
            "text": f"קריאת AI נכשלה: {message}",
            "error": message,
            "payload": {},
        }
        print(f"[Mosaicly AI] assistant request failed. request={request_id}. reason={message}", flush=True)
        self._set_status("קריאת AI נכשלה.")
        self.assistantFinished.emit(request_id)

    def _preview_worker_finished(self, table: Any, mapping: Any) -> None:
        self.import_service.current_table = table
        self.import_service.current_mapping = dict(mapping or {})
        self._set_status(f"נטען קובץ עם {table.row_count} שורות.")
        self.previewChanged.emit()
        self._set_busy(False, "")

    def _preview_worker_failed(self, message: str) -> None:
        self._set_status(f"טעינת הקובץ נכשלה: {message}")
        self._set_busy(False, "")

    def _import_worker_finished(self, result: Any) -> None:
        data = dict(result or {})
        self._set_status(f"יובאו {data.get('students_count', 0)} תלמידים.")
        self.dataChanged.emit()
        self.previewChanged.emit()
        self._set_busy(False, "")

    def _import_worker_failed(self, message: str) -> None:
        self._set_status(f"ייבוא התלמידים נכשל: {message}")
        self._set_busy(False, "")

    def _export_worker_finished(self, path: str) -> None:
        self._set_status(f"קובץ הייצוא נשמר: {path}")
        self._set_busy(False, "")

    def _export_worker_failed(self, message: str) -> None:
        self._set_status(f"ייצוא הקובץ נכשל: {message}")
        self._set_busy(False, "")

    def _start_ai_action_suggestions_request(
        self,
        task: str,
        payload: dict[str, Any],
        candidates: list[dict[str, Any]],
        allow_external: bool,
    ) -> None:
        thread = QThread(self)
        worker = AiActionSuggestionWorker(task, payload, candidates, allow_external, self._provider_limit())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._ai_action_suggestions_finished)
        worker.failed.connect(self._ai_action_suggestions_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()

    def _start_ai_rule_recommendation_request(
        self,
        task: str,
        payload: dict[str, Any],
        fallback: str,
        allow_external: bool,
    ) -> None:
        thread = QThread(self)
        worker = AiRuleRecommendationWorker(task, payload, fallback, allow_external, self._provider_limit())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._ai_rule_recommendation_finished)
        worker.failed.connect(self._ai_rule_recommendation_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()

    def _ai_action_suggestions_finished(self, result: Any) -> None:
        self._ai_action_suggestions = dict(result)
        if self._ai_action_suggestions.get("actions"):
            label = "AI" if self._ai_action_suggestions.get("used_ai") else "מקומיות"
            self._set_status(f"התקבלו {len(self._ai_action_suggestions.get('actions', []))} הצעות {label} לשיפור.")
        elif self._ai_action_suggestions.get("used_ai"):
            self._set_status("AI לא מצא פעולה משפרת מתוך המועמדים.")
        else:
            self._set_status("הצעות AI לא זמינות כרגע.")
        self.aiActionSuggestionsChanged.emit()

    def _ai_action_suggestions_failed(self, message: str) -> None:
        self._ai_action_suggestions = {
            "status": "ai_failed",
            "used_ai": False,
            "source": "local",
            "message": f"קריאת AI להצעות שיפור נכשלה: {message}",
            "actions": [],
            "providers": [],
        }
        print(f"[Mosaicly AI] action suggestions failed. reason={message}", flush=True)
        self._set_status("קריאת AI להצעות שיפור נכשלה.")
        self.aiActionSuggestionsChanged.emit()

    def _ai_rule_recommendation_finished(self, result: Any) -> None:
        self._ai_rule_recommendation = dict(result)
        _print_ai_failures(self._ai_rule_recommendation.get("providers", []))
        if self._ai_rule_recommendation.get("used_ai"):
            self._set_status("התקבלה המלצת כללים מ-AI.")
        elif self._ai_rule_recommendation.get("status") == "ai_failed":
            self._set_status("AI לא הצליח להמליץ על כללים, לכן מוצגת המלצה מקומית.")
        else:
            self._set_status("נוצרה המלצת כללים מקומית.")
        self.aiRuleRecommendationChanged.emit()

    def _ai_rule_recommendation_failed(self, message: str) -> None:
        self._ai_rule_recommendation = {
            "status": "failed",
            "used_ai": False,
            "source": "local",
            "message": f"קריאת AI להמלצת כללים נכשלה: {message}",
            "providers": [],
            "recommendation": {},
        }
        print(f"[Mosaicly AI] rule recommendation failed. reason={message}", flush=True)
        self._set_status("קריאת AI להמלצת כללים נכשלה.")
        self.aiRuleRecommendationChanged.emit()

    def _reset_ai_action_suggestions(self, message: str) -> None:
        self._ai_action_suggestions = {
            "status": "not_run",
            "used_ai": False,
            "source": "",
            "message": message,
            "actions": [],
            "providers": [],
        }
        self.aiActionSuggestionsChanged.emit()

    def _record_ai_connection_results(self, results: list[dict[str, Any]]) -> None:
        normalized = []
        for item in results:
            provider = str(item.get("provider", ""))
            ok = bool(item.get("ok"))
            message = str(item.get("message", ""))
            source = str(item.get("source", ""))
            used = bool(item.get("used", ok or bool(source)))
            normalized.append(
                {
                    "provider": provider,
                    "used": used,
                    "ok": ok,
                    "source": source or provider,
                    "model": item.get("model", ""),
                    "text": message[:1000] if ok else "",
                    "error": "" if ok else message[:500],
                    "message": message,
                    "attempts": item.get("attempts", []),
                }
            )
        _print_ai_failures(normalized)
        ok_count = sum(1 for item in normalized if item.get("ok"))
        self._last_ai_review = {
            "status": "connection_test",
            "used_ai": ok_count > 0,
            "text": f"בדיקת חיבור AI הסתיימה: {ok_count}/{len(normalized)} הצליחו.",
            "providers": normalized,
            "best": {},
        }
        self.aiReviewChanged.emit()

    def _assignment_worker_finished(self, result: Any) -> None:
        score = dict(result).get("score", {})
        total_score = float(score.get("total_score", 0) or 0)
        self._set_status(f"השיבוץ הסתיים. ציון: {total_score}")
        self._reset_ai_action_suggestions("נוצרה הרצה חדשה. אפשר לבקש הצעות AI לגרסה הפעילה.")
        self.dataChanged.emit()
        self.assignmentFinished.emit()
        if self._request_ai_actions_after_assignment:
            self._start_post_assignment_ai_actions()
        if self._should_run_auto_ai_review(total_score, self._force_ai_review_after_assignment):
            self._start_auto_ai_review(total_score, self._ai_review_provider_limit)
        else:
            self._last_ai_review = {
                "status": "skipped",
                "used_ai": False,
                "text": "ציון השיבוץ מעל סף ה-AI או שהבדיקה האוטומטית כבויה.",
                "providers": load_ai_settings().get("providers", []),
                "best": {},
            }
            self.aiReviewChanged.emit()
            self._set_busy(False, "")
        self._force_ai_review_after_assignment = False
        self._request_ai_actions_after_assignment = False
        self._current_assignment_run_mode = "regular"

    def _assignment_worker_failed(self, message: str) -> None:
        self._set_status(f"השיבוץ נכשל: {message}")
        self._last_ai_review = {
            "status": "assignment_failed",
            "used_ai": False,
            "text": "AI לא הופעל כי השיבוץ נכשל.",
            "providers": [],
            "best": {},
        }
        self.aiReviewChanged.emit()
        self._set_busy(False, "")
        self._force_ai_review_after_assignment = False
        self._request_ai_actions_after_assignment = False
        self._current_assignment_run_mode = "regular"
        self.dataChanged.emit()

    def _start_post_assignment_ai_actions(self) -> None:
        try:
            project_id = self._require_project()
            candidates = self.assignment_service.action_suggestions(project_id, limit=120, exhaustive=True)
            self._request_ai_action_suggestions(
                project_id,
                candidates,
                no_candidates_message="הרצת MAX הסתיימה ולא נמצאה פעולה מקומית נוספת שמשפרת בלי להוסיף בעיות.",
                task=(
                    "הרצת MAX הסתיימה. בחר עד 8 העברות או החלפות מתוך candidate_actions "
                    "שיכולות לשפר את השיבוץ בלי להמציא פעולה חדשה."
                ),
            )
        except Exception as exc:
            self._ai_action_suggestions = {
                "status": "failed",
                "used_ai": False,
                "source": "local",
                "message": f"הרצת MAX הסתיימה, אבל הכנת הצעות AI נכשלה: {exc}",
                "actions": [],
                "providers": [],
            }
            self.aiActionSuggestionsChanged.emit()

    def _provider_limit(self, value: int | None = None) -> int:
        if value is None:
            try:
                project = self.database.get_project(self._require_project())
                settings = project.settings if project else DEFAULT_RULE_SETTINGS
                value = int(settings.get("ai_provider_limit", DEFAULT_RULE_SETTINGS["ai_provider_limit"]) or 1)
            except Exception:
                value = int(DEFAULT_RULE_SETTINGS["ai_provider_limit"])
        return max(1, min(len(PROVIDER_ENV_VARS), int(value or 1)))

    def _should_run_auto_ai_review(self, total_score: float, force: bool = False) -> bool:
        if force:
            return True
        project_id = self._require_project()
        project = self.database.get_project(project_id)
        settings = project.settings if project else DEFAULT_RULE_SETTINGS
        threshold = float(settings.get("ai_review_threshold", DEFAULT_RULE_SETTINGS["ai_review_threshold"]))
        return bool(settings.get("ai_auto_review", DEFAULT_RULE_SETTINGS["ai_auto_review"])) and total_score < threshold

    def _start_auto_ai_review(self, total_score: float, provider_limit: int = 3) -> None:
        project_id = self._require_project()
        provider_limit = self._provider_limit(provider_limit)
        payload = self._auto_ai_review_payload(project_id, total_score)
        fallback = self._local_ai_fallback("דוח AI אוטומטי", self.report_service.quality_report(project_id))
        task = (
            "נתח שיבוץ תלמידים שקיבל ציון נמוך מסף האיכות. "
            "החזר המלצות פעולה בלבד; אין לקבל החלטת שיבוץ סופית ואין לבקש מידע מזהה."
        )
        self._last_ai_review = {
            "status": "running",
            "used_ai": False,
            "text": "AI/ניתוח מקומי בודק את איכות השיבוץ לפי סיכום נתונים אנונימי.",
            "providers": [],
            "best": {},
            "payload": payload,
        }
        self.aiReviewChanged.emit()
        allow_external = self._project_allows_external_ai()
        self._set_busy(
            True,
            "השיבוץ הסתיים. מנתח איכות עם AI/ניתוח מקומי...",
            progress=0,
            progress_text="בודק את דוח האיכות האנונימי. בדרך כלל זה נמשך עד כשתי דקות.",
            estimated_seconds=120,
        )
        thread = QThread(self)
        worker = AiReviewWorker(task, payload, fallback, allow_external, provider_limit)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._ai_review_finished)
        worker.failed.connect(self._ai_review_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_thread(thread))
        self._threads.append((thread, worker))
        thread.start()

    def _ai_review_finished(self, result: Any) -> None:
        self._last_ai_review = dict(result)
        _print_ai_failures(self._last_ai_review.get("providers", []))
        self.aiReviewChanged.emit()
        if self._last_ai_review.get("used_ai"):
            self._set_status("ניתוח AI הושלם.")
        elif self._last_ai_review.get("status") == "ai_failed":
            self._set_status("AI לא הצליח לענות, לכן מוצג ניתוח מקומי.")
        else:
            self._set_status("נוצר ניתוח מקומי ללא שליחת מידע.")
        self._set_busy(False, "")

    def _ai_review_failed(self, message: str) -> None:
        self._last_ai_review = {
            "status": "failed",
            "used_ai": False,
            "text": f"ניתוח AI נכשל: {message}",
            "providers": [],
            "best": {},
        }
        self.aiReviewChanged.emit()
        self._set_status("ניתוח AI נכשל. ניתן להשתמש בדוח המקומי.")
        self._set_busy(False, "")

    def _auto_ai_review_payload(self, project_id: int, total_score: float) -> dict[str, Any]:
        report = self.report_service.quality_report(project_id)
        settings = self.ruleSettings()
        class_stats = []
        for index, item in enumerate(report.get("class_stats", [])[:12], start=1):
            schools = item.get("schools", {}) or {}
            class_stats.append(
                {
                    "class_ref": f"C{index:03d}",
                    "size": int(item.get("size", 0) or 0),
                    "boys": int(item.get("boys", 0) or 0),
                    "girls": int(item.get("girls", 0) or 0),
                    "avg_grade": item.get("avg_grade"),
                    "avg_behavior": item.get("avg_behavior"),
                    "friends_satisfied": int(item.get("friends_satisfied", 0) or 0),
                    "source_school_group_count": len(schools),
                }
            )
        return {
            "schema_version": 1,
            "task": "assignment_quality_review",
            "privacy": {
                "contains_student_names": False,
                "contains_notes": False,
                "contains_raw_rows": False,
                "max_recommendations": 5,
            },
            "assignment": {
                "total_score": total_score,
                "review_threshold": float(settings.get("ai_review_threshold", 78)),
                "student_count": len(self.database.get_students(project_id)),
                "class_count": len(self.database.get_classes(project_id)),
            },
            "penalties": report.get("penalties", {}),
            "issues": {
                "hard_violations_count": len(report.get("hard_violations", [])),
                "missing_friends_count": len(report.get("missing_friends", [])),
                "isolated_students_count": len(report.get("isolated_students", [])),
            },
            "classes": class_stats,
        }

    def _estimated_assignment_seconds(
        self,
        project_id: int,
        variant_count: int,
        settings_override: dict[str, Any] | None = None,
    ) -> float:
        student_count = len(self.database.get_students(project_id))
        class_count = max(1, len(self.database.get_classes(project_id)))
        variants = max(1, min(24, int(variant_count or 1)))
        per_variant = 18.0 + (student_count * 0.38) + (class_count * 1.5)
        if settings_override and settings_override.get("local_search_time_limit_seconds"):
            per_variant = min(
                per_variant,
                28.0
                + float(settings_override.get("optimizer_time_limit_seconds", 8) or 8)
                + float(settings_override.get("local_search_time_limit_seconds", 90) or 90),
            )
        if student_count >= 150:
            per_variant = min(per_variant, 150.0)
        upper = 1200.0 if settings_override and settings_override.get("allow_slow_large_search") else 600.0
        return max(45.0, min(upper, 12.0 + (variants * per_variant)))

    def _effective_assignment_variant_count(self, project_id: int, requested_count: int) -> int:
        return max(1, min(24, int(requested_count or 1)))

    def _refresh_validation(self, project_id: int) -> None:
        project = self.database.get_project(project_id)
        classes = self.database.get_classes(project_id)
        students = self.database.get_students(project_id)
        issues = validate_students(
            project_id=project_id,
            students=students,
            class_names=[group.name for group in classes],
            settings=project.settings if project else {},
        )
        self.database.replace_validation_issues(project_id, issues)

    def _local_ai_fallback(self, task: str, report: dict[str, Any]) -> str:
        return _build_local_ai_fallback(self.database, self._require_project(), task, report)


def _safe_class_stats(class_stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe = []
    for index, item in enumerate(class_stats[:12], start=1):
        schools = item.get("schools", {}) or {}
        safe.append(
            {
                "class_ref": f"C{index:03d}",
                "class_name": item.get("name", ""),
                "size": item.get("size", 0),
                "boys": item.get("boys", 0),
                "girls": item.get("girls", 0),
                "avg_grade": item.get("avg_grade"),
                "avg_behavior": item.get("avg_behavior"),
                "friends_satisfied": item.get("friends_satisfied", 0),
                "friends_missing": item.get("friends_missing", 0),
                "source_school_group_count": len(schools),
                "quality_score": item.get("quality_score"),
                "quality_summary": item.get("quality_summary", ""),
            }
        )
    return safe


def _student_details(
    database: Database,
    project_id: int,
    student_id: int,
    dashboard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    students = {
        int(student.id): student
        for student in database.get_students(project_id)
        if student.id is not None
    }
    clean_student_id = int(student_id)
    student = students.get(clean_student_id)
    if not student:
        return {}
    dashboard = dashboard if dashboard is not None else AssignmentService(database).dashboard(project_id)
    score = dashboard.get("score", {}) or {}
    row = next((item for item in dashboard.get("rows", []) if int(item["student_id"]) == clean_student_id), {})
    friendships = database.get_friendships(project_id)
    requested = [item for item in friendships if int(item["student_id"]) == clean_student_id]
    requested_by = [item for item in friendships if int(item["requested_friend_id"]) == clean_student_id]
    class_constraint = next(
        (
            item
            for item in database.get_class_constraints(project_id)
            if int(item["student_id"]) == clean_student_id
        ),
        {},
    )
    pairs = database.get_pair_constraints(project_id)
    together_names = [
        students.get(int(item["other_student_id"])).display_name
        for item in pairs.get("together", [])
        if int(item["student_id"]) == clean_student_id and students.get(int(item["other_student_id"]))
    ]
    separation_names = [
        students.get(int(item["other_student_id"])).display_name
        for item in pairs.get("separation", [])
        if int(item["student_id"]) == clean_student_id and students.get(int(item["other_student_id"]))
    ]
    return {
        "student": student.to_dict(),
        "assignment": row,
        "requested_friend_count": len(requested),
        "requested_friends": [
            students.get(int(item["requested_friend_id"])).display_name
            for item in requested
            if students.get(int(item["requested_friend_id"]))
        ],
        "requested_by": [
            students.get(int(item["student_id"])).display_name
            for item in requested_by
            if students.get(int(item["student_id"]))
        ],
        "got_friends": split_multi_value(row.get("got_friends", "")),
        "notes": {
            "parent": student.parent_notes,
            "teacher": student.teacher_notes,
            "interview": student.interview_notes,
        },
        "reasons": score.get("student_reasons", {}).get(str(clean_student_id), []),
        "suggestions": [],
        "constraints": {
            "allowed_classes": ", ".join(class_constraint.get("allowed_classes", [])),
            "forbidden_classes": ", ".join(class_constraint.get("forbidden_classes", [])),
            "must_be_with": ", ".join(together_names),
            "must_not_be_with": ", ".join(separation_names),
        },
    }


def _settings_for_ai_rules(settings: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "balance_class_size",
        "balance_gender",
        "balance_grades",
        "balance_behavior",
        "spread_dominant_students",
        "friendship",
        "friendship_required",
        "friendship_first",
        "friendship_priority_order",
        "spread_source_school",
        "avoid_social_isolation",
        "hard_class_capacity",
        "max_students_per_class",
        "max_students_per_gender",
        "class_size_weight",
        "gender_weight",
        "grade_weight",
        "subject_weight",
        "behavior_weight",
        "dominance_weight",
        "friendship_weight",
        "source_school_weight",
        "grade_tolerance",
        "gender_tolerance",
        "behavior_tolerance",
        "dominance_tolerance",
        "max_iterations",
        "search_restarts",
        "swap_search_min_score",
        "stop_when_score_at_least",
        "optimizer_backend",
        "optimizer_time_limit_seconds",
        "random_seed",
        "ai_assisted_assignment",
        "ai_auto_review",
        "ai_provider_limit",
        "allow_slow_large_search",
    }
    merged = {**DEFAULT_RULE_SETTINGS, **(settings or {})}
    return {key: merged.get(key) for key in DEFAULT_RULE_SETTINGS if key in allowed}


def _max_assignment_settings(settings: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_RULE_SETTINGS, **(settings or {})}
    return {
        **merged,
        "allow_slow_large_search": True,
        "hard_class_capacity": True,
        "max_students_per_class": int(merged.get("max_students_per_class", 40) or 40),
        "max_students_per_gender": int(merged.get("max_students_per_gender", 20) or 20),
        "friendship": True,
        "friendship_required": True,
        "friendship_first": True,
        "friendship_weight": max(2.6, float(merged.get("friendship_weight", 2.2) or 2.2)),
        "search_restarts": max(8, int(merged.get("search_restarts", 6) or 6)),
        "max_iterations": max(560, int(merged.get("max_iterations", 220) or 220)),
        "optimizer_backend": "auto",
        "optimizer_time_limit_seconds": max(12, int(merged.get("optimizer_time_limit_seconds", 8) or 8)),
        "local_search_time_limit_seconds": max(90, int(merged.get("local_search_time_limit_seconds", 0) or 0)),
        "stop_when_score_at_least": 96,
        "ai_assisted_assignment": True,
        "ai_auto_review": True,
        "ai_review_threshold": 100,
        "save_top_variants": 5,
    }


def _with_candidate_id(index: int, action: dict[str, Any]) -> dict[str, Any]:
    item = dict(action)
    item["candidate_id"] = f"A{index:03d}"
    return item


def _replace_names(text: str, name_to_ref: dict[str, str], school_refs: dict[str, str]) -> str:
    clean = text
    for name, ref in sorted(name_to_ref.items(), key=lambda item: len(item[0]), reverse=True):
        if name:
            clean = clean.replace(name, ref)
    for name, ref in sorted(school_refs.items(), key=lambda item: len(item[0]), reverse=True):
        if name:
            clean = clean.replace(name, ref)
    return clean


def _path_from_url(value: str) -> str:
    if value.startswith("file:"):
        return QUrl(value).toLocalFile()
    return str(Path(value))


def _format_duration(seconds: float) -> str:
    clean = max(0, int(round(seconds)))
    minutes, sec = divmod(clean, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} שעות ו-{minutes} דקות"
    if minutes:
        return f"{minutes} דקות ו-{sec} שניות"
    return f"{sec} שניות"


def _round_optional(value: Any, digits: int = 1) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def ceil_div(value: int, divisor: int) -> int:
    divisor = max(1, int(divisor or 1))
    return int((int(value or 0) + divisor - 1) // divisor)


def _plain_variant(value: Any) -> Any:
    to_variant = getattr(value, "toVariant", None)
    if callable(to_variant):
        try:
            converted = to_variant()
            if converted is not value:
                return _plain_variant(converted)
        except (TypeError, RuntimeError):
            pass
    if isinstance(value, dict):
        return {str(key): _plain_variant(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_variant(item) for item in value]
    return value


def _variant_dict(value: Any) -> dict[str, Any]:
    plain = _plain_variant(value)
    if isinstance(plain, dict):
        return plain
    try:
        return dict(plain)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"Expected a QML object or dict, got {type(value).__name__}") from exc


def _print_ai_failures(results: list[dict[str, Any]]) -> None:
    for item in results:
        if item.get("ok"):
            continue
        provider = item.get("provider", "Unknown")
        model = item.get("model") or "-"
        message = item.get("error") or item.get("message") or "לא התקבלה הודעת שגיאה."
        print(f"[Mosaicly AI] {provider} failed. model={model}. reason={message}", flush=True)
        for attempt in item.get("attempts", []) or []:
            if attempt.get("ok"):
                continue
            attempt_model = attempt.get("model", "-")
            attempt_message = attempt.get("message", "לא התקבלה הודעת שגיאה.")
            print(
                f"[Mosaicly AI] {provider} model attempt failed. model={attempt_model}. reason={attempt_message}",
                flush=True,
            )
