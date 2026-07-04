# Hardening Status

Updated: 2026-06-21

This document is the current implementation status for product hardening. Older planning and compliance documents are historical context; when they conflict, this file should be treated as the fresher status note.

## Implemented

- CP-SAT gender balancing uses canonical normalized gender values.
- Assignment scoring exposes both a normalized display score and an uncut raw objective.
- Engine and service candidate ranking share one `score_rank` implementation.
- Hard-constraint assignments cannot be saved as active success.
- Assignment rows are guarded against cross-project student/class/version mixing by validation, unique indexes and SQLite triggers.
- Replacing classes invalidates assignment versions in the same transaction.
- Active assignment dashboards and reports require one assignment row per student.
- SQLite initialization records schema migrations in `schema_migrations`.
- Student imports use stable keys/upsert behavior instead of full delete-all rebuilds when possible.
- CSV and XLSX imports enforce basic file, row, column, XML and ZIP safety limits.
- CSV preview reads as a stream and preserves the detected dialect.
- Gemini receives the JSON schema and API keys are sent in headers rather than query strings.
- AI provider calls are bounded by provider limit and multi-provider requests run in parallel.
- AI token writes reject newline injection; user token storage tries OS keychain first and falls back to a protected `.env`.
- Grade parsing is explicit about ranges and fractions.
- `Student.grade_value` uses `average_grade` first, otherwise the mean of known subject grades.
- Fuzzy import mapping is one-to-one across all headers and fields.
- Dominance values outside 0..100 are reported by validation.

## Partial

- Objective consistency is improved, but CP-SAT, local construction and display scoring are not yet generated from a single `ObjectiveDefinition`.
- Local search has a first-improvement path for larger inputs, but does not yet have full delta scoring, tabu, 3-cycle, friend-cluster moves or LNS.
- Solver telemetry is stored in score metadata for CP-SAT, but not yet surfaced fully in all UI/report views.
- Keychain storage is supported through `keyring` when available, with `.env` fallback. Full secret migration/delete UX is still not implemented.
- Import upsert preserves student identity where stable keys can be derived. Full import preview/diff/undo is still planned.

## Planned

- Full migration tests across real older database snapshots.
- Dedicated `ObjectiveDefinition` shared by CP-SAT, local search, reports and explanations.
- Delta scoring with class aggregates.
- Benchmark suite for 30/100/300 student datasets.
- Split `bridge.py` into view-model/controller modules.
- Split `Main.qml` into screens/components and remove unqualified QML warnings.
- Full OS keychain management UI and secret deletion.
- CI gates for dependency audit, security checks, mypy and QML lint.
