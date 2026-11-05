"""Read-only supplied-data inspection; write a report under docs/."""

import csv
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

root = Path(__file__).resolve().parents[1]


def profile(rows: Iterable[Mapping[str, Any]], names: list[str]) -> dict[str, Any]:
    """Count missing values, duplicate keys, and normalized timestamp ranges."""
    nulls = Counter()
    ids = Counter()
    keys = Counter()
    normalized = Counter()
    times = []
    cities = set()
    count = 0
    for row in rows:
        count += 1
        for name, value in row.items():
            if value is None or str(value).strip().lower() in ("", "null", "nan", "none"):
                nulls[name] += 1
        if "observation_id" in row:
            ids[str(row["observation_id"])] += 1
        if "timestamp_utc" in row:
            ts = str(row["timestamp_utc"])
            times.append(datetime.fromisoformat(ts).isoformat())
            keys[(row["city"], ts)] += 1
            normalized[(row["city"], datetime.fromisoformat(ts).isoformat())] += 1
            cities.add(row["city"])
    return dict(
        rows=count,
        null_counts={n: nulls[n] for n in names},
        min_timestamp=min(times) if times else None,
        max_timestamp=max(times) if times else None,
        excess_duplicate_ids=sum(v - 1 for v in ids.values()),
        excess_duplicate_city_timestamp=sum(v - 1 for v in keys.values()),
        excess_duplicate_normalized_city_timestamp=sum(v - 1 for v in normalized.values()),
        distinct_cities=len(cities),
    )


report = {
    "engine": "SQLite 3",
    "access": "read-only; source files untouched",
    "tables": {},
    "csv": {},
}
c = sqlite3.connect((root / "data/weather.db").as_uri() + "?mode=ro", uri=True)
c.row_factory = sqlite3.Row
for item in c.execute("select name, sql from sqlite_master where type='table'").fetchall():
    name = item["name"]
    quoted = '"' + name.replace('"', '""') + '"'
    names = [r["name"] for r in c.execute("pragma table_info(" + quoted + ")")]
    report["tables"][name] = dict(
        schema_sql=item["sql"],
        **profile((dict(r) for r in c.execute("select * from " + quoted)), names),
    )
for path in (root / "data").glob("*.csv"):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        report["csv"][path.name] = dict(
            columns=reader.fieldnames, **profile(reader, reader.fieldnames)
        )
# Compare every CSV cell with the corresponding DB cell using schema-specific numeric conversion.
columns = report["csv"]["weather_dataset.csv"]["columns"]
types = {r["name"]: r["type"] for r in c.execute("pragma table_info(weather_observations)")}
mismatches = 0
with (root / "data/weather_dataset.csv").open(newline="") as f:
    for row in csv.DictReader(f):
        db = c.execute(
            "select * from weather_observations where observation_id=?", (row["observation_id"],)
        ).fetchone()
        if db is None:
            mismatches += 1
            continue
        for k in columns:
            value = row[k]
            if value == "" and db[k] is None:
                continue
            try:
                same = float(value) == db[k] if types[k] in ("REAL", "INTEGER") else value == db[k]
            except ValueError:
                same = False
            if not same:
                mismatches += 1
                break
report["csv_db_comparison"] = {
    "rows_with_missing_or_different_values": mismatches,
    "comparison": "all 37 columns, numeric types normalized, blank CSV equals SQL NULL",
}
(root / "docs/data-inspection.json").write_text(json.dumps(report, indent=2) + "\n")
