# Inspection and proposed design — approval checkpoint

Historical approved proposal. For completed behavior and verification, see
[completion.md](completion.md) and the current README.

## Existing data

The complete schema, per-column null counts, table counts, timestamp ranges,
and duplicate counts are recorded in `data-inspection.json`. Inspection opens
SQLite in read-only mode and does not change the supplied files.

- `weather.db`: SQLite; user tables `weather_observations`,
  `weather_dataset_updated`, `etl_load_audit`; internal table `sqlite_sequence`.
- Main table and weather CSV: 210,592 records, 37 matching columns, 218 cities.
  Timestamp range: 2026-06-01 00:00 through 2026-11-05 05:45 (columns declare UTC;
  timezone-naive source values must be treated according to that contract).
- Main table and CSV have unique observation IDs but 5,903 excess rows sharing
  exact city/timestamp keys. `division` and `region` each have 1,876 missing
  values; other weather columns have no null/blank values.
- Legacy weather table: 9,440 rows, June 1–20, 480 excess duplicate IDs,
  720 excess city/timestamp rows, and 80 nulls each for division and region.
- Audit table: 898 rows; all error messages are null. Load timestamps span
  June 21–November 5, 2026. Internal sequence table: two rows.
- City reference: 224 rows; missing division/region in two rows, city_id in
  three, state in 165, and csv_lat/csv_lon in 161 each. It has a UTF-8 BOM;
  load with utf-8-sig. Use lat/lon, subject to range validation.
- Observation IDs differ in declared type across tables (INTEGER vs TEXT).
  Timestamp and ingestion timestamp formats are mixed.

## Proposed implementation

Keep the project at the current workspace root and retain SQLite. Preserve
all supplied files as immutable inputs. Before migration, create verified
backups; place the working database at `data/silver/weather.db` and the
compatible 37-column export at `data/silver/weather_dataset.csv`.

Default city scope is the 218 cities in the weather history. Reconcile the
224-row reference by name, district, and coordinates; document unmatched or
ambiguous entries rather than adding cities silently.

Use Open-Meteo's forecast endpoint, with UTC timestamps and explicit units.
Fetch hourly weather through the latest completed hour, plus daily sunrise,
sunset and min/max temperatures. Use a bounded overlap window on subsequent
runs to recover missed observations. Document the archive/backlog strategy
separately for gaps beyond forecast API retention. No API key is required.
Open-Meteo provides model-derived weather; preserve that source distinction.

Map WMO weather codes to compatible weather_main/weather_desc/severity fields.
Keep native WMO codes and units in provenance outside the 37-column export;
use a documented mapping for legacy weather_id semantics. Missing source
fields stay null with an explicit quality reason; do not invent measurements.
Daily min/max values and hourly visibility/rain fields need documented
semantics, since the former provider's fields are not exact equivalents.

Bronze stores raw response bytes unchanged, partitioned by ingestion date/hour,
with separate immutable request/run metadata. Silver validates and normalizes
UTC, deduplicates by city + timestamp, retains original IDs in provenance,
and allocates stable numeric IDs for new records. Import main CSV/DB once,
reconcile their contents, and inspect legacy-only rows before including them.
Quarantine duplicates/conflicts with source lineage and deterministic selection;
retain original source data. Freshness rules apply to new ingestion, while
historical backfill is checked against historical rules.

Gold holds daily city summaries, district/division rollups, latest conditions,
and quality metrics. The working SQLite DB also stores Bronze metadata,
quarantine, source lineage, and pipeline_runs.

SQLite transactions and atomic CSV replacement cannot form a single filesystem
transaction. Use a single-writer lock and a durable export generation marker:
commit Silver, generate a temporary CSV, replace atomically, then mark the
export complete. On failure report pending export and repair on restart before
new ingestion. Validate matching keys, row counts and content; never report a
successful synchronized run until the export matches the DB.

Use argparse, requests, pydantic, python-dotenv, pandas as needed, plus the
requested tooling. To avoid an extra YAML dependency, initially support a
clearly documented JSON-compatible YAML config (JSON is valid YAML); adopting
a general YAML parser requires approval under the dependency restriction.
All tests use fixtures, temporary sinks, and mocked HTTP; CI checks ruff, mypy,
and pytest with >=85% source coverage. Scheduled runs upload durable artifacts
with documented restoration; ephemeral CI runners must not reset pipeline
history between executions.

## Milestones

This is the pre-implementation inspection checkpoint within M1. After approval,
finish M1 scaffolding, config, logging, tooling, CI, tests, and exact GitHub setup
commands. Stop for "continue" after each subsequent milestone as requested:
M2 Bronze; M3 Silver/backfill; M4 dual sinks/recovery; M5 Gold/run metrics;
M6 CLI/docs/scheduled ingestion. No live ingestion has been performed yet.

API reference: https://open-meteo.com/en/docs
