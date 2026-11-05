"""JSON logging with a run identifier on every event."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO
from uuid import uuid4


class JsonFormatter(logging.Formatter):
    """Format standard log records without serializing arbitrary record attributes."""

    def __init__(self, run_id: str) -> None:
        """Bind the pipeline run identifier."""
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log event with UTC time and optional exception details."""
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "run_id": self.run_id,
            "message": record.getMessage(),
        }
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event)


def configure_logging(
    level: str = "INFO",
    run_id: str | None = None,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure only the package logger; repeated calls replace its handler."""
    logger = logging.getLogger("weather_pipeline")
    logger.setLevel(level)
    logger.propagate = False
    for existing in logger.handlers[:]:
        logger.removeHandler(existing)
        existing.close()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter(run_id or str(uuid4())))
    logger.addHandler(handler)
    return logger
