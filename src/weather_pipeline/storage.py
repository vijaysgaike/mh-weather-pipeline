"""SQLite transactions, writer locking, quarantine, and recoverable CSV exports."""

import csv
import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from weather_pipeline.models import COLUMNS, INTEGER_COLUMNS, REAL_COLUMNS, Observation

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL,
    export_generation INTEGER NOT NULL);
INSERT OR IGNORE INTO state VALUES(1,0,-1);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY, command TEXT NOT NULL, started_at TEXT NOT NULL,
    ended_at TEXT, status TEXT NOT NULL, bronze_in INTEGER DEFAULT 0,
    bronze_out INTEGER DEFAULT 0, bronze_rejected INTEGER DEFAULT 0,
    silver_in INTEGER DEFAULT 0, silver_out INTEGER DEFAULT 0,
    silver_rejected INTEGER DEFAULT 0, silver_skipped INTEGER DEFAULT 0,
    gold_in INTEGER DEFAULT 0, gold_out INTEGER DEFAULT 0, gold_rejected INTEGER DEFAULT 0,
    cities_failed INTEGER DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS bronze_metadata (
    raw_path TEXT PRIMARY KEY, run_id TEXT NOT NULL, city TEXT NOT NULL,
    metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quarantine (
    fingerprint TEXT PRIMARY KEY, run_id TEXT NOT NULL, source TEXT NOT NULL,
    reason TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lineage (
    observation_id INTEGER PRIMARY KEY REFERENCES weather_observations(observation_id),
    source TEXT NOT NULL, source_id TEXT, raw_path TEXT, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS city_failures (
    run_id TEXT NOT NULL, city TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY(run_id,city));
CREATE TABLE IF NOT EXISTS imports (
    fingerprint TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, completed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quality_warnings (
    observation_id INTEGER NOT NULL, field TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY(observation_id,field));
"""


@contextmanager
def writer_lock(database: Path) -> Iterator[None]:
    """Allow one process across all sinks; fail immediately rather than race."""
    database.parent.mkdir(parents=True, exist_ok=True)
    lock = database.with_suffix(database.suffix + ".lock")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another pipeline writer is active") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def connect(database: Path) -> sqlite3.Connection:
    """Open the working DB with bounded lock waiting and foreign keys."""
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    definitions = []
    for name in COLUMNS:
        kind = "INTEGER" if name in INTEGER_COLUMNS else "REAL" if name in REAL_COLUMNS else "TEXT"
        suffix = " PRIMARY KEY AUTOINCREMENT" if name == "observation_id" else ""
        if name in {"city", "timestamp_utc", "temp_celsius", "humidity_pct", "ingestion_timestamp"}:
            suffix += " NOT NULL"
        definitions.append(f"{name} {kind}{suffix}")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS weather_observations ("
        + ",".join(definitions)
        + ", UNIQUE(city,timestamp_utc))",
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS silver_time ON weather_observations(timestamp_utc)",
    )
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def quarantine(
    connection: sqlite3.Connection,
    run_id: str,
    source: str,
    reason: str,
    payload: Any,
) -> bool:
    """Idempotently retain rejected content and its source/reason in SQLite."""
    encoded = json.dumps(payload, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(f"{source}|{reason}|{encoded}".encode()).hexdigest()
    cursor = connection.execute(
        "INSERT OR IGNORE INTO quarantine VALUES(?,?,?,?,?)",
        (fingerprint, run_id, source, reason, encoded),
    )
    return cursor.rowcount == 1


def insert_observation(
    connection: sqlite3.Connection,
    observation: Observation,
    source: str,
    source_id: str | None,
    raw_path: str | None,
    metadata: dict[str, Any],
) -> tuple[bool, bool]:
    """Append once by city/time; return (inserted, conflicting_existing_row)."""
    row = observation.model_dump()
    existing = connection.execute(
        "SELECT * FROM weather_observations WHERE city=? AND timestamp_utc=?",
        (observation.city, observation.timestamp_utc),
    ).fetchone()
    if existing is not None:
        # API revisions never overwrite accepted data. Ingestion times are provenance only.
        ignored = {"observation_id", "ingestion_timestamp", "record_created_at"}
        return False, any(existing[key] != row[key] for key in COLUMNS if key not in ignored)
    candidate_id = row["observation_id"]
    if (
        candidate_id is not None
        and connection.execute(
            "SELECT 1 FROM weather_observations WHERE observation_id=?",
            (candidate_id,),
        ).fetchone()
    ):
        row["observation_id"] = None
    cursor = connection.execute(
        f"INSERT INTO weather_observations ({','.join(COLUMNS)}) "
        f"VALUES ({','.join('?' for _ in COLUMNS)})",
        tuple(row[key] for key in COLUMNS),
    )
    observation_id = cursor.lastrowid
    connection.execute(
        "INSERT INTO lineage VALUES(?,?,?,?,?)",
        (observation_id, source, source_id, raw_path, json.dumps(metadata, sort_keys=True)),
    )
    from weather_pipeline.quality import missing_fields

    connection.executemany(
        "INSERT INTO quality_warnings VALUES(?,?,?)",
        [
            (observation_id, field, "source value unavailable")
            for field in missing_fields(observation)
        ],
    )
    connection.execute("UPDATE state SET generation=generation+1 WHERE id=1")
    return True, False


def fsync_directory(path: Path) -> None:
    """Make atomic replacement durable on Linux filesystems."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_csv(path: Path, columns: Sequence[str], rows: Iterator[Sequence[Any]]) -> None:
    """Write beside the destination, fsync and atomically replace; clean failed temps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=".export-",
            delete=False,
        ) as handle:
            name = handle.name
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        fsync_directory(path.parent)
    finally:
        if name is not None and Path(name).exists():
            Path(name).unlink()


def sync_errors(connection: sqlite3.Connection, path: Path) -> list[str]:
    """Stream-compare every export cell and key with ordered authoritative Silver."""
    if not path.exists():
        return ["CSV export missing"]
    cursor = connection.execute(
        f"SELECT {','.join(COLUMNS)} FROM weather_observations ORDER BY observation_id",
    )
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            if next(reader, None) != COLUMNS:
                return ["CSV header mismatch"]
            for index, db_row in enumerate(cursor, 1):
                expected = ["" if value is None else str(value) for value in db_row]
                if next(reader, None) != expected:
                    return [f"CSV content mismatch at data row {index}"]
            if next(reader, None) is not None:
                return ["CSV contains extra rows"]
    except (OSError, UnicodeError, csv.Error) as exc:
        return [f"CSV unreadable: {type(exc).__name__}"]
    return []


def export_csv(connection: sqlite3.Connection, path: Path) -> None:
    """Publish Silver and only then acknowledge the current export generation."""
    cursor = connection.execute(
        f"SELECT {','.join(COLUMNS)} FROM weather_observations ORDER BY observation_id",
    )
    atomic_csv(path, COLUMNS, iter(cursor))
    if sync_errors(connection, path):
        raise RuntimeError("export verification failed")
    with connection:
        connection.execute("UPDATE state SET export_generation=generation WHERE id=1")


def repair_export(connection: sqlite3.Connection, path: Path) -> bool:
    """Repair interrupted or externally modified exports before further ingestion."""
    state = connection.execute("SELECT generation,export_generation FROM state").fetchone()
    if state[0] != state[1] or sync_errors(connection, path):
        export_csv(connection, path)
        return True
    return False
