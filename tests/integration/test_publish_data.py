"""Published GitHub data remains deterministic, recoverable, and append-only."""

import csv
import gzip
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from weather_pipeline.config import Settings
from weather_pipeline.pipeline import Pipeline, inspect_status

spec = importlib.util.spec_from_file_location("publish_data", Path("scripts/publish_data.py"))
publication: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)
NOW = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)


def recovery_settings(settings: Settings, root: Path) -> Settings:
    return settings.model_copy(
        update={
            "working_db": root / "silver/weather.db",
            "export_csv": root / "silver/weather.csv",
            "bronze_dir": root / "bronze",
            "gold_dir": root / "gold",
            "quarantine_dir": root / "quarantine",
        }
    )


def test_deterministic_publication_and_recovery(settings: Settings, tmp_path: Path) -> None:
    Pipeline(settings, NOW).backfill()
    output = tmp_path / "published"
    manifest = publication.publish(settings, output)
    assert manifest["row_count"] == 1
    assert manifest["daily_files"][0]["path"] == "daily/2026/11/2026-11-04.csv"
    with gzip.open(output / "weather_dataset.csv.gz", "rt", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["city"] == "Pune"
    before = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    publication.publish(settings, output)
    after = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    assert before == after
    recovered = recovery_settings(settings, tmp_path / "recovered")
    assert publication.restore(recovered, output)["silver_out"] == 1
    assert inspect_status(recovered, validate=True)["status"] == "ready"
    csv_before = recovered.export_csv.read_bytes()
    assert publication.restore(recovered, output)["silver_out"] == 0
    assert recovered.export_csv.read_bytes() == csv_before


def test_branch_progress_merges_into_older_artifact(
    settings: Settings,
    tmp_path: Path,
    history_row: dict[str, Any],
) -> None:
    from weather_pipeline.models import COLUMNS

    Pipeline(settings, NOW).backfill()
    original = tmp_path / "original.csv"
    original.write_bytes(settings.export_csv.read_bytes())
    newer = tmp_path / "newer.csv"
    extra = {**history_row, "observation_id": 2, "city": "Nagpur"}
    with newer.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(extra)
    Pipeline(settings, NOW).merge_published_csv(newer)
    output = tmp_path / "published"
    publication.publish(settings, output)
    recovered = recovery_settings(settings, tmp_path / "recovered")
    Pipeline(recovered, NOW).merge_published_csv(original)
    result = publication.restore(recovered, output)
    assert result["silver_out"] == 1
    assert inspect_status(recovered)["silver_rows"] == 2


@pytest.mark.parametrize("problem", ["checksum", "schema", "path", "rows"])
def test_invalid_publication_rejected_before_import(
    settings: Settings, tmp_path: Path, problem: str
) -> None:
    Pipeline(settings, NOW).backfill()
    output = tmp_path / "published"
    manifest = publication.publish(settings, output)
    if problem == "checksum":
        (output / "weather_dataset.csv.gz").write_bytes(b"broken")
    elif problem == "schema":
        manifest["format_version"] = 5
    elif problem == "path":
        manifest["snapshot"]["path"] = "../secret"
    else:
        manifest["row_count"] = 2
    (output / "manifest.json").write_text(json.dumps(manifest))
    recovered = recovery_settings(settings, tmp_path / "recovered")
    with pytest.raises(ValueError):
        publication.restore(recovered, output)
    assert not recovered.working_db.exists()


def test_conflicts_roll_back_snapshot(
    settings: Settings, tmp_path: Path, history_row: dict[str, Any]
) -> None:
    from weather_pipeline.models import COLUMNS

    Pipeline(settings, NOW).backfill()
    incoming = tmp_path / "conflicts.csv"
    with incoming.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(
            [
                {**history_row, "city": "Nagpur", "observation_id": 2},
                {**history_row, "temp_celsius": 29},
            ]
        )
    original = settings.export_csv.read_bytes()
    with pytest.raises(ValueError, match="conflicts"):
        Pipeline(settings, NOW).merge_published_csv(incoming)
    assert inspect_status(settings)["silver_rows"] == 1
    assert settings.export_csv.read_bytes() == original


def test_publication_refuses_source_directory(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="contain source"):
        publication.publish(settings, settings.working_db.parent)
    (tmp_path / "pyproject.toml").touch()
    with pytest.raises(ValueError, match="dedicated"):
        publication.publish(settings, tmp_path)


def test_uninitialized_cannot_publish(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="synchronized"):
        publication.publish(settings, tmp_path / "published")
