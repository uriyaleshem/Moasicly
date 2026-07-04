from __future__ import annotations

from pathlib import Path

from class_balancer.db import Database
from class_balancer.export import ExcelExporter


class ExportService:
    def __init__(self, database: Database) -> None:
        self.exporter = ExcelExporter(database)

    def export_project(self, project_id: int, output_path: str | Path) -> Path:
        return self.exporter.export_project(project_id, output_path)

    def export_validation_issues(self, project_id: int, output_path: str | Path) -> Path:
        return self.exporter.export_validation_issues(project_id, output_path)
