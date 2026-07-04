from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from class_balancer.db import Database


class ExcelExporter:
    def __init__(self, database: Database) -> None:
        self.database = database

    def export_project(self, project_id: int, output_path: str | Path) -> Path:
        output = Path(output_path)
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        output.parent.mkdir(parents=True, exist_ok=True)

        active = self.database.get_active_assignment_version(project_id)
        if not active:
            raise ValueError("אין שיבוץ פעיל לייצוא.")
        project = self.database.get_project(project_id)
        rows = self.database.get_active_assignment_rows(project_id)
        classes = self.database.get_classes(project_id)
        students = {student.id: student for student in self.database.get_students(project_id)}
        score = active.get("score", {})
        issues = self.database.get_validation_issues(project_id, include_resolved=True)

        sheets: list[tuple[str, list[list[Any]]]] = []
        sheets.append(("כל התלמידים", self._all_students_sheet(rows, students)))
        for group in classes:
            class_rows = [row for row in rows if int(row["class_id"]) == int(group.id)]
            sheets.append((group.name, self._all_students_sheet(class_rows, students, include_class=False)))
        sheets.append(("סטטיסטיקות", self._stats_sheet(score)))
        sheets.append(("חריגות", self._issues_sheet(score, issues)))
        sheets.append(("ללא חבר", self._missing_friends_sheet(score)))
        sheets.append(("שינויים ידניים", self._manual_changes_sheet(rows, students)))
        sheets.append(("גרסאות שיבוץ", self._versions_sheet(project_id)))
        sheets.append(("הגדרות", self._settings_sheet(project.settings if project else {}, active)))

        _write_xlsx(output, sheets)
        return output

    def export_validation_issues(self, project_id: int, output_path: str | Path) -> Path:
        output = Path(output_path)
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        output.parent.mkdir(parents=True, exist_ok=True)

        project = self.database.get_project(project_id)
        issues = self.database.get_validation_issues(project_id, include_resolved=True)
        students = {
            int(student.id): student
            for student in self.database.get_students(project_id)
            if student.id is not None
        }
        sheets = [
            ("סיכום", self._validation_summary_sheet(project.name if project else "", issues)),
            ("שגיאות", self._validation_issues_sheet(issues, students)),
        ]
        _write_xlsx(output, sheets)
        return output

    def _validation_summary_sheet(self, project_name: str, issues: list[Any]) -> list[list[Any]]:
        severity_counts = {"critical": 0, "warning": 0, "info": 0}
        for issue in issues:
            severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1
        return [
            ["מדד", "ערך"],
            ["פרויקט", project_name],
            ["סה\"כ נושאים לבדיקה", len(issues)],
            ["שגיאות קריטיות", severity_counts.get("critical", 0)],
            ["אזהרות", severity_counts.get("warning", 0)],
            ["מידע", severity_counts.get("info", 0)],
        ]

    def _validation_issues_sheet(self, issues: list[Any], students: dict[int, Any]) -> list[list[Any]]:
        data = [["חומרה", "סוג", "שדה", "קוד תלמיד", "שם תלמיד", "פירוט", "נפתר", "נוצר בתאריך"]]
        ordered = sorted(
            issues,
            key=lambda issue: (_severity_rank(issue.severity), issue.student_id or 0, issue.id or 0),
        )
        for issue in ordered:
            student = students.get(int(issue.student_id)) if issue.student_id else None
            data.append(
                [
                    _severity_label(issue.severity),
                    issue.severity,
                    issue.field_name,
                    student.internal_code if student else "",
                    student.display_name if student else "",
                    issue.message,
                    "כן" if issue.resolved else "",
                    issue.created_at,
                ]
            )
        if len(data) == 1:
            data.append(["", "", "", "", "", "לא נמצאו שגיאות או אזהרות פעילות.", "", ""])
        return data

    def _all_students_sheet(
        self,
        rows: list[dict[str, Any]],
        students: dict[int | None, Any],
        include_class: bool = True,
    ) -> list[list[Any]]:
        headers = [
            "כיתה",
            "קוד",
            "שם פרטי",
            "שם משפחה",
            "שם מלא",
            "מגדר",
            "בית ספר מקור",
            "מתמטיקה",
            "אנגלית",
            "עברית",
            "ממוצע",
            "התנהגות",
            "דומיננטיות / אתגר",
            "נעול",
            "שונה ידנית",
            "הערות מורה",
        ]
        if not include_class:
            headers = headers[1:]
        data = [headers]
        for row in rows:
            student = students.get(int(row["student_id"]))
            values = [
                row.get("class_name", ""),
                row.get("internal_code", ""),
                row.get("first_name", ""),
                row.get("last_name", ""),
                row.get("full_name", ""),
                row.get("gender", ""),
                row.get("source_school", ""),
                student.math_grade if student else "",
                student.english_grade if student else "",
                student.hebrew_grade if student else "",
                row.get("average_grade", ""),
                row.get("behavior_score", ""),
                student.dominance_score if student else "",
                "כן" if row.get("locked_manually") else "",
                "כן" if row.get("changed_manually") else "",
                student.teacher_notes if student else "",
            ]
            data.append(values if include_class else values[1:])
        return data

    def _stats_sheet(self, score: dict[str, Any]) -> list[list[Any]]:
        data = [["מדד", "ערך"]]
        data.append(["ציון כללי", score.get("total_score", "")])
        data.append(["סיכום", score.get("summary", "")])
        for key, value in score.get("penalties", {}).items():
            data.append([f"קנס: {key}", value])
        data.append([])
        data.append(["כיתה", "מספר תלמידים", "בנים", "בנות", "ממוצע ציונים", "ממוצע התנהגות", "קיבלו חבר"])
        for stat in score.get("class_stats", []):
            data.append(
                [
                    stat.get("name"),
                    stat.get("size"),
                    stat.get("boys"),
                    stat.get("girls"),
                    stat.get("avg_grade"),
                    stat.get("avg_behavior"),
                    stat.get("friends_satisfied"),
                ]
            )
        return data

    def _issues_sheet(self, score: dict[str, Any], issues: list[Any]) -> list[list[Any]]:
        data = [["סוג", "חומרה", "שדה", "פירוט"]]
        for violation in score.get("hard_violations", []):
            data.append(["אילוץ שיבוץ", "critical", "", violation])
        for issue in issues:
            data.append(["בדיקת נתונים", issue.severity, issue.field_name, issue.message])
        return data

    def _missing_friends_sheet(self, score: dict[str, Any]) -> list[list[Any]]:
        data = [["תלמיד/ה", "סיבה"]]
        for item in score.get("friendship", {}).get("missing", []):
            data.append([item.get("student_name"), item.get("reason")])
        return data

    def _manual_changes_sheet(self, rows: list[dict[str, Any]], students: dict[int | None, Any]) -> list[list[Any]]:
        data = [["כיתה", "קוד", "שם", "נעול", "שונה ידנית"]]
        for row in rows:
            if not row.get("locked_manually") and not row.get("changed_manually"):
                continue
            student = students.get(int(row["student_id"]))
            data.append(
                [
                    row.get("class_name"),
                    row.get("internal_code"),
                    student.display_name if student else row.get("full_name", ""),
                    "כן" if row.get("locked_manually") else "",
                    "כן" if row.get("changed_manually") else "",
                ]
            )
        return data

    def _settings_sheet(self, settings: dict[str, Any], active: dict[str, Any]) -> list[list[Any]]:
        data = [["הגדרה", "ערך"]]
        data.append(["גרסת שיבוץ", active.get("name", "")])
        data.append(["נוצר בתאריך", active.get("created_at", "")])
        for key, value in settings.items():
            data.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
        return data

    def _versions_sheet(self, project_id: int) -> list[list[Any]]:
        data = [["שם גרסה", "תאריך", "ציון", "פעילה", "סיכום"]]
        for version in self.database.get_assignment_versions(project_id):
            score = version.get("score", {})
            data.append(
                [
                    version.get("name", ""),
                    version.get("created_at", ""),
                    version.get("score_total", ""),
                    "כן" if version.get("is_active") else "",
                    score.get("summary", ""),
                ]
            )
        return data


def _write_xlsx(path: Path, sheets: list[tuple[str, list[list[Any]]]]) -> None:
    clean_sheets = _clean_sheet_names(sheets)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(clean_sheets)))
        archive.writestr("_rels/.rels", _root_rels())
        archive.writestr("xl/workbook.xml", _workbook_xml(clean_sheets))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(clean_sheets)))
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, (_, rows) in enumerate(clean_sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows))


def _clean_sheet_names(sheets: list[tuple[str, list[list[Any]]]]) -> list[tuple[str, list[list[Any]]]]:
    used: set[str] = set()
    clean: list[tuple[str, list[list[Any]]]] = []
    for index, (name, rows) in enumerate(sheets, start=1):
        sheet_name = re.sub(r"[\[\]:*?/\\]", " ", name or f"Sheet {index}").strip()[:31] or f"Sheet {index}"
        original = sheet_name
        suffix = 2
        while sheet_name in used:
            sheet_name = f"{original[:28]} {suffix}"[:31]
            suffix += 1
        used.add(sheet_name)
        clean.append((sheet_name, rows))
    return clean


def _content_types(sheet_count: int) -> str:
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{sheet_overrides}</Types>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheets: list[tuple[str, list[list[Any]]]]) -> str:
    sheet_nodes = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _) in enumerate(sheets, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<workbookViews><workbookView activeTab="0"/></workbookViews>'
        f"<sheets>{sheet_nodes}</sheets></workbook>"
    )


def _workbook_rels(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    rels += (
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font>'
        '<font><b/><sz val="11"/><name val="Arial"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        "</styleSheet>"
    )


def _worksheet_xml(rows: list[list[Any]]) -> str:
    col_widths = _column_widths(rows)
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(col_widths, start=1)
    )
    row_nodes = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            cells.append(_cell_xml(row_index, col_index, value, header=row_index == 1))
        row_nodes.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    dimension = f"A1:{_column_name(max(1, len(col_widths)))}{max(1, len(rows))}"
    auto_filter = f'<autoFilter ref="{dimension}"/>' if rows and len(rows) > 1 else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0" rightToLeft="1"/></sheetViews>'
        f"<cols>{cols}</cols>"
        f'<sheetData>{"".join(row_nodes)}</sheetData>'
        f"{auto_filter}</worksheet>"
    )


def _cell_xml(row_index: int, col_index: int, value: Any, header: bool = False) -> str:
    ref = f"{_column_name(col_index)}{row_index}"
    style = ' s="1"' if header else ""
    if value is None:
        value = ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"{style}><is><t>{text}</t></is></c>'


def _column_widths(rows: list[list[Any]]) -> list[int]:
    max_cols = max((len(row) for row in rows), default=1)
    widths: list[int] = []
    for index in range(max_cols):
        max_len = max((len(str(row[index])) for row in rows if index < len(row) and row[index] is not None), default=8)
        widths.append(min(42, max(10, max_len + 3)))
    return widths


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name or "A"


def _severity_rank(severity: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get(severity, 3)


def _severity_label(severity: str) -> str:
    return {
        "critical": "שגיאה קריטית",
        "warning": "אזהרה",
        "info": "מידע",
    }.get(severity, severity)
