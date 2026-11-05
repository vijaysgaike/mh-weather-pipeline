"""Single CLI for ingestion, historical import, synchronization and quality checks."""

import argparse
import json
import sqlite3
from pathlib import Path

from weather_pipeline.config import load_settings
from weather_pipeline.logging_setup import configure_logging
from weather_pipeline.pipeline import Pipeline, inspect_status


def main(argv: list[str] | None = None) -> int:
    """Run one command; return 0 on success, 1 for partial/invalid, 2 on failure."""
    parser = argparse.ArgumentParser(description="Maharashtra Open-Meteo weather pipeline")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("command", choices=["run", "backfill", "export-csv", "validate", "status"])
    parser.add_argument(
        "--city", action="append", default=[], help="Historical city name; repeatable"
    )
    parser.add_argument("--limit-cities", type=int, help="Process first N selected cities")
    args = parser.parse_args(argv)
    if args.limit_cities is not None and args.limit_cities < 1:
        parser.error("--limit-cities must be positive")
    if args.command != "run" and (args.city or args.limit_cities is not None):
        parser.error("city filters are supported only for run")
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError) as exc:
        configure_logging().error(
            "Invalid configuration (%s); check config paths and values",
            type(exc).__name__,
        )
        return 2
    logger = configure_logging(settings.log_level)
    try:
        if args.command in {"status", "validate"}:
            result = inspect_status(settings, validate=args.command == "validate")
            logger.info("%s", json.dumps(result, sort_keys=True))
            return 1 if result["status"] in {"invalid", "not_initialized"} else 0
        pipeline = Pipeline(settings)
        if args.command == "backfill":
            result = pipeline.backfill()
        elif args.command == "export-csv":
            result = pipeline.export()
        else:
            result = pipeline.run(args.city, args.limit_cities)
        return 1 if result["status"] == "partial" else 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        logger.error("Command failed (%s); see run logs and status", type(exc).__name__)
        return 2
