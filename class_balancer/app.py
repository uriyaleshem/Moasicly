from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from class_balancer.db import Database
from class_balancer.services import AssignmentService, ExportService, ImportService, ProjectService, ReportService


def run_gui(database_path: str | None = None) -> int:
    try:
        from PySide6.QtCore import QUrl, Qt
        from PySide6.QtGui import QFont, QGuiApplication, QIcon
        from PySide6.QtQml import QQmlApplicationEngine
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("PySide6 לא מותקן. התקינו requirements.txt או הריצו --smoke לליבת המערכת.") from exc

    from class_balancer.ui.bridge import AppBridge

    app = QGuiApplication([])
    app.setApplicationName("Mosaicly")
    app.setApplicationDisplayName("Mosaicly")
    app.setLayoutDirection(Qt.RightToLeft)
    app.setFont(QFont("Segoe UI", 10))
    resource_root = _resource_root()
    window_icon_path = _first_existing(
        resource_root / "mosaiclyIcon.ico",
        resource_root / "moasiclyIcon.png",
        resource_root / "AppIcon.ico",
        resource_root / "AppIcon.png",
        fallback=resource_root / "mosaiclyIcon.ico",
    )
    app_icon_path = _first_existing(
        resource_root / "moasiclyIcon.png",
        resource_root / "mosaiclyIcon.ico",
        resource_root / "AppIcon.png",
        resource_root / "AppIcon.ico",
        fallback=window_icon_path,
    )
    if window_icon_path.exists():
        app.setWindowIcon(QIcon(str(window_icon_path)))
    database = Database(database_path)
    bridge = AppBridge(
        database=database,
        project_service=ProjectService(database),
        import_service=ImportService(database),
        assignment_service=AssignmentService(database),
        export_service=ExportService(database),
        report_service=ReportService(database),
    )

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.rootContext().setContextProperty("appIconUrl", QUrl.fromLocalFile(str(app_icon_path)).toString() if app_icon_path.exists() else "")
    qml_path = resource_root / "class_balancer" / "ui" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        return 1
    return app.exec()


def _resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent.parent


def _first_existing(*paths: Path, fallback: Path) -> Path:
    return next((path for path in paths if path.exists()), fallback)


def run_smoke(database_path: str | None = None) -> dict[str, object]:
    import csv

    temp_dir = Path(tempfile.mkdtemp(prefix="class_balancer_"))
    db_path = Path(database_path) if database_path else temp_dir / "class_balancer.sqlite3"
    csv_path = temp_dir / "students.csv"
    export_path = temp_dir / "result.xlsx"
    rows = [
        ["שם מלא", "מין", "בית ספר קודם", "ממוצע", "התנהגות", "חבר 1"],
        ["נועה כהן", "בת", "אלון", "92", "מצוין", "מאיה לוי"],
        ["מאיה לוי", "בת", "אלון", "88", "טובה", "נועה כהן"],
        ["יואב מזרחי", "בן", "ברוש", "77", "בינוני", "אדם פרץ"],
        ["אדם פרץ", "בן", "ברוש", "81", "טובה", "יואב מזרחי"],
        ["רוני שלום", "בת", "ארז", "69", "בעייתית", ""],
        ["דניאל חדד", "בן", "ארז", "95", "מצוין", ""],
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)

    database = Database(db_path)
    project_service = ProjectService(database)
    import_service = ImportService(database)
    assignment_service = AssignmentService(database)
    export_service = ExportService(database)

    project_id = project_service.create_project(
        name="בדיקת עשן",
        grade_level="ז",
        school_year="תשפז",
        class_count=2,
        class_names_text="ז1, ז2",
    )
    table = import_service.load_preview(csv_path)
    import_service.save_imported_students(project_id, table and import_service.current_mapping)
    result = assignment_service.run_assignment(project_id)
    export_service.export_project(project_id, export_path)
    return {
        "project_id": project_id,
        "students": database.count_students(project_id),
        "score": result["score"]["total_score"],
        "export_path": str(export_path),
        "database_path": str(db_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mosaicly desktop app")
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--smoke", action="store_true", help="Run core smoke test without GUI")
    args = parser.parse_args(argv)
    if args.smoke:
        result = run_smoke(args.db)
        print(result)
        return 0
    return run_gui(args.db)
