# M2–M6 completion and validation

The user authorized completing the remaining milestones without pauses. The
original milestone-by-milestone waiting rule was updated in AGENTS.md accordingly.

| Milestone | Implemented files / behavior |
|---|---|
| M2 | api_client.py, bronze.py, raw fixtures/API tests: explicit UTC/units, timeout, pooled HTTP, throttling, bounded retries, provider cooldowns, immutable raw/error response landing |
| M3 | models.py, silver.py, quality.py, backfill.py: all 37 fields, WMO mapping, NOAA heat index/categories, physical/calendar validation, source snapshots and reconciliation, unique normalized city/time, quarantine, original-ID lineage |
| M4 | storage.py: SQLite constraints/transactions, single writer lock, atomic fsync/replace CSV, full-content sync checks, durable export generations, interrupted-export recovery |
| M5 | gold.py, pipeline.py: daily city summaries, district/division rollups, latest conditions, data-quality metrics, per-run layer counts, per-city failures, interrupted run handling |
| M6 | cli.py, scripts/state_archive.py, scheduled_ingest.yml, Makefile, README, architecture, dictionary, runbook, scheduled operations and tests |

## Checks

`make check` runs Ruff lint/format checks over src, tests and scripts, strict
mypy over src, and pytest with coverage. Tests forbid network and use temporary
source copies/sinks. Measured coverage and the final test count are recorded in
the completion response; coverage.xml is generated locally and uploaded by CI.
Final local result: **143 passed, 98.38% source statement coverage**; Ruff format/lint
and strict mypy passed. The configured gate remains 85%. Python 3.12 is checked locally, with Python
3.11/3.12 in CI. Workflow files also passed YAML syntax parsing locally; live
GitHub schedule setup requires the initial state/enablement described below.

Integration checks cover exact fixture outputs, repeated import/run idempotency,
append preserving accepted rows, daily aggregates, CSV/DB cell comparison,
concurrent writers, DB transaction rollback, failed atomic replacement with old
CSV preserved, recovery after failure, manual CSV tampering, API timeouts, HTTP
errors, malformed payloads, stale observations, partial cities and subsequent
retries, optional null warnings, historical mirror conflicts, original source
immutability, legacy string IDs, portable restore, checksums and unsafe archives.

## Real-data exercise — November 5, 2026

- Supplied CSV/main DB: 210,592 records each, all 37 fields reconciled with zero
  mismatches. Legacy table also considered. Verified source snapshots created
  before migration; all original files remain unchanged.
- Backfill: 204,689 unique accepted records. Conflicting historical copies are
  retained in quarantine (12,286 records across main/legacy/CSV source identities);
  exact repeated records are counted as skipped. The supplied originals are the
  complete immutable history of the rejected/duplicate input rows.
- Live smoke test: two cities, 78 records appended, zero city failures.
- Full live run: all 218 cities, another 8,424 records appended, 30 repeated
  records skipped, zero city/API/DQ failures.
- Working Silver and compatible CSV: 213,191 records. Every latest city condition
  is 2026-11-05 14:00 UTC (19:30 Asia/Kolkata).
- Full read-only validation passed with no CSV/content, schema/range/calendar,
  or SQLite integrity errors. Export generations match. Gold was regenerated
  from the accepted data. History quarantine remains available for inspection.
- A portable snapshot is available locally at data/silver/weather-state.zip.
  Its complete 213,191-row restore was exercised in a temporary directory and
  validated. State and datasets are gitignored; only code/docs/synthetic fixtures
  belong in the repository.

## Operational handoff

Run `.venv/bin/weather-pipeline run` to append another bounded live batch, and
`.venv/bin/weather-pipeline validate` to check it. Gaps beyond seven days need
an explicit historical source/archive adapter; this pipeline does not claim to
repair arbitrary historical gaps automatically.

For GitHub scheduling, upload the local state archive to an HTTPS location you
control, provide WEATHER_SEED_URL, manually seed the workflow, then enable the
WEATHER_SCHEDULE_ENABLED repository variable. No dataset upload, public data
release, or GitHub secret/settings change is performed by project code setup.
See scheduled-ingestion.md for durable-state expiry, backup and access trade-offs.
