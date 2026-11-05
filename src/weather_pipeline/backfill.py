"""Verified source snapshots, reconciliation and deterministic historical import."""

import csv
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from weather_pipeline.config import Settings
from weather_pipeline.models import COLUMNS, INTEGER_COLUMNS, REAL_COLUMNS


def file_hash(path: Path) -> str:
    """Hash a file with bounded memory."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def snapshot_sources(settings: Settings, now: datetime) -> dict[str, Any]:
    """Copy CSV/reference and SQLite online backup before reading migration inputs."""
    folder = settings.backup_dir / (now.strftime("%Y%m%dT%H%M%S%f") + "-" + uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    files: dict[str, Any] = {}
    for kind, source in (
        ("csv", settings.historical_csv),
        ("db", settings.historical_db),
        ("reference", settings.city_reference),
    ):
        if not source.is_file():
            raise FileNotFoundError(f"required historical input missing: {source.name}")
        destination = folder / f"{kind}-{source.name}"
        if kind == "db":
            with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original:
                with sqlite3.connect(destination) as backup:
                    original.backup(backup)
            with sqlite3.connect(destination) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite backup integrity check failed")
        else:
            before = file_hash(source)
            shutil.copy2(source, destination)
            if before != file_hash(destination) or before != file_hash(source):
                raise ValueError("source changed during snapshot")
        files[kind] = {
            "source": str(source),
            "path": str(destination),
            "sha256": file_hash(destination),
        }
    # SQLite online backup can change file layout; logical contents are compared below.
    manifest = {"created_at": now.isoformat(), "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def canonical_source(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize CSV scalar types for an exact comparison with source SQLite."""
    result: dict[str, Any] = {}
    for key in COLUMNS:
        value = row[key]
        if value is None or str(value).strip().lower() in {"", "nan", "null", "none"}:
            result[key] = None
        elif key in INTEGER_COLUMNS:
            # Legacy tables may have nonnumeric observation identifiers.
            try:
                result[key] = int(str(value))
            except ValueError:
                if key != "observation_id":
                    raise
                result[key] = str(value)
        elif key in REAL_COLUMNS:
            result[key] = float(value)
        else:
            result[key] = str(value)
    return result


def reconcile(manifest: dict[str, Any]) -> dict[str, int]:
    """Compare all CSV rows with main-table rows by ID, without modifying either."""
    db_path = Path(manifest["files"]["db"]["path"])
    csv_path = Path(manifest["files"]["csv"]["path"])
    mismatches, csv_count = 0, 0
    seen: set[str] = set()
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != COLUMNS:
                raise ValueError("historical CSV header differs from the 37-column contract")
            for row in reader:
                csv_count += 1
                key = row["observation_id"]
                existing = connection.execute(
                    "SELECT * FROM weather_observations WHERE observation_id=?",
                    (key,),
                ).fetchone()
                try:
                    same = existing is not None and canonical_source(row) == canonical_source(
                        dict(existing),
                    )
                except (ValueError, KeyError):
                    same = False
                mismatches += not same or key in seen
                seen.add(key)
        db_count = connection.execute("SELECT count(*) FROM weather_observations").fetchone()[0]
    return {"csv_rows": csv_count, "db_rows": db_count, "mismatches": mismatches}


def historical_rows(manifest: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Prefer main DB ordered by ID, then reconcile CSV and the legacy table."""
    database = Path(manifest["files"]["db"]["path"])
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in ("weather_observations", "weather_dataset_updated"):
            if table in tables:
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY observation_id,rowid"
                ):
                    yield f"historical:{table}", dict(row)
    with Path(manifest["files"]["csv"]["path"]).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            yield "historical:csv", dict(row)
