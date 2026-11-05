"""Composable orchestration of independent pipeline stages under one writer lock."""

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from weather_pipeline.api_client import ApiError, OpenMeteoClient
from weather_pipeline.backfill import canonical_source, historical_rows, reconcile, snapshot_sources
from weather_pipeline.bronze import land_raw
from weather_pipeline.config import Settings
from weather_pipeline.gold import export_gold, refresh_gold
from weather_pipeline.logging_setup import configure_logging
from weather_pipeline.models import City
from weather_pipeline.quality import validate_observation
from weather_pipeline.silver import api_rows, enrich, parse_payload
from weather_pipeline.storage import (
    connect,
    export_csv,
    insert_observation,
    quarantine,
    repair_export,
    sync_errors,
    writer_lock,
)

COUNTERS = (
    "bronze_in bronze_out bronze_rejected silver_in silver_out silver_rejected silver_skipped "
    "gold_in gold_out gold_rejected cities_failed"
).split()


def city_catalog(connection: sqlite3.Connection, names: list[str]) -> list[City]:
    """Use latest accepted metadata for each historical name; never add reference-only names."""
    rows = connection.execute("""SELECT city,district,division,region,country,latitude,longitude
        FROM latest_conditions ORDER BY city""").fetchall()
    cities = [City.model_validate(dict(row)) for row in rows]
    available = {city.city for city in cities}
    if names and set(names) - available:
        raise ValueError("unknown requested cities: " + ", ".join(sorted(set(names) - available)))
    return [city for city in cities if not names or city.city in names]


class Pipeline:
    """A single-run coordinator; every stage can also be invoked independently."""

    def __init__(
        self,
        settings: Settings,
        now: datetime | None = None,
        client: OpenMeteoClient | None = None,
    ) -> None:
        """Bind deterministic time and an injectable HTTP client for offline tests."""
        self.settings = settings
        self.now = now or datetime.now(UTC)
        self.client = client
        self.run_id = str(uuid4())
        self.counts = dict.fromkeys(COUNTERS, 0)
        self.logger: logging.Logger = configure_logging(settings.log_level, self.run_id)

    def _reject(
        self,
        connection: sqlite3.Connection,
        source: str,
        reason: str,
        payload: Any,
    ) -> None:
        quarantine(connection, self.run_id, source, reason, payload)
        self.counts["silver_rejected"] += 1

    def _accept(
        self,
        connection: sqlite3.Connection,
        row: dict[str, Any],
        source: str,
        source_id: str | None = None,
        raw_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        historical: bool = False,
    ) -> None:
        self.counts["silver_in"] += 1
        try:
            validated = validate_observation(
                row if historical else enrich(row),
                self.now,
                historical=historical,
            )
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reason = "invalid row: " + str(exc)
            self._reject(connection, source, reason, {"row": row, "source_id": source_id})
            return
        inserted, conflict = insert_observation(
            connection,
            validated,
            source,
            source_id,
            raw_path,
            metadata or {},
        )
        if inserted:
            self.counts["silver_out"] += 1
        elif conflict and historical:
            self._reject(
                connection, source, "duplicate key with conflicting historical values", row
            )
        elif conflict:
            # Retain API revisions in Bronze, without rewriting the accepted row.
            self._reject(connection, source, "duplicate key with revised source values", row)
        else:
            self.counts["silver_skipped"] += 1

    def _execute(self, command: str, task: Callable[[sqlite3.Connection], None]) -> dict[str, Any]:
        with writer_lock(self.settings.working_db):
            connection = connect(self.settings.working_db)
            try:
                with connection:
                    # A killed prior process cannot leave a successful run behind.
                    connection.execute(
                        "UPDATE pipeline_runs SET status='interrupted',ended_at=? "
                        "WHERE status='running'",
                        (self.now.isoformat(),),
                    )
                    connection.execute(
                        "INSERT INTO pipeline_runs(run_id,command,started_at,status) "
                        "VALUES(?,?,?,'running')",
                        (self.run_id, command, self.now.isoformat()),
                    )
                try:
                    if repair_export(connection, self.settings.export_csv):
                        self.logger.info("CSV export recovered or initialized")
                    task(connection)
                    with connection:
                        self.counts["gold_in"] = connection.execute(
                            "SELECT count(*) FROM weather_observations",
                        ).fetchone()[0]
                        self.counts["gold_out"] = refresh_gold(
                            connection,
                            self.run_id,
                            self.now.isoformat(),
                        )
                    export_csv(connection, self.settings.export_csv)
                    export_gold(connection, self.settings.gold_dir)
                    status = "partial" if self.counts["cities_failed"] else "success"
                except Exception as exc:
                    connection.rollback()
                    self._finish(connection, "failed", type(exc).__name__)
                    self.logger.exception("Pipeline failed; accepted DB batches remain recoverable")
                    raise
                self._finish(connection, status)
                result = {"run_id": self.run_id, "status": status, **self.counts}
                self.logger.info("Run completed: %s", json.dumps(result, sort_keys=True))
                return result
            finally:
                connection.close()

    def _finish(
        self,
        connection: sqlite3.Connection,
        status: str,
        error: str | None = None,
    ) -> None:
        with connection:
            assignments = ",".join(f"{key}=?" for key in COUNTERS)
            connection.execute(
                f"UPDATE pipeline_runs SET ended_at=?,status=?,error=?,{assignments} "
                "WHERE run_id=?",
                (
                    datetime.now(UTC).isoformat(),
                    status,
                    error,
                    *(self.counts[key] for key in COUNTERS),
                    self.run_id,
                ),
            )

    def backfill(self) -> dict[str, Any]:
        """Snapshot/reconcile supplied sources and import history once by content fingerprint."""
        return self._execute("backfill", self._backfill)

    def _backfill(self, connection: sqlite3.Connection) -> None:
        manifest = snapshot_sources(self.settings, self.now)
        reconciliation = reconcile(manifest)
        manifest["reconciliation"] = reconciliation
        fingerprint = "|".join(
            manifest["files"][kind]["sha256"] for kind in ("csv", "db", "reference")
        )
        if connection.execute(
            "SELECT 1 FROM imports WHERE fingerprint=?", (fingerprint,)
        ).fetchone():
            self.logger.info("Historical snapshot already imported; no Silver changes")
            return
        reference: dict[tuple[str, str], list[dict[str, Any]]] = {}
        import csv

        with Path(manifest["files"]["reference"]["path"]).open(
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            for item in csv.DictReader(handle):
                for name in {item.get("tehsil", ""), item.get("city_name", "")}:
                    reference.setdefault(
                        (name.casefold(), item.get("district", "").casefold()), []
                    ).append(item)
        self.logger.info("Historical reconciliation: %s", json.dumps(reconciliation))
        # Main DB first, ascending IDs: deterministic earliest-ID winner for historical duplicates.
        # The entire historical import and its completed marker share one transaction.
        with connection:
            for source, raw in historical_rows(manifest):
                try:
                    row = canonical_source(raw)
                except (ValueError, KeyError, TypeError) as exc:
                    self.counts["silver_in"] += 1
                    self._reject(connection, source, f"invalid source scalar: {exc}", raw)
                    continue
                original_id = str(raw.get("observation_id", ""))
                if not isinstance(row["observation_id"], int):
                    row["observation_id"] = None
                # Fill missing administrative metadata only on an unambiguous name+district match.
                matches = reference.get(
                    (str(row["city"]).casefold(), str(row["district"]).casefold()), []
                )
                if len(matches) == 1:
                    for field in ("division", "region"):
                        row[field] = row[field] or matches[0].get(field) or None
                self._accept(
                    connection,
                    row,
                    source,
                    original_id,
                    metadata={"import_fingerprint": fingerprint, "original_id": original_id},
                    historical=True,
                )
            connection.execute(
                "INSERT INTO imports VALUES(?,?,?)",
                (
                    fingerprint,
                    json.dumps(manifest, sort_keys=True),
                    self.now.isoformat(),
                ),
            )

    def run(self, names: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
        """Fetch recent complete hours for historical cities, continuing after city failures."""

        def task(connection: sqlite3.Connection) -> None:
            if not connection.execute("SELECT 1 FROM weather_observations LIMIT 1").fetchone():
                raise ValueError("Silver is empty; run backfill before ingestion")
            # Gold may be stale after a prior interrupted import.
            with connection:
                refresh_gold(connection, self.run_id, self.now.isoformat())
            cities = city_catalog(connection, names or self.settings.cities)
            if limit is not None:
                cities = cities[:limit]
            client = self.client or OpenMeteoClient(self.settings)
            try:
                for index, city in enumerate(cities, 1):
                    self._ingest_city(connection, client, city)
                    if index % self.settings.batch_size == 0 or index == len(cities):
                        self.logger.info("Cities processed: %s/%s", index, len(cities))
            finally:
                if self.client is None:
                    client.close()

        return self._execute("run", task)

    def _ingest_city(
        self,
        connection: sqlite3.Connection,
        client: OpenMeteoClient,
        city: City,
    ) -> None:
        latest = connection.execute(
            "SELECT max(timestamp_utc) FROM weather_observations WHERE city=?",
            (city.city,),
        ).fetchone()[0]
        start = self.now - timedelta(days=self.settings.lookback_days)
        if latest:
            start = max(
                start, datetime.fromisoformat(latest) - timedelta(hours=self.settings.overlap_hours)
            )
        end = self.now.date().isoformat()
        self.counts["bronze_in"] += 1
        try:
            fetched = client.fetch(city, start.date().isoformat(), end)
        except ApiError as exc:
            if exc.response is not None:
                self._land(connection, exc.response, city)
            self.counts["bronze_rejected"] += 1
            self._city_failure(connection, city.city, str(exc))
            return
        metadata = self._land(connection, fetched, city)
        raw_path = str(metadata["raw_path"])
        try:
            payload = parse_payload(fetched.body)
            rows = api_rows(payload, city, self.now)
        except (ValueError, TypeError, KeyError) as exc:
            with connection:
                self._reject(connection, "open-meteo", f"invalid API payload: {exc}", metadata)
            self.counts["bronze_rejected"] += 1
            self._city_failure(connection, city.city, "invalid API payload")
            return
        with connection:
            for row, provenance in rows:
                self._accept(connection, row, "open-meteo", raw_path=raw_path, metadata=provenance)
        newest = connection.execute(
            "SELECT max(timestamp_utc) FROM weather_observations WHERE city=?",
            (city.city,),
        ).fetchone()[0]
        if newest is None or datetime.fromisoformat(newest) < self.now - timedelta(
            hours=self.settings.freshness_hours,
        ):
            self._city_failure(connection, city.city, "latest accepted observation is stale")
        self.logger.info("City processed: %s", city.city)

    def _land(self, connection: sqlite3.Connection, fetched: Any, city: City) -> dict[str, Any]:
        metadata = land_raw(self.settings.bronze_dir, fetched, city.city, self.run_id, self.now)
        with connection:
            connection.execute(
                "INSERT INTO bronze_metadata VALUES(?,?,?,?)",
                (
                    metadata["raw_path"],
                    self.run_id,
                    city.city,
                    json.dumps(metadata, sort_keys=True),
                ),
            )
        self.counts["bronze_out"] += 1
        return metadata

    def _city_failure(self, connection: sqlite3.Connection, city: str, reason: str) -> None:
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO city_failures VALUES(?,?,?)", (self.run_id, city, reason)
            )
        self.counts["cities_failed"] += 1
        self.logger.error("City failed: %s; %s; will retry next run", city, reason)

    def export(self) -> dict[str, Any]:
        """Repair/publish synchronized Silver and Gold exports."""
        return self._execute("export-csv", lambda connection: None)


def inspect_status(settings: Settings, *, validate: bool = False) -> dict[str, Any]:
    """Read status/quality without creating a database, directories, or export."""
    if not settings.working_db.exists():
        return {"status": "not_initialized", "next": "weather-pipeline backfill"}
    connection = sqlite3.connect(settings.working_db.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        count = connection.execute("SELECT count(*) FROM weather_observations").fetchone()[0]
        last = connection.execute(
            "SELECT * FROM pipeline_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        state = dict(connection.execute("SELECT * FROM state").fetchone())
        errors = sync_errors(connection, settings.export_csv)
        if state["generation"] != state["export_generation"]:
            errors.append("CSV generation pending")
        if validate:
            for record in connection.execute("SELECT * FROM weather_observations"):
                try:
                    validate_observation(dict(record), datetime.now(UTC), historical=True)
                except (ValidationError, ValueError):
                    errors.append(f"invalid Silver row {record['observation_id']}")
                    break
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                errors.append("SQLite integrity check failed")
        return {
            "status": "invalid" if errors else "ready",
            "silver_rows": count,
            "quarantined_rows": connection.execute("SELECT count(*) FROM quarantine").fetchone()[0],
            "errors": errors,
            "last_run": dict(last) if last else None,
            **state,
        }
    finally:
        connection.close()
