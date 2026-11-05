"""Configuration precedence, validation, and input protection."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from weather_pipeline.config import Settings, load_settings


def write_config(tmp_path: Path, values: object) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(values))
    return path


def test_defaults_resolve_relative_to_config(tmp_path: Path) -> None:
    settings = load_settings(write_config(tmp_path, {}))
    assert settings.source == "open-meteo"
    assert settings.historical_csv == tmp_path / "data/weather_dataset.csv"
    assert settings.working_db == tmp_path / "data/silver/weather.db"
    assert settings.cities == []


def test_environment_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_config(tmp_path, {"batch_size": 5, "log_level": "WARNING"})
    (tmp_path / ".env").write_text("WEATHER_BATCH_SIZE=10\nWEATHER_LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("WEATHER_BATCH_SIZE", "30")
    settings = load_settings(path)
    assert settings.batch_size == 30
    assert settings.log_level == "DEBUG"
    assert "WEATHER_LOG_LEVEL" not in __import__("os").environ


@pytest.mark.parametrize(
    "values",
    [
        {"batch_size": 0},
        {"batch_size": 101},
        {"timeout_seconds": 0},
        {"request_interval_seconds": 0},
        {"max_retries": 11},
        {"log_level": "BAD"},
        {"source": "openweathermap"},
        {"api_key": "unused"},
        {"api_url": "http://untrusted.example"},
        {"working_db": "data/weather.db"},
        {"export_csv": "data/weather_dataset.csv"},
        {"export_csv": "data/silver/weather.db"},
        {"gold_dir": "data/bronze"},
        {"bronze_dir": "data/weather.db/nested"},
    ],
)
def test_invalid_config(tmp_path: Path, values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        load_settings(write_config(tmp_path, values))


def test_symlink_cannot_alias_input(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    source.touch()
    alias = tmp_path / "alias.db"
    alias.symlink_to(source)
    with pytest.raises(ValidationError, match="distinct"):
        Settings(historical_db=source, working_db=alias)


@pytest.mark.parametrize("value", [[], "text", 1])
def test_non_object_config(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError, match="JSON object"):
        load_settings(write_config(tmp_path, value))


def test_malformed_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("invalid: yaml")
    with pytest.raises(ValueError):
        load_settings(path)


@pytest.mark.parametrize("value", [None, 42, []])
def test_path_must_be_string(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError, match="filesystem path"):
        load_settings(write_config(tmp_path, {"working_db": value}))


def test_hardlink_cannot_alias_input(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    source.touch()
    alias = tmp_path / "alias.db"
    alias.hardlink_to(source)
    with pytest.raises(ValidationError, match="hard links"):
        Settings(historical_db=source, working_db=alias)


@pytest.mark.parametrize(
    "patch",
    [
        {"bronze_dir": "data"},
        {"gold_dir": "data/bronze/nested"},
        {"historical_db": "data/weather_dataset.csv"},
    ],
)
def test_layer_and_input_paths_cannot_overlap(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(patch)
