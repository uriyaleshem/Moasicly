from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from class_balancer.models.entities import ClassGroup, Project, Student, ValidationIssue, utc_now_iso
from class_balancer.models.fields import DEFAULT_RULE_SETTINGS
from class_balancer.validation.normalization import GENDER_FEMALE, GENDER_MALE, student_stable_key

STUDENT_EDITABLE_COLUMNS = {
    "internal_code",
    "stable_key",
    "first_name",
    "last_name",
    "full_name",
    "gender",
    "source_school",
    "math_grade",
    "english_grade",
    "hebrew_grade",
    "average_grade",
    "behavior_score",
    "dominance_score",
    "parent_notes",
    "teacher_notes",
    "interview_notes",
}

SCHEMA_VERSION = 4
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, "baseline_current_schema"),
    (2, "assignments_project_guard"),
    (3, "student_stable_key"),
    (4, "integrity_indexes_and_triggers"),
)


def default_database_path() -> Path:
    configured = os.environ.get("CLASS_BALANCER_DB")
    if configured:
        return Path(configured)
    return Path.home() / ".class_balancer" / "class_balancer.sqlite3"


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | bytes | None, default: Any = None) -> Any:
    if value in (None, ""):
        return {} if default is None else default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {} if default is None else default


def _settings_int(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return max(0, int(settings.get(key, default) or 0))
    except (TypeError, ValueError):
        return max(0, int(default))


def _unique_clean_names(values: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _unique_stable_key(base_key: str, seen: set[str], fallback_id: int) -> str:
    base = (base_key or f"row:{fallback_id:06d}").strip()
    if base not in seen:
        return base
    suffix = 2
    while f"{base}#{suffix}" in seen:
        suffix += 1
    return f"{base}#{suffix}"


def _unique_internal_code(code: str, reserved: set[str], index: int) -> str:
    base = (code or f"S{index:03d}").strip()
    if base not in reserved:
        return base
    suffix = 2
    while f"{base}-{suffix}" in reserved:
        suffix += 1
    return f"{base}-{suffix}"


def _student_row_changed(row: sqlite3.Row, student: Student) -> bool:
    comparisons = {
        "internal_code": student.internal_code,
        "stable_key": student.stable_key,
        "first_name": student.first_name,
        "last_name": student.last_name,
        "full_name": student.full_name,
        "gender": student.gender,
        "source_school": student.source_school,
        "math_grade": student.math_grade,
        "english_grade": student.english_grade,
        "hebrew_grade": student.hebrew_grade,
        "average_grade": student.average_grade,
        "behavior_score": student.behavior_score,
        "dominance_score": student.dominance_score,
        "parent_notes": student.parent_notes,
        "teacher_notes": student.teacher_notes,
        "interview_notes": student.interview_notes,
        "raw_data_json": _json_dumps(student.raw_data),
    }
    for key, value in comparisons.items():
        current = row[key]
        if isinstance(value, float) or isinstance(current, float):
            if current is None and value is None:
                continue
            if current is None or value is None or abs(float(current) - float(value)) > 0.0001:
                return True
            continue
        if (current or "") != (value or ""):
            return True
    return False


class Database:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            try:
                self._connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass
            self.init_schema()
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                grade_level TEXT,
                school_year TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                internal_code TEXT NOT NULL,
                stable_key TEXT,
                first_name TEXT,
                last_name TEXT,
                full_name TEXT,
                gender TEXT,
                source_school TEXT,
                math_grade REAL,
                english_grade REAL,
                hebrew_grade REAL,
                average_grade REAL,
                behavior_score TEXT,
                dominance_score REAL,
                parent_notes TEXT,
                teacher_notes TEXT,
                interview_notes TEXT,
                raw_data_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_students_project ON students(project_id);

            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                min_students INTEGER NOT NULL DEFAULT 0,
                max_students INTEGER NOT NULL DEFAULT 0,
                target_students INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS friendship_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                requested_friend_id INTEGER NOT NULL,
                priority INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY(requested_friend_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS class_constraints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                allowed_classes_json TEXT NOT NULL DEFAULT '[]',
                forbidden_classes_json TEXT NOT NULL DEFAULT '[]',
                locked_class_id INTEGER,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY(locked_class_id) REFERENCES classes(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS together_constraints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                other_student_id INTEGER NOT NULL,
                reason TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY(other_student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS separation_constraints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                other_student_id INTEGER NOT NULL,
                reason TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY(other_student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS assignment_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                score_total REAL NOT NULL DEFAULT 0,
                score_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT,
                is_active INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                class_id INTEGER NOT NULL,
                locked_manually INTEGER NOT NULL DEFAULT 0,
                changed_manually INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(version_id) REFERENCES assignment_versions(id) ON DELETE CASCADE,
                FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS import_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                original_column_name TEXT NOT NULL,
                mapped_field_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS validation_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                student_id INTEGER,
                field_name TEXT,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
            );
            """
        )
        self._run_migrations()
        self._install_integrity_guards()

    def _run_migrations(self) -> None:
        applied = {
            int(row["version"])
            for row in self.connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, name in MIGRATIONS:
            if version in applied:
                continue
            getattr(self, f"_migration_{version}")()
            self.connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, utc_now_iso()),
            )
            self.connection.commit()
        self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()

    def _migration_1(self) -> None:
        self._ensure_column("students", "dominance_score", "REAL")

    def _migration_2(self) -> None:
        self._ensure_column("assignments", "project_id", "INTEGER")
        self._backfill_assignment_project_ids()

    def _migration_3(self) -> None:
        self._ensure_column("students", "stable_key", "TEXT")
        self._backfill_student_stable_keys()

    def _migration_4(self) -> None:
        self._install_integrity_guards()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            self.connection.commit()

    def _backfill_assignment_project_ids(self) -> None:
        self.connection.execute(
            """
            UPDATE assignments
            SET project_id = (
                SELECT project_id FROM assignment_versions
                WHERE assignment_versions.id = assignments.version_id
            )
            WHERE project_id IS NULL
            """
        )
        self.connection.execute(
            """
            DELETE FROM assignments
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM assignments
                GROUP BY version_id, student_id
            )
            """
        )
        self.connection.execute(
            """
            UPDATE assignment_versions
            SET is_active = 0
            WHERE is_active = 1
              AND id NOT IN (
                  SELECT MAX(id)
                  FROM assignment_versions
                  WHERE is_active = 1
                  GROUP BY project_id
              )
            """
        )
        self.connection.commit()

    def _backfill_student_stable_keys(self) -> None:
        rows = self.connection.execute(
            """
            SELECT id, project_id, internal_code, first_name, last_name, full_name, stable_key
            FROM students
            ORDER BY project_id, id
            """
        ).fetchall()
        seen: dict[int, set[str]] = {}
        for row in rows:
            project_id = int(row["project_id"])
            project_seen = seen.setdefault(project_id, set())
            key = str(row["stable_key"] or "").strip()
            if not key:
                display_name = row["full_name"] or f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
                key = student_stable_key(row["internal_code"], display_name, int(row["id"]))
            key = _unique_stable_key(key, project_seen, int(row["id"]))
            project_seen.add(key)
            self.connection.execute("UPDATE students SET stable_key = ? WHERE id = ?", (key, int(row["id"])))
        self.connection.commit()

    def _install_integrity_guards(self) -> None:
        self.connection.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_assignments_version_student
            ON assignments(version_id, student_id);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_assignment_versions_one_active
            ON assignment_versions(project_id)
            WHERE is_active = 1;

            CREATE TRIGGER IF NOT EXISTS trg_assignments_project_insert
            BEFORE INSERT ON assignments
            BEGIN
                SELECT CASE
                    WHEN NEW.project_id IS NULL THEN
                        RAISE(ABORT, 'assignment project_id is required')
                END;
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM assignment_versions
                        WHERE id = NEW.version_id AND project_id = NEW.project_id
                    ) THEN
                        RAISE(ABORT, 'assignment version project mismatch')
                END;
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM students
                        WHERE id = NEW.student_id AND project_id = NEW.project_id
                    ) THEN
                        RAISE(ABORT, 'assignment student project mismatch')
                END;
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM classes
                        WHERE id = NEW.class_id AND project_id = NEW.project_id
                    ) THEN
                        RAISE(ABORT, 'assignment class project mismatch')
                END;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_assignments_project_update
            BEFORE UPDATE ON assignments
            BEGIN
                SELECT CASE
                    WHEN NEW.project_id IS NULL THEN
                        RAISE(ABORT, 'assignment project_id is required')
                END;
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM assignment_versions
                        WHERE id = NEW.version_id AND project_id = NEW.project_id
                    ) THEN
                        RAISE(ABORT, 'assignment version project mismatch')
                END;
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM students
                        WHERE id = NEW.student_id AND project_id = NEW.project_id
                    ) THEN
                        RAISE(ABORT, 'assignment student project mismatch')
                END;
                SELECT CASE
                    WHEN NOT EXISTS (
                        SELECT 1 FROM classes
                        WHERE id = NEW.class_id AND project_id = NEW.project_id
                    ) THEN
                        RAISE(ABORT, 'assignment class project mismatch')
                END;
            END;
            """
        )
        try:
            self.connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_classes_project_name ON classes(project_id, name)"
            )
            self.connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_project_code ON students(project_id, internal_code)"
            )
            self.connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_project_stable_key ON students(project_id, stable_key)"
            )
        except sqlite3.IntegrityError:
            pass
        self.connection.commit()

    def create_project(
        self,
        name: str,
        grade_level: str,
        school_year: str,
        class_names: list[str],
        notes: str = "",
        settings: dict[str, Any] | None = None,
    ) -> int:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("שם פרויקט לא יכול להיות ריק.")
        now = utc_now_iso()
        merged_settings = {**DEFAULT_RULE_SETTINGS, **(settings or {})}
        if notes:
            merged_settings["notes"] = notes
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO projects (name, grade_level, school_year, created_at, updated_at, settings_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (clean_name, grade_level.strip(), school_year.strip(), now, now, _json_dumps(merged_settings)),
            )
            project_id = int(cursor.lastrowid)
            self._replace_classes(connection, project_id, class_names)
        return project_id

    def update_project_settings(self, project_id: int, settings: dict[str, Any]) -> None:
        current = self.get_project(project_id)
        merged = {**(current.settings if current else DEFAULT_RULE_SETTINGS), **settings}
        self.connection.execute(
            "UPDATE projects SET settings_json = ?, updated_at = ? WHERE id = ?",
            (_json_dumps(merged), utc_now_iso(), project_id),
        )
        self.connection.commit()

    def list_projects(self) -> list[Project]:
        rows = self.connection.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [self._project_from_row(row) for row in rows]

    def get_project(self, project_id: int) -> Project | None:
        row = self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._project_from_row(row) if row else None

    def delete_project(self, project_id: int) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0

    def replace_classes(self, project_id: int, class_names: list[str]) -> None:
        with self.transaction() as connection:
            self._replace_classes(connection, project_id, class_names)

    def _replace_classes(self, connection: sqlite3.Connection, project_id: int, class_names: list[str]) -> None:
        clean_names = _unique_clean_names(class_names)
        if not clean_names:
            clean_names = ["כיתה 1", "כיתה 2"]
        students_count = self.count_students(project_id, connection=connection)
        target = int(round(students_count / len(clean_names))) if students_count else 0
        connection.execute("DELETE FROM assignment_versions WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM classes WHERE project_id = ?", (project_id,))
        now = utc_now_iso()
        for name in clean_names:
            connection.execute(
                """
                INSERT INTO classes
                    (project_id, name, min_students, max_students, target_students, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, name, 0, 0, target, now, now),
            )
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))

    def get_classes(self, project_id: int) -> list[ClassGroup]:
        rows = self.connection.execute(
            "SELECT * FROM classes WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return [self._class_from_row(row) for row in rows]

    def replace_students(self, project_id: int, students: list[Student]) -> list[Student]:
        with self.transaction() as connection:
            now = utc_now_iso()
            existing_rows = connection.execute(
                "SELECT * FROM students WHERE project_id = ? ORDER BY id",
                (project_id,),
            ).fetchall()
            existing_by_key = {
                str(row["stable_key"] or ""): row
                for row in existing_rows
                if str(row["stable_key"] or "")
            }
            existing_by_code = {
                str(row["internal_code"] or ""): row
                for row in existing_rows
                if str(row["internal_code"] or "")
            }
            incoming_ids: set[int] = set()
            incoming_keys: set[str] = set()
            reserved_codes = {str(row["internal_code"] or "") for row in existing_rows if str(row["internal_code"] or "")}
            saved: list[Student] = []
            changed = False
            for index, student in enumerate(students, start=1):
                student.project_id = project_id
                student.internal_code = student.internal_code or f"S{index:03d}"
                stable_base = student.stable_key or student_stable_key(student.internal_code, student.display_name, index)
                student.stable_key = _unique_stable_key(stable_base, incoming_keys, index)
                incoming_keys.add(student.stable_key)
                student.updated_at = now
                existing = existing_by_key.get(student.stable_key) or existing_by_code.get(student.internal_code)
                if existing and student.raw_data.get("_generated_internal_code"):
                    student.internal_code = str(existing["internal_code"] or student.internal_code)
                elif student.raw_data.get("_generated_internal_code"):
                    student.internal_code = _unique_internal_code(student.internal_code, reserved_codes, index)
                    reserved_codes.add(student.internal_code)
                if existing:
                    student.id = int(existing["id"])
                    student.created_at = existing["created_at"]
                    if _student_row_changed(existing, student):
                        connection.execute(
                            """
                            UPDATE students
                            SET internal_code = ?, stable_key = ?, first_name = ?, last_name = ?, full_name = ?,
                                gender = ?, source_school = ?, math_grade = ?, english_grade = ?, hebrew_grade = ?,
                                average_grade = ?, behavior_score = ?, dominance_score = ?, parent_notes = ?,
                                teacher_notes = ?, interview_notes = ?, raw_data_json = ?, updated_at = ?
                            WHERE project_id = ? AND id = ?
                            """,
                            (
                                student.internal_code,
                                student.stable_key,
                                student.first_name,
                                student.last_name,
                                student.full_name,
                                student.gender,
                                student.source_school,
                                student.math_grade,
                                student.english_grade,
                                student.hebrew_grade,
                                student.average_grade,
                                student.behavior_score,
                                student.dominance_score,
                                student.parent_notes,
                                student.teacher_notes,
                                student.interview_notes,
                                _json_dumps(student.raw_data),
                                student.updated_at,
                                project_id,
                                student.id,
                            ),
                        )
                        changed = True
                else:
                    student.created_at = student.created_at or now
                    cursor = connection.execute(
                        """
                        INSERT INTO students (
                            project_id, internal_code, stable_key, first_name, last_name, full_name, gender, source_school,
                            math_grade, english_grade, hebrew_grade, average_grade, behavior_score, dominance_score,
                            parent_notes, teacher_notes, interview_notes, raw_data_json, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            project_id,
                            student.internal_code,
                            student.stable_key,
                            student.first_name,
                            student.last_name,
                            student.full_name,
                            student.gender,
                            student.source_school,
                            student.math_grade,
                            student.english_grade,
                            student.hebrew_grade,
                            student.average_grade,
                            student.behavior_score,
                            student.dominance_score,
                            student.parent_notes,
                            student.teacher_notes,
                            student.interview_notes,
                            _json_dumps(student.raw_data),
                            student.created_at,
                            student.updated_at,
                        ),
                    )
                    student.id = int(cursor.lastrowid)
                    changed = True
                incoming_ids.add(int(student.id))
                saved.append(student)

            existing_ids = {int(row["id"]) for row in existing_rows}
            removed_ids = existing_ids - incoming_ids
            if removed_ids:
                placeholders = ",".join("?" for _ in removed_ids)
                connection.execute(f"DELETE FROM students WHERE project_id = ? AND id IN ({placeholders})", (project_id, *removed_ids))
                changed = True

            connection.execute("DELETE FROM validation_issues WHERE project_id = ?", (project_id,))
            if changed:
                connection.execute("DELETE FROM assignment_versions WHERE project_id = ?", (project_id,))
            self._retarget_classes(connection, project_id)
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
        return saved

    def _retarget_classes(self, connection: sqlite3.Connection, project_id: int) -> None:
        classes = connection.execute("SELECT id FROM classes WHERE project_id = ?", (project_id,)).fetchall()
        count = self.count_students(project_id, connection=connection)
        target = int(round(count / len(classes))) if classes else 0
        for row in classes:
            connection.execute(
                "UPDATE classes SET target_students = ?, updated_at = ? WHERE id = ?",
                (target, utc_now_iso(), int(row["id"])),
            )

    def count_students(self, project_id: int, connection: sqlite3.Connection | None = None) -> int:
        connection = connection or self.connection
        row = connection.execute("SELECT COUNT(*) AS c FROM students WHERE project_id = ?", (project_id,)).fetchone()
        return int(row["c"]) if row else 0

    def get_students(self, project_id: int) -> list[Student]:
        rows = self.connection.execute(
            "SELECT * FROM students WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return [self._student_from_row(row) for row in rows]

    def update_student_fields(self, project_id: int, student_id: int, fields: dict[str, Any]) -> None:
        clean_fields = {
            key: value
            for key, value in fields.items()
            if key in STUDENT_EDITABLE_COLUMNS
        }
        if not clean_fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in clean_fields)
        values = list(clean_fields.values())
        values.extend([utc_now_iso(), project_id, student_id])
        with self.transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE students
                SET {assignments}, updated_at = ?
                WHERE project_id = ? AND id = ?
                """,
                values,
            )
            if cursor.rowcount == 0:
                raise ValueError("התלמיד/ה לא נמצא/ת בפרויקט.")
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (utc_now_iso(), project_id))

    def save_import_mappings(self, project_id: int, mapping: dict[str, str]) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM import_mappings WHERE project_id = ?", (project_id,))
            now = utc_now_iso()
            for field_name, original_column in mapping.items():
                if not original_column:
                    continue
                connection.execute(
                    """
                    INSERT INTO import_mappings
                        (project_id, original_column_name, mapped_field_name, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, original_column, field_name, now),
                )

    def get_import_mappings(self, project_id: int) -> dict[str, str]:
        rows = self.connection.execute(
            "SELECT mapped_field_name, original_column_name FROM import_mappings WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        return {str(row["mapped_field_name"]): str(row["original_column_name"]) for row in rows}

    def replace_validation_issues(self, project_id: int, issues: Iterable[ValidationIssue]) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM validation_issues WHERE project_id = ?", (project_id,))
            for issue in issues:
                cursor = connection.execute(
                    """
                    INSERT INTO validation_issues
                        (project_id, student_id, field_name, severity, message, resolved, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        issue.student_id,
                        issue.field_name,
                        issue.severity,
                        issue.message,
                        int(issue.resolved),
                        issue.created_at,
                    ),
                )
                issue.id = int(cursor.lastrowid)

    def get_validation_issues(self, project_id: int, include_resolved: bool = False) -> list[ValidationIssue]:
        if include_resolved:
            rows = self.connection.execute(
                "SELECT * FROM validation_issues WHERE project_id = ? ORDER BY severity, id",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM validation_issues
                WHERE project_id = ? AND resolved = 0
                ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id
                """,
                (project_id,),
            ).fetchall()
        return [self._issue_from_row(row) for row in rows]

    def replace_friendships(self, project_id: int, requests: Iterable[tuple[int, int, int]]) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM friendship_requests WHERE project_id = ?", (project_id,))
            for student_id, friend_id, priority in requests:
                connection.execute(
                    """
                    INSERT INTO friendship_requests
                        (project_id, student_id, requested_friend_id, priority)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, student_id, friend_id, priority),
                )

    def get_friendships(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM friendship_requests WHERE project_id = ? ORDER BY student_id, priority",
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def replace_class_constraints(self, project_id: int, constraints: Iterable[dict[str, Any]]) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM class_constraints WHERE project_id = ?", (project_id,))
            for item in constraints:
                connection.execute(
                    """
                    INSERT INTO class_constraints
                        (project_id, student_id, allowed_classes_json, forbidden_classes_json, locked_class_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        int(item["student_id"]),
                        _json_dumps(item.get("allowed_classes", [])),
                        _json_dumps(item.get("forbidden_classes", [])),
                        item.get("locked_class_id"),
                    ),
                )

    def get_class_constraints(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM class_constraints WHERE project_id = ?", (project_id,)
        ).fetchall()
        constraints: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["allowed_classes"] = _json_loads(data.pop("allowed_classes_json"), [])
            data["forbidden_classes"] = _json_loads(data.pop("forbidden_classes_json"), [])
            constraints.append(data)
        return constraints

    def replace_pair_constraints(
        self,
        project_id: int,
        together: Iterable[tuple[int, int, str]],
        separation: Iterable[tuple[int, int, str]],
    ) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM together_constraints WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM separation_constraints WHERE project_id = ?", (project_id,))
            for student_id, other_id, reason in together:
                connection.execute(
                    """
                    INSERT INTO together_constraints
                        (project_id, student_id, other_student_id, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, student_id, other_id, reason),
                )
            for student_id, other_id, reason in separation:
                connection.execute(
                    """
                    INSERT INTO separation_constraints
                        (project_id, student_id, other_student_id, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, student_id, other_id, reason),
                )

    def get_pair_constraints(self, project_id: int) -> dict[str, list[dict[str, Any]]]:
        together = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM together_constraints WHERE project_id = ?", (project_id,)
            ).fetchall()
        ]
        separation = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM separation_constraints WHERE project_id = ?", (project_id,)
            ).fetchall()
        ]
        return {"together": together, "separation": separation}

    def save_assignment_version(
        self,
        project_id: int,
        name: str,
        assignments: dict[int, int],
        score: dict[str, Any],
        notes: str = "",
        locked_student_ids: set[int] | None = None,
        changed_student_ids: set[int] | None = None,
    ) -> int:
        locked_student_ids = locked_student_ids or set()
        changed_student_ids = changed_student_ids or set()
        now = utc_now_iso()
        with self.transaction() as connection:
            self._validate_assignment_payload(connection, project_id, assignments, score)
            connection.execute(
                "UPDATE assignment_versions SET is_active = 0 WHERE project_id = ?",
                (project_id,),
            )
            cursor = connection.execute(
                """
                INSERT INTO assignment_versions
                    (project_id, name, created_at, score_total, score_json, notes, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    project_id,
                    name,
                    now,
                    float(score.get("total_score", 0)),
                    _json_dumps(score),
                    notes,
                ),
            )
            version_id = int(cursor.lastrowid)
            for student_id, class_id in assignments.items():
                connection.execute(
                    """
                    INSERT INTO assignments
                        (project_id, version_id, student_id, class_id, locked_manually, changed_manually, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        version_id,
                        student_id,
                        class_id,
                        int(student_id in locked_student_ids),
                        int(student_id in changed_student_ids),
                        now,
                        now,
                    ),
                )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
        return version_id

    def update_assignment_version(
        self,
        project_id: int,
        version_id: int,
        assignments: dict[int, int],
        score: dict[str, Any],
        notes: str = "",
        locked_student_ids: set[int] | None = None,
        changed_student_ids: set[int] | None = None,
    ) -> None:
        locked_student_ids = locked_student_ids or set()
        changed_student_ids = changed_student_ids or set()
        now = utc_now_iso()
        with self.transaction() as connection:
            self._validate_assignment_payload(connection, project_id, assignments, score)
            exists = connection.execute(
                "SELECT id FROM assignment_versions WHERE project_id = ? AND id = ?",
                (project_id, version_id),
            ).fetchone()
            if not exists:
                raise ValueError("גרסת שיבוץ לא נמצאה.")
            connection.execute(
                """
                UPDATE assignment_versions
                SET score_total = ?, score_json = ?, notes = ?
                WHERE project_id = ? AND id = ?
                """,
                (float(score.get("total_score", 0)), _json_dumps(score), notes, project_id, version_id),
            )
            connection.execute("DELETE FROM assignments WHERE version_id = ?", (version_id,))
            for student_id, class_id in assignments.items():
                connection.execute(
                    """
                    INSERT INTO assignments
                        (project_id, version_id, student_id, class_id, locked_manually, changed_manually, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        version_id,
                        student_id,
                        class_id,
                        int(student_id in locked_student_ids),
                        int(student_id in changed_student_ids),
                        now,
                        now,
                    ),
                )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))

    def _validate_assignment_payload(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        assignments: dict[int, int],
        score: dict[str, Any],
    ) -> None:
        expected_student_ids = {
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM students WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        }
        assignment_student_ids = {int(student_id) for student_id in assignments}
        if assignment_student_ids != expected_student_ids:
            missing = len(expected_student_ids - assignment_student_ids)
            extra = len(assignment_student_ids - expected_student_ids)
            raise ValueError(
                f"השיבוץ חייב לכלול כל תלמיד בדיוק פעם אחת. חסרים: {missing}, לא שייכים לפרויקט: {extra}."
            )

        class_ids = {int(class_id) for class_id in assignments.values()}
        if class_ids:
            placeholders = ",".join("?" for _ in class_ids)
            valid_class_ids = {
                int(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM classes WHERE project_id = ? AND id IN ({placeholders})",
                    (project_id, *class_ids),
                ).fetchall()
            }
            if class_ids != valid_class_ids:
                raise ValueError("השיבוץ כולל כיתה שאינה שייכת לפרויקט.")

    def _validate_assignment_capacity_rules(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        assignments: dict[int, int],
    ) -> None:
        project_row = connection.execute("SELECT settings_json FROM projects WHERE id = ?", (project_id,)).fetchone()
        settings = {**DEFAULT_RULE_SETTINGS, **_json_loads(project_row["settings_json"], {})} if project_row else DEFAULT_RULE_SETTINGS
        class_rows = connection.execute("SELECT id, name, max_students FROM classes WHERE project_id = ?", (project_id,)).fetchall()
        class_by_id = {int(row["id"]): row for row in class_rows}
        student_rows = connection.execute("SELECT id, gender FROM students WHERE project_id = ?", (project_id,)).fetchall()
        gender_by_student = {int(row["id"]): row["gender"] or "" for row in student_rows}

        class_counts: dict[int, int] = defaultdict(int)
        gender_counts: dict[int, dict[str, int]] = defaultdict(lambda: {GENDER_MALE: 0, GENDER_FEMALE: 0})
        for raw_student_id, raw_class_id in assignments.items():
            student_id = int(raw_student_id)
            class_id = int(raw_class_id)
            class_counts[class_id] += 1
            gender = gender_by_student.get(student_id, "")
            if gender in (GENDER_MALE, GENDER_FEMALE):
                gender_counts[class_id][gender] += 1

        global_class_max = _settings_int(settings, "max_students_per_class", 0)
        gender_max = _settings_int(settings, "max_students_per_gender", 0)
        violations: list[str] = []
        for class_id, count in class_counts.items():
            class_row = class_by_id.get(class_id)
            if not class_row:
                continue
            class_max = int(class_row["max_students"] or 0)
            max_values = [value for value in (class_max, global_class_max) if value > 0]
            effective_max = min(max_values) if max_values else 0
            class_name = class_row["name"] or str(class_id)
            if effective_max and count > effective_max:
                violations.append(f"{class_name}: {count} תלמידים מעל המקסימום {effective_max}")
            if gender_max:
                for gender, gender_count in gender_counts[class_id].items():
                    if gender_count > gender_max:
                        violations.append(f"{class_name}: {gender_count} תלמידים ממגדר {gender} מעל המקסימום {gender_max}")
        if violations:
            raise ValueError("אי אפשר לשמור שיבוץ שחורג מחוקי הקיבולת: " + "; ".join(violations[:4]))

    def get_assignment_versions(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM assignment_versions
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (project_id,),
        ).fetchall()
        versions: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["score"] = _json_loads(data.pop("score_json"), {})
            data["is_active"] = bool(data["is_active"])
            versions.append(data)
        return versions

    def get_active_assignment_version(self, project_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM assignment_versions
            WHERE project_id = ? AND is_active = 1
            ORDER BY id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["score"] = _json_loads(data.pop("score_json"), {})
        data["is_active"] = bool(data["is_active"])
        return data

    def set_active_assignment_version(self, project_id: int, version_id: int) -> None:
        with self.transaction() as connection:
            exists = connection.execute(
                "SELECT id FROM assignment_versions WHERE project_id = ? AND id = ?",
                (project_id, version_id),
            ).fetchone()
            if not exists:
                raise ValueError("גרסת שיבוץ לא נמצאה.")
            connection.execute(
                "UPDATE assignment_versions SET is_active = 0 WHERE project_id = ?",
                (project_id,),
            )
            connection.execute(
                "UPDATE assignment_versions SET is_active = 1 WHERE id = ?",
                (version_id,),
            )

    def rename_assignment_version(self, project_id: int, version_id: int, name: str) -> None:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("שם ההרצה לא יכול להיות ריק.")
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE assignment_versions
                SET name = ?
                WHERE project_id = ? AND id = ?
                """,
                (clean_name[:120], project_id, version_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("גרסת שיבוץ לא נמצאה.")

    def get_assignments(self, version_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM assignments WHERE version_id = ? ORDER BY student_id",
            (version_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_active_assignment_rows(self, project_id: int) -> list[dict[str, Any]]:
        active = self.get_active_assignment_version(project_id)
        if not active:
            return []
        rows = self.connection.execute(
            """
            SELECT
                a.*,
                s.internal_code,
                s.first_name,
                s.last_name,
                s.full_name,
                s.gender,
                s.source_school,
                s.math_grade,
                s.english_grade,
                s.hebrew_grade,
                s.average_grade,
                s.behavior_score,
                s.dominance_score,
                s.parent_notes,
                s.teacher_notes,
                s.interview_notes,
                c.name AS class_name
            FROM assignments a
            JOIN students s ON s.id = a.student_id AND s.project_id = a.project_id
            JOIN classes c ON c.id = a.class_id AND c.project_id = a.project_id
            WHERE a.version_id = ? AND a.project_id = ?
            ORDER BY c.id, s.last_name, s.first_name, s.full_name
            """,
            (active["id"], project_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _project_from_row(self, row: sqlite3.Row) -> Project:
        settings = {**DEFAULT_RULE_SETTINGS, **_json_loads(row["settings_json"], {})}
        return Project(
            id=int(row["id"]),
            name=row["name"] or "",
            grade_level=row["grade_level"] or "",
            school_year=row["school_year"] or "",
            settings=settings,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _class_from_row(self, row: sqlite3.Row) -> ClassGroup:
        return ClassGroup(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            name=row["name"] or "",
            min_students=int(row["min_students"] or 0),
            max_students=int(row["max_students"] or 0),
            target_students=int(row["target_students"] or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _student_from_row(self, row: sqlite3.Row) -> Student:
        return Student(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            internal_code=row["internal_code"] or "",
            stable_key=row["stable_key"] or "",
            first_name=row["first_name"] or "",
            last_name=row["last_name"] or "",
            full_name=row["full_name"] or "",
            gender=row["gender"] or "",
            source_school=row["source_school"] or "",
            math_grade=row["math_grade"],
            english_grade=row["english_grade"],
            hebrew_grade=row["hebrew_grade"],
            average_grade=row["average_grade"],
            behavior_score=row["behavior_score"] or "",
            dominance_score=row["dominance_score"],
            parent_notes=row["parent_notes"] or "",
            teacher_notes=row["teacher_notes"] or "",
            interview_notes=row["interview_notes"] or "",
            raw_data=_json_loads(row["raw_data_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _issue_from_row(self, row: sqlite3.Row) -> ValidationIssue:
        return ValidationIssue(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            student_id=row["student_id"],
            field_name=row["field_name"] or "",
            severity=row["severity"] or "info",
            message=row["message"] or "",
            resolved=bool(row["resolved"]),
            created_at=row["created_at"],
        )
