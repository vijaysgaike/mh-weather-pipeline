# Runbook

## Installation and first run

```bash
make install
make check
.venv/bin/weather-pipeline backfill
.venv/bin/weather-pipeline validate
.venv/bin/weather-pipeline run
.venv/bin/weather-pipeline status
```

No API key is required. Keep the existing .env; supported WEATHER_ settings are
listed in README.md. The source CSV, DB and city reference remain read-only
inputs. Backfill creates verified copies under data/backups before importing
into data/silver/weather.db. The CSV export is data/silver/weather_dataset.csv.
The two mirrors are compared over all 37 columns; mismatches are recorded in
the import manifest. Main DB wins by ascending observation ID, then the legacy
DB table, then CSV. Identical records are counted as skipped; conflicting keys
are quarantined. Unseen valid keys from any source are added. Original files,
backup manifests, import metadata, source IDs and rejected content retain history.

Backfill is content-idempotent. Repeating it makes another immutable snapshot
but does not append already imported data. Large import transactions deliberately
roll back as a unit; live ingestion commits one city at a time. Memory is bounded
by one source row, one API response and streaming database/CSV cursors.

## Normal operations

```bash
.venv/bin/weather-pipeline run --city Pune
.venv/bin/weather-pipeline run --limit-cities 2
.venv/bin/weather-pipeline export-csv
.venv/bin/weather-pipeline validate
```

Use --config PATH before the command for another JSON-compatible YAML config.
City filters apply only to run. Defaults cover all cities accepted from history;
reference-only cities are not silently introduced. Requests use per-city
watermarks with a six-hour overlap and a maximum seven-day lookback. Full UTC
calendar days are fetched, then current/future hours are excluded. Only completed
hours are ingested. New rows older than eight days or future timestamps fail DQ.
Latest accepted conditions must be within three hours; failed/stale cities are
recorded and attempted again next run. Forecast model revisions for existing
keys are retained in Bronze/quarantine rather than overwriting Silver.

Gaps older than seven days are **not** automatically repaired. Restore an older
state and provide a validated historical source/backfill, or build a separately
reviewed archive-API adapter. Status/quality metrics report stale cities; a
successful bounded ingest does not prove historical continuity.

The single-writer lock covers DB and every export. Readers may temporarily see
an old complete CSV after the DB commits. This cannot be one cross-filesystem
transaction. A durable generation marker remains pending until atomic CSV
replacement and full comparison succeed. Run export-csv after a failure; every
mutating command also repairs a stale/missing/modified CSV before new work.
Status and validate are read-only and never repair. SQLite is authoritative.
Gold exports are individually atomic convenience files and can be regenerated.
Do not edit the working CSV manually.

Exit codes: 0 success; 1 partial city failure, invalid quality/sync, or missing
initialization; 2 configuration/storage/task failure. Historical quarantine
counts alone do not fail a successful import: inspect reasons before analysis.
Failed commands retain run metadata. A later writer marks leftover running
runs interrupted after acquiring the lock. Fatal storage failures stop ingestion;
API/timeouts/schema failures are isolated per city. Logs are JSON on stderr,
with run_id, UTC timestamp and exception details. Never put secrets in config.

## Inspection queries

```sql
SELECT * FROM pipeline_runs ORDER BY rowid DESC LIMIT 5;
SELECT reason, count(*) FROM quarantine GROUP BY reason;
SELECT * FROM city_failures ORDER BY rowid DESC LIMIT 20;
SELECT * FROM data_quality_metrics ORDER BY rowid DESC LIMIT 5;
SELECT * FROM latest_conditions;
SELECT source, count(*) FROM lineage GROUP BY source;
SELECT * FROM imports;
```

Backups and Bronze files are append-only. If a crash lands a raw body before its
metadata transaction, it can leave an unindexed raw object. Preserve it for
forensics; rerunning ingestion safely requests the interval again. Back up the
working SQLite database with SQLite's backup API, not a plain copy while WAL
writes may be active. Tests block network and always use temporary data.
