The independent check of your last work failed (attempt $attempt of $max_attempts).

Failed checks on commit $sha:

$failures

The full report with logs and screenshots is in `$report_dir` (start with `report.md`).

Find the root cause of each failure and fix it. Do not hide errors (for example by deleting features or silencing output); the game must keep working. Change a failing scenario only when the scenario itself is wrong, and say why in `docs/PROGRESS.md`. Keep `docs/CONTRACT.yaml` and the scenarios in `tests/scenarios/` in sync with the game (add a scenario for anything new or fixed). Run all self-check commands, including the scenarios, before you finish and update `docs/PROGRESS.md`.
