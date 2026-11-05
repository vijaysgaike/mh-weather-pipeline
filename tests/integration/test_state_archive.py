"""Portable state recovery rejects corrupt/path-traversing/overwriting archives."""

import importlib.util
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from weather_pipeline.config import Settings
from weather_pipeline.pipeline import Pipeline, inspect_status

spec = importlib.util.spec_from_file_location("state_archive", Path("scripts/state_archive.py"))
archive_module: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_module)
NOW = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)


def test_pack_restore(settings: Settings, tmp_path: Path) -> None:
    Pipeline(settings, NOW).backfill()
    archive = tmp_path / "state.zip"
    archive_module.pack(settings, archive)
    target = settings.model_copy(
        update={
            "working_db": tmp_path / "restored/silver/weather.db",
            "export_csv": tmp_path / "restored/silver/weather.csv",
            "bronze_dir": tmp_path / "restored/bronze",
            "gold_dir": tmp_path / "restored/gold",
            "quarantine_dir": tmp_path / "restored/quarantine",
        }
    )
    archive_module.restore(target, archive)
    assert inspect_status(target, validate=True)["silver_rows"] == 1
    with pytest.raises(ValueError, match="empty"):
        archive_module.restore(target, archive)


@pytest.mark.parametrize("key", ["../escape", "/tmp/escape", "unknown/file", "bronze/../escape"])
def test_unsafe_archives(settings: Settings, tmp_path: Path, key: str) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "manifest.json",
            json.dumps({"version": 1, "sha256": {"silver.db": "x", "silver.csv": "x", key: "x"}}),
        )
        output.writestr(key, "x")
        output.writestr("silver.db", "x")
        output.writestr("silver.csv", "x")
    with pytest.raises(ValueError):
        archive_module.restore(settings, archive)
    assert not settings.working_db.exists()


def test_pack_requires_valid_state(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="initialized"):
        archive_module.pack(settings, tmp_path / "state.zip")
    with pytest.raises(ValueError, match="source"):
        archive_module.pack(settings, settings.historical_csv)


def test_checksum_checked_before_writing(settings: Settings, tmp_path: Path) -> None:
    archive = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "manifest.json",
            json.dumps({"version": 1, "sha256": {"silver.db": "bad", "silver.csv": "bad"}}),
        )
        output.writestr("silver.db", "x")
        output.writestr("silver.csv", "x")
    with pytest.raises(ValueError, match="checksum"):
        archive_module.restore(settings, archive)
    assert not settings.working_db.exists()
