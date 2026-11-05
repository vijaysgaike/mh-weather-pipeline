"""Milestone-one CLI for inspecting validated runtime configuration."""

import argparse
from pathlib import Path

from weather_pipeline.config import load_settings
from weather_pipeline.logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    """Validate configuration and report scaffold status without opening data sinks."""
    parser = argparse.ArgumentParser(description="Maharashtra Open-Meteo weather pipeline")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("command", choices=["status"])
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError) as exc:
        configure_logging().error(
            "Invalid configuration (%s); check config paths and values", type(exc).__name__
        )
        return 2
    logger = configure_logging(settings.log_level)
    logger.info("M1 scaffold ready; source=%s; ingestion is not implemented yet", settings.source)
    return 0
