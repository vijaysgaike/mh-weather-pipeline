"""Physical validation, missing-field warnings and ingestion freshness checks."""

from datetime import UTC, datetime, timedelta
from typing import Any

from weather_pipeline.models import Observation


class QualityError(ValueError):
    """An invalid row with an actionable quarantine reason."""


def validate_observation(
    row: dict[str, Any],
    now: datetime,
    *,
    historical: bool = False,
    max_age_days: int = 8,
) -> Observation:
    """Validate ranges and UTC time; historical records bypass only age checks."""
    observation = Observation.model_validate(row)
    timestamp = datetime.fromisoformat(observation.timestamp_utc)
    if timestamp > now.astimezone(UTC) + timedelta(minutes=5):
        raise QualityError("future timestamp")
    if not historical and timestamp < now - timedelta(days=max_age_days):
        raise QualityError("observation exceeds ingestion freshness window")
    return observation


def missing_fields(observation: Observation) -> list[str]:
    """Record unavailable optional values without fabricating measurements."""
    return [
        key
        for key, value in observation.model_dump().items()
        if value is None and key != "observation_id"
    ]
