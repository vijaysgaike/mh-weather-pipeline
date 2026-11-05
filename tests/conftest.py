"""Synthetic histories and responses; every network connection is forbidden."""

import csv
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from weather_pipeline.config import Settings
from weather_pipeline.models import COLUMNS
from weather_pipeline.storage import connect

NOW = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def disable_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast if a test attempts network I/O."""

    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are forbidden in tests")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def history_row() -> dict[str, Any]:
    """Return a hand-authored compatible row, independent of transformation code."""
    return dict(
        zip(
            COLUMNS,
            [
                1,
                "Pune",
                "Pune",
                "Pune",
                "Western Maharashtra",
                "IN",
                18.52,
                73.85,
                "2026-11-04T00:00:00+00:00",
                "2026-11-04T01:00:00+00:00",
                "2026-11-04T12:00:00+00:00",
                "Clear",
                "clear sky",
                800,
                25.0,
                26.0,
                21.0,
                34.0,
                65.0,
                1013.0,
                10000.0,
                2.0,
                180.0,
                0.0,
                0.0,
                "2026-11-04T00:05:00+00:00",
                77.0,
                7.2,
                25.5,
                "2026-11-04",
                0,
                "Wednesday",
                11.0,
                "Moderate",
                "Medium",
                "Clear",
                "2026-11-04T00:05:00+00:00",
            ],
            strict=True,
        )
    )


@pytest.fixture
def payload() -> dict[str, Any]:
    """Load a tiny synthetic Open-Meteo response; no actual weather observations."""
    return json.loads((Path(__file__).parent / "fixtures/open_meteo.json").read_text())


@pytest.fixture
def settings(tmp_path: Path, history_row: dict[str, Any]) -> Settings:
    """Create isolated original sources; tests never use project data paths."""
    config = Settings(
        historical_csv=tmp_path / "inputs/weather.csv",
        historical_db=tmp_path / "inputs/weather.db",
        city_reference=tmp_path / "inputs/cities.csv",
        working_db=tmp_path / "silver/weather.db",
        export_csv=tmp_path / "silver/weather.csv",
        bronze_dir=tmp_path / "bronze",
        gold_dir=tmp_path / "gold",
        quarantine_dir=tmp_path / "quarantine",
        backup_dir=tmp_path / "backups",
    )
    config.historical_csv.parent.mkdir()
    connection = connect(config.historical_db)
    with connection:
        connection.execute(
            f"INSERT INTO weather_observations ({','.join(COLUMNS)}) VALUES "
            f"({','.join('?' for _ in COLUMNS)})",
            tuple(history_row[name] for name in COLUMNS),
        )
    connection.close()
    with config.historical_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(history_row)
    config.city_reference.write_text(
        "tehsil,district,division,region,lat,lon\nPune,Pune,Pune,Western Maharashtra,18.52,73.85\n",
    )
    return config
