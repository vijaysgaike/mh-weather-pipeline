"""Analytics materializations and quality metrics derived transactionally from Silver."""

import sqlite3
from pathlib import Path

from weather_pipeline.storage import atomic_csv

QUERIES = {
    "daily_city_summary": """
        SELECT date, city, max(district) district, max(division) division, max(region) region,
        count(*) observation_count,
        avg(temp_celsius) avg_temp_celsius, min(temp_celsius) min_temp_celsius,
        max(temp_celsius) max_temp_celsius, avg(humidity_pct) avg_humidity_pct,
        avg(wind_speed_mps) avg_wind_speed_mps, avg(rain_1h_mm) avg_rain_1h_mm,
        max(rain_1h_mm) max_rain_1h_mm
        FROM weather_observations GROUP BY date,city""",
    "district_rollup": """
        SELECT date, district, count(DISTINCT city) city_count, count(*) observation_count,
        avg(temp_celsius) avg_temp_celsius, avg(humidity_pct) avg_humidity_pct
        FROM weather_observations GROUP BY date,district""",
    "division_rollup": """
        SELECT date, division, count(DISTINCT city) city_count, count(*) observation_count,
        avg(temp_celsius) avg_temp_celsius, avg(humidity_pct) avg_humidity_pct
        FROM weather_observations GROUP BY date,division""",
    "latest_conditions": """
        SELECT w.* FROM weather_observations w JOIN
        (SELECT city,max(timestamp_utc) latest FROM weather_observations GROUP BY city) t
        ON w.city=t.city AND w.timestamp_utc=t.latest""",
}


def refresh_gold(connection: sqlite3.Connection, run_id: str, now: str) -> int:
    """Refresh all working Gold tables and append one run's quality snapshot."""
    for name, query in QUERIES.items():
        connection.execute(f"CREATE TABLE IF NOT EXISTS {name} AS {query} LIMIT 0")
        connection.execute(f"DELETE FROM {name}")
        connection.execute(f"INSERT INTO {name} {query}")
    connection.execute("""CREATE TABLE IF NOT EXISTS data_quality_metrics (
        run_id TEXT PRIMARY KEY, measured_at TEXT, silver_rows INTEGER,
        quarantined_rows INTEGER, missing_optional_values INTEGER, stale_cities INTEGER)""")
    connection.execute(
        """INSERT OR REPLACE INTO data_quality_metrics
        SELECT ?,?, (SELECT count(*) FROM weather_observations),
        (SELECT count(*) FROM quarantine), (SELECT count(*) FROM quality_warnings),
        (SELECT count(*) FROM latest_conditions WHERE julianday(?) -
         julianday(timestamp_utc) > 3.0/24)""",
        (run_id, now, now),
    )
    return sum(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in QUERIES)


def export_gold(connection: sqlite3.Connection, root: Path) -> None:
    """Publish reproducible convenience exports; SQLite remains authoritative."""
    for name in [*QUERIES, "data_quality_metrics"]:
        cursor = connection.execute(f"SELECT * FROM {name} ORDER BY 1,2")
        columns = [item[0] for item in cursor.description]
        atomic_csv(root / f"{name}.csv", columns, iter(cursor))
