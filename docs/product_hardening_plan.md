# Product Hardening Plan

## Baseline Safety

- Git branch: unavailable; `D:\classmaker` is not a Git repository.
- Backup created: `D:\classmaker\_backups\product-hardening-baseline-20260617-203109`.
- Baseline screenshots: `docs/baseline_screenshots/`.
- Screenshot note: offscreen Qt capture works at 1024x700, 1280x820, 1366x768 and 1920x1080. Real Windows 125%, 150% and 200% display scaling still needs manual verification; offscreen capture cannot faithfully emulate monitor scaling.
- UI rendering note: the offscreen baseline screenshots show Hebrew glyph boxes, so font/RTL rendering must be verified on a real desktop session.

## Baseline Quality Gates

- `python -m pytest`: initially failed when run from the whole workspace because the local `_backups` copy duplicated test module names. Tool config now scopes pytest to `tests`.
- `python -m pytest tests`: passed, 9 tests.
- `python -m coverage run -m pytest tests`: initially collected tests but unscoped reporting failed with `No source for code: 'D:\classmaker\pyscript'`. Tool config now scopes coverage to `class_balancer`.
- `python -m coverage run --source=class_balancer -m pytest tests` plus `python -m coverage report`: passed, total application coverage 62%.
- `python -m ruff check class_balancer tests scripts`: failed with 4 baseline findings; these are now fixed.
- `python -m bandit -r class_balancer tests scripts`: failed with 8 medium and 3 low findings.
- `python -m compileall -q class_balancer tests`: passed.
- `pyside6-qmllint class_balancer\ui\qml\Main.qml`: completed with thousands of warnings, mostly unqualified access in the monolithic QML file.

## Implementation Phases

1. Preflight hard-constraint engine and P0 solver safety.
2. Regression tests for empty domains, contradictory pair constraints, lock conflicts, hard capacity contradictions and friendship-hard impossibility.
3. CP-SAT objective rebuild with directed at-least-one friendship satisfaction and structured solver metadata.
4. Normalized scoring and evidence-based explanations.
5. Safe manual preview/commit APIs.
6. Database migrations, stable identity, import diff/rollback and stale-version tracking.
7. AI privacy and secret storage hardening.
8. QML split into typed, maintainable pages/components.
9. Teacher-first workflow, RTL/accessibility and responsive verification.
10. Final QA, benchmarks, documentation and screenshot report.

## Current Phase Notes

The first completed slice adds a dedicated preflight feasibility engine and blocks critical infeasibility before CP-SAT/local search can run or an assignment version can be saved. Remaining phases are intentionally not hidden as complete; they require broader model, database, UI and privacy work.

## Verification After Current Slice

- `python -m pytest`: passed, 17 tests.
- `python -m coverage run -m pytest` plus `python -m coverage report`: passed, total application coverage 64%.
- `python -m ruff check .`: passed.
- `python -m compileall -q class_balancer tests`: passed.
- `python -m class_balancer --smoke`: passed.
- `python -m bandit -r class_balancer tests scripts`: still reports 8 medium and 3 low findings; these are not suppressed because they represent real future hardening work.
- `pyside6-qmllint class_balancer\ui\qml\Main.qml`: completes with the existing large set of unqualified-access warnings.
