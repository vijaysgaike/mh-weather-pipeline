"""Publish a Git-friendly Silver snapshot or merge it back into runtime state."""

import argparse
import csv
import gzip
import hashlib
import itertools
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from weather_pipeline.backfill import file_hash
from weather_pipeline.config import Settings, load_settings
from weather_pipeline.logging_setup import configure_logging
from weather_pipeline.models import COLUMNS
from weather_pipeline.pipeline import Pipeline, inspect_status
from weather_pipeline.storage import atomic_csv, writer_lock

MAX_GIT_FILE_BYTES = 95 * 1024**2
MAX_RESTORE_BYTES = 2 * 1024**3


def publish(settings: Settings, destination: Path) -> dict[str, Any]:
    """Produce deterministic full gzip and daily CSVs from synchronized Silver."""
    destination = destination.resolve()
    if (destination / "pyproject.toml").exists() or (destination / ".env").exists():
        raise ValueError("publish into a dedicated data checkout, not the source repository")
    protected = [
        settings.historical_db,
        settings.historical_csv,
        settings.city_reference,
        settings.working_db,
        settings.export_csv,
    ]
    if any(destination == p.resolve() or destination in p.resolve().parents for p in protected):
        raise ValueError("publication directory cannot contain source or working files")
    destination.mkdir(parents=True, exist_ok=True)
    with writer_lock(settings.working_db):
        if inspect_status(settings)["status"] != "ready":
            raise ValueError("publish requires synchronized validated runtime state")
        snapshot = destination / "weather_dataset.csv.gz"
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as temporary:
            temp = Path(temporary.name)
        try:
            with temp.open("wb") as output:
                with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
                    with settings.export_csv.open("rb") as source:
                        shutil.copyfileobj(source, compressed)
                output.flush()
                os.fsync(output.fileno())
            if temp.stat().st_size >= MAX_GIT_FILE_BYTES:
                raise ValueError(
                    "full compressed snapshot exceeds the publication file-size budget"
                )
            temp.replace(snapshot)
        finally:
            temp.unlink(missing_ok=True)
        connection = sqlite3.connect(settings.working_db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            cursor = connection.execute(
                f"SELECT {','.join(COLUMNS)} FROM weather_observations "
                "ORDER BY date,observation_id",
            )
            date_index = COLUMNS.index("date")
            partitions = []
            for day, rows in itertools.groupby(cursor, key=lambda row: str(row[date_index])):
                parsed = date.fromisoformat(day)
                relative = f"daily/{parsed:%Y}/{parsed:%m}/{day}.csv"
                path = destination / relative
                atomic_csv(path, COLUMNS, rows)
                if path.stat().st_size >= MAX_GIT_FILE_BYTES:
                    raise ValueError("daily CSV exceeds the publication file-size budget")
                partitions.append(
                    {"path": relative, "sha256": file_hash(path), "bytes": path.stat().st_size}
                )
            count, latest = connection.execute(
                "SELECT count(*),max(timestamp_utc) FROM weather_observations",
            ).fetchone()
        finally:
            connection.close()
        manifest = {
            "format_version": 1,
            "row_count": count,
            "latest_timestamp_utc": latest,
            "columns": COLUMNS,
            "snapshot": {
                "path": snapshot.name,
                "sha256": file_hash(snapshot),
                "csv_sha256": file_hash(settings.export_csv),
                "csv_bytes": settings.export_csv.stat().st_size,
            },
            "daily_files": partitions,
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (destination / "README.md").write_text(
            "# Maharashtra weather data\n\n"
            "Automatically updated every four hours by the pipeline on the main branch.\n\n"
            "- Full accumulated dataset: [weather_dataset.csv.gz](weather_dataset.csv.gz) "
            "(download and decompress to get the 37-column CSV).\n"
            "- Browse smaller, uncompressed files under [daily/](daily/), "
            "grouped by year/month/day.\n"
            "- Row count, latest timestamp, schema and SHA-256 checksums: "
            "[manifest.json](manifest.json).\n\n"
            "Existing accepted observations are preserved; "
            "each update adds unique city/time records.\n"
            "No-change runs do not create data commits. Database audit/lineage and raw responses "
            "are retained separately in workflow artifacts.\n\n"
            "Weather source: [Open-Meteo](https://open-meteo.com/), plus the historical dataset "
            "described in the main branch. The code license does not license third-party data.\n",
        )
        return manifest


def restore(settings: Settings, source: Path) -> dict[str, Any]:
    """Verify/decompress the published full snapshot and merge it transactionally."""
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("format_version") != 1 or manifest.get("columns") != COLUMNS:
        raise ValueError("unsupported published data contract")
    snapshot = manifest["snapshot"]
    if snapshot.get("path") != "weather_dataset.csv.gz":
        raise ValueError("unexpected published snapshot path")
    path = source / snapshot["path"]
    if file_hash(path) != snapshot["sha256"]:
        raise ValueError("published gzip checksum mismatch")
    digest = hashlib.sha256()
    size = 0
    with tempfile.TemporaryDirectory() as scratch:
        csv_path = Path(scratch) / "published.csv"
        with gzip.open(path, "rb") as incoming, csv_path.open("wb") as output:
            while chunk := incoming.read(1024**2):
                size += len(chunk)
                if size > MAX_RESTORE_BYTES:
                    raise ValueError("published CSV exceeds restore size budget")
                digest.update(chunk)
                output.write(chunk)
        if size != snapshot["csv_bytes"] or digest.hexdigest() != snapshot["csv_sha256"]:
            raise ValueError("published CSV checksum/size mismatch")
        with csv_path.open(newline="") as handle:
            reader = csv.reader(handle)
            if next(reader, None) != COLUMNS or sum(1 for _ in reader) != manifest["row_count"]:
                raise ValueError("published CSV schema/row count mismatch")
        return Pipeline(settings).merge_published_csv(csv_path)


def main() -> int:
    """Expose publication/restoration commands for GitHub Actions and local setup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["publish", "restore"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()
    logger = configure_logging()
    try:
        settings = load_settings(args.config)
        result = (publish if args.command == "publish" else restore)(settings, args.directory)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError):
        logging.getLogger("weather_pipeline").exception("GitHub data command failed")
        return 2
    logger.info(
        "GitHub data %s completed: %s",
        args.command,
        result.get("row_count", result.get("silver_out")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
