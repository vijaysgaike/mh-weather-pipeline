"""Structured logging contract."""

import io
import json
import logging
from datetime import datetime

from weather_pipeline.logging_setup import configure_logging


def test_log_fields_and_filtering() -> None:
    stream = io.StringIO()
    logger = configure_logging("INFO", "run-123", stream)
    logger.debug("hidden")
    logger.info("city=%s", "Pune")
    event = json.loads(stream.getvalue())
    assert event["run_id"] == "run-123"
    assert event["level"] == "INFO"
    assert event["message"] == "city=Pune"
    assert datetime.fromisoformat(event["timestamp"]).utcoffset().total_seconds() == 0


def test_exception_and_reconfiguration() -> None:
    first, second = io.StringIO(), io.StringIO()
    logger = configure_logging(stream=first)
    try:
        raise ValueError("bad response")
    except ValueError:
        logger.exception("failed")
    event = json.loads(first.getvalue())
    assert "ValueError: bad response" in event["exception"]
    assert event["run_id"]
    before = first.getvalue()
    root_handlers = logging.getLogger().handlers[:]
    logger = configure_logging(stream=second)
    logger.info("next")
    assert len(logger.handlers) == 1
    assert first.getvalue() == before
    assert json.loads(second.getvalue())["message"] == "next"
    assert logging.getLogger().handlers == root_handlers
