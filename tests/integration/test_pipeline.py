"""Real SQLite/CSV tests, with all HTTP behavior replaced by mock sessions."""

import csv
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from weather_pipeline.api_client import OpenMeteoClient
from weather_pipeline.backfill import canonical_source, file_hash
from weather_pipeline.config import Settings
from weather_pipeline.models import COLUMNS
from weather_pipeline.pipeline import Pipeline, city_catalog, inspect_status
from weather_pipeline.storage import connect, sync_errors, writer_lock

NOW = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)


def fake_client(settings: Settings, payload: dict[str, Any]) -> OpenMeteoClient:
    session = Mock()
    session.get.return_value = Mock(
        content=json.dumps(payload).encode(), status_code=200, headers={}
    )
    return OpenMeteoClient(settings, session)


def read_rows(settings: Settings) -> list[tuple[Any, ...]]:
    with sqlite3.connect(settings.working_db) as db:
        return db.execute("SELECT * FROM weather_observations ORDER BY observation_id").fetchall()


def test_full_run_idempotency_append_and_gold(
    settings: Settings,
    payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("weather_pipeline.api_client.time.sleep", Mock())
    originals = {
        path: file_hash(path) for path in (settings.historical_csv, settings.historical_db)
    }
    result = Pipeline(settings, NOW).backfill()
    assert result["silver_out"] == 1
    assert result["silver_skipped"] == 1  # Same CSV/DB record is reconciled.
    original_rows = read_rows(settings)
    before = settings.export_csv.read_bytes()
    second = Pipeline(settings, NOW).backfill()
    assert second["silver_out"] == 0
    assert settings.export_csv.read_bytes() == before
    assert {path: file_hash(path) for path in originals} == originals
    client = fake_client(settings, payload)
    first = Pipeline(settings, NOW, client).run()
    assert first["status"] == "success"
    assert first["silver_out"] == 2
    rows = read_rows(settings)
    assert rows[:1] == original_rows
    assert len(rows) == 3
    repeated = Pipeline(settings, NOW, client).run()
    assert repeated["silver_out"] == 0
    assert repeated["silver_skipped"] == 2
    assert read_rows(settings) == rows
    with connect(settings.working_db) as db:
        assert sync_errors(db, settings.export_csv) == []
        daily = db.execute("SELECT * FROM daily_city_summary WHERE date='2026-11-05'").fetchone()
        assert daily["observation_count"] == 2
        assert daily["avg_temp_celsius"] == 31
        assert daily["max_rain_1h_mm"] == 0.5
        latest = db.execute("SELECT * FROM latest_conditions").fetchone()
        assert latest["timestamp_utc"] == "2026-11-05T11:00:00+00:00"
        assert db.execute("SELECT count(*) FROM district_rollup").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM division_rollup").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM lineage").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM bronze_metadata").fetchone()[0] == 2
        assert (
            db.execute(
                "SELECT silver_rows FROM data_quality_metrics ORDER BY rowid DESC"
            ).fetchone()[0]
            == 3
        )
    assert inspect_status(settings, validate=True)["status"] == "ready"
    assert len(list(settings.gold_dir.glob("*.csv"))) == 5
    raw = list(settings.bronze_dir.rglob("*.json"))
    assert len(raw) == 4


def test_quarantine_and_optional_missing(
    settings: Settings,
    payload: dict[str, Any],
) -> None:
    Pipeline(settings, NOW).backfill()
    payload["hourly"]["humidity_pct"] = [
        101
    ] * 4  # Unrequested extra arrays cannot override fields.
    payload["hourly"]["relative_humidity_2m"][0] = 101
    del payload["hourly"]["visibility"]
    result = Pipeline(settings, NOW, fake_client(settings, payload)).run()
    assert result["silver_out"] == 1
    assert result["silver_rejected"] == 1
    with connect(settings.working_db) as db:
        assert "humidity_pct" in db.execute("SELECT reason FROM quarantine").fetchone()[0]
        assert db.execute("SELECT field FROM quality_warnings").fetchone()[0] == "visibility_m"


@pytest.mark.parametrize("failure", ["timeout", "http", "json", "stale"])
def test_partial_city_failures(
    settings: Settings,
    payload: dict[str, Any],
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("weather_pipeline.api_client.time.sleep", Mock())
    Pipeline(settings, NOW).backfill()
    with connect(settings.working_db) as db:
        row = dict(db.execute("SELECT * FROM weather_observations").fetchone())
        row.update(city="Mumbai", observation_id=2)
        db.execute(
            f"INSERT INTO weather_observations ({','.join(COLUMNS)}) VALUES "
            f"({','.join('?' for _ in COLUMNS)})",
            tuple(row[k] for k in COLUMNS),
        )
    session = Mock()
    good = Mock(content=json.dumps(payload).encode(), status_code=200, headers={})
    bad: Any
    if failure == "timeout":
        bad = requests.Timeout()
    elif failure == "http":
        bad = Mock(content=b'{"error":true}', status_code=400, headers={})
    elif failure == "json":
        bad = Mock(content=b"broken", status_code=200, headers={})
    else:
        stale = json.loads(json.dumps(payload))
        stale["hourly"]["time"] = [
            "2026-10-01T10:00",
            "2026-10-01T11:00",
            "2026-10-01T12:00",
            "2026-10-01T13:00",
        ]
        bad = Mock(content=json.dumps(stale).encode(), status_code=200, headers={})
    session.get.side_effect = [bad, good]
    client = OpenMeteoClient(settings.model_copy(update={"max_retries": 0}), session)
    result = Pipeline(settings, NOW, client).run()
    assert result["status"] == "partial"
    assert result["cities_failed"] == 1
    assert result["silver_out"] == 2
    with connect(settings.working_db) as db:
        assert db.execute("SELECT city FROM city_failures").fetchone()[0] == "Mumbai"
        assert (
            db.execute("SELECT status FROM pipeline_runs ORDER BY rowid DESC").fetchone()[0]
            == "partial"
        )
        assert not sync_errors(db, settings.export_csv)
    # No permanent blacklist: a fresh run tries both cities again.
    repaired = Pipeline(settings, NOW, fake_client(settings, payload)).run()
    assert repaired["cities_failed"] == 0


def test_export_failure_and_recovery(
    settings: Settings,
    payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Pipeline(settings, NOW).backfill()
    original_csv = settings.export_csv.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            "weather_pipeline.storage.os.replace", Mock(side_effect=OSError("disk error"))
        )
        with pytest.raises(OSError):
            Pipeline(settings, NOW, fake_client(settings, payload)).run()
    assert settings.export_csv.read_bytes() == original_csv
    assert len(read_rows(settings)) == 3
    assert not list(settings.export_csv.parent.glob(".export-*"))
    status = inspect_status(settings)
    assert status["status"] == "invalid"
    assert status["last_run"]["status"] == "failed"
    assert "CSV generation pending" in status["errors"]
    Pipeline(settings, NOW).export()
    assert inspect_status(settings, validate=True)["status"] == "ready"


def test_csv_tampering_is_detected_and_repaired(settings: Settings) -> None:
    Pipeline(settings, NOW).backfill()
    settings.export_csv.write_text("wrong-header\n")
    assert inspect_status(settings)["errors"] == ["CSV header mismatch"]
    Pipeline(settings, NOW).export()
    assert inspect_status(settings)["status"] == "ready"
    settings.export_csv.unlink()
    assert inspect_status(settings)["errors"] == ["CSV export missing"]
    Pipeline(settings, NOW).export()
    assert inspect_status(settings)["status"] == "ready"


def test_concurrent_writers_and_empty_run(settings: Settings) -> None:
    with writer_lock(settings.working_db), pytest.raises(RuntimeError, match="writer"):
        Pipeline(settings, NOW).backfill()
    with pytest.raises(ValueError, match="backfill"):
        Pipeline(settings, NOW).run()
    assert inspect_status(settings)["last_run"]["status"] == "failed"


def test_unknown_city_and_filters(settings: Settings, payload: dict[str, Any]) -> None:
    Pipeline(settings, NOW).backfill()
    with connect(settings.working_db) as db, pytest.raises(ValueError, match="unknown"):
        city_catalog(db, ["Unknown"])
    result = Pipeline(settings, NOW, fake_client(settings, payload)).run(["Pune"], 1)
    assert result["bronze_in"] == 1


def test_historical_conflicts_and_invalid_rows(
    settings: Settings, history_row: dict[str, Any]
) -> None:
    # Simulate divergent mirrors, a legacy string identifier and bad scalar content.
    changed = {**history_row, "temp_celsius": 28}
    invalid = {**history_row, "observation_id": 4, "temp_celsius": "bad"}
    with settings.historical_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows([changed, invalid])
    with sqlite3.connect(settings.historical_db) as db:
        db.execute("CREATE TABLE weather_dataset_updated AS SELECT * FROM weather_observations")
        db.execute("UPDATE weather_dataset_updated SET observation_id='legacy-id',city='Nagpur'")
    result = Pipeline(settings, NOW).backfill()
    assert result["silver_out"] == 2
    assert result["silver_rejected"] == 2
    with connect(settings.working_db) as db:
        assert (
            db.execute(
                "SELECT temp_celsius FROM weather_observations WHERE city='Pune'"
            ).fetchone()[0]
            == 25
        )
        assert (
            db.execute("SELECT source_id FROM lineage WHERE source LIKE '%updated'").fetchone()[0]
            == "legacy-id"
        )
        imported = json.loads(db.execute("SELECT manifest_json FROM imports").fetchone()[0])
        assert imported["reconciliation"]["mismatches"] == 2


def test_transaction_rollback(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected transformation crash")

    monkeypatch.setattr(Pipeline, "_accept", crash)
    with pytest.raises(RuntimeError):
        Pipeline(settings, NOW).backfill()
    assert read_rows(settings) == []
    with connect(settings.working_db) as db:
        assert db.execute("SELECT count(*) FROM imports").fetchone()[0] == 0


def test_validate_detects_invalid_db_rows(settings: Settings) -> None:
    Pipeline(settings, NOW).backfill()
    with sqlite3.connect(settings.working_db) as db:
        db.execute("UPDATE weather_observations SET humidity_pct=101")
    Pipeline(settings, NOW).export()
    assert "invalid Silver row 1" in inspect_status(settings, validate=True)["errors"]


def test_revised_api_values_never_overwrite(settings: Settings, payload: dict[str, Any]) -> None:
    Pipeline(settings, NOW).backfill()
    Pipeline(settings, NOW, fake_client(settings, payload)).run()
    original = read_rows(settings)
    payload["hourly"]["temperature_2m"][0] = 29
    result = Pipeline(settings, NOW, fake_client(settings, payload)).run()
    assert result["silver_rejected"] == 1
    assert read_rows(settings) == original


def test_reference_enriches_only_missing_fields(settings: Settings) -> None:
    with sqlite3.connect(settings.historical_db) as db:
        db.execute("UPDATE weather_observations SET division=NULL,region=NULL")
    with settings.historical_csv.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0].update(division="", region="")
    with settings.historical_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    Pipeline(settings, NOW).backfill()
    assert read_rows(settings)[0][3:5] == ("Pune", "Western Maharashtra")


def test_missing_source_and_contract_mismatch(settings: Settings) -> None:
    settings.city_reference.unlink()
    with pytest.raises(FileNotFoundError):
        Pipeline(settings, NOW).backfill()
    settings.city_reference.write_text("tehsil,district\n")
    settings.historical_csv.write_text("bad_header\n")
    with pytest.raises(ValueError, match="header"):
        Pipeline(settings, NOW).backfill()


def test_scalar_conversion() -> None:
    with pytest.raises(ValueError):
        canonical_source({name: "bad" for name in COLUMNS})
