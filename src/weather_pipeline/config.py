"""Validated configuration with explicit, secret-free operational overrides."""

import json
import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    """Paths and limits; original inputs cannot be used as output files."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["open-meteo"] = "open-meteo"
    api_url: Literal["https://api.open-meteo.com/v1/forecast"] = (
        "https://api.open-meteo.com/v1/forecast"
    )
    historical_csv: Path = Path("data/weather_dataset.csv")
    historical_db: Path = Path("data/weather.db")
    city_reference: Path = Path("data/maharashtra_tehsils_final.csv")
    working_db: Path = Path("data/silver/weather.db")
    export_csv: Path = Path("data/silver/weather_dataset.csv")
    bronze_dir: Path = Path("data/bronze")
    quarantine_dir: Path = Path("data/quarantine")
    gold_dir: Path = Path("data/gold")
    cities: list[str] = Field(default_factory=list)
    batch_size: int = Field(default=20, ge=1, le=100)
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    request_interval_seconds: float = Field(default=1, ge=1)
    max_retries: int = Field(default=3, ge=0, le=10)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @model_validator(mode="after")
    def protect_inputs(self) -> "Settings":
        """Reject aliased inputs, outputs, and output directories."""
        inputs = {
            self.historical_csv.resolve(),
            self.historical_db.resolve(),
            self.city_reference.resolve(),
        }
        outputs = [self.working_db.resolve(), self.export_csv.resolve()]
        directories = [
            self.bronze_dir.resolve(),
            self.quarantine_dir.resolve(),
            self.gold_dir.resolve(),
        ]
        if len(set(outputs)) != len(outputs) or inputs.intersection(outputs):
            raise ValueError("Working outputs must be distinct from each other and source inputs")
        if len(set(directories)) != len(directories):
            raise ValueError("Layer directories must be distinct")
        if any(d == p or p in d.parents for d in directories for p in inputs | set(outputs)):
            raise ValueError("Layer directories cannot alias or descend from file paths")
        return self


def load_settings(config_path: Path = Path("config.yaml")) -> Settings:
    """Load JSON-compatible YAML; environment overrides .env, which overrides config.

    Resolve paths against the configuration directory without mutating os.environ.
    Only WEATHER_ operational keys are accepted from the environment.
    """
    config_path = config_path.resolve()
    values = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise ValueError("Configuration must be a JSON object (JSON-compatible YAML)")
    environment = {**dotenv_values(config_path.parent / ".env"), **os.environ}
    for name in (
        "batch_size",
        "timeout_seconds",
        "request_interval_seconds",
        "max_retries",
        "log_level",
    ):
        override = environment.get("WEATHER_" + name.upper())
        if override is not None:
            values[name] = override
    for name in (
        "historical_csv",
        "historical_db",
        "city_reference",
        "working_db",
        "export_csv",
        "bronze_dir",
        "quarantine_dir",
        "gold_dir",
    ):
        value = values.get(name, Settings.model_fields[name].default)
        if not isinstance(value, (str, Path)):
            raise ValueError(f"{name} must be a filesystem path")
        values[name] = config_path.parent / value
    return Settings.model_validate(values)
