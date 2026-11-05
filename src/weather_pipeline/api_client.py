"""Bounded Open-Meteo HTTP requests with throttling, timeout and backoff."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from weather_pipeline.config import Settings
from weather_pipeline.models import City

HOURLY = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,pressure_msl,visibility,"
    "wind_speed_10m,wind_direction_10m,cloud_cover,rain,weather_code"
)
DAILY = "sunrise,sunset,temperature_2m_min,temperature_2m_max"


@dataclass(frozen=True)
class Fetched:
    """Raw response and exact request parameters, before JSON interpretation."""

    body: bytes
    params: dict[str, Any]
    status: int


class ApiError(RuntimeError):
    """Request failure with an optional raw final HTTP response."""

    def __init__(self, message: str, response: Fetched | None = None) -> None:
        """Attach the final body so even unsuccessful requests can land in Bronze."""
        super().__init__(message)
        self.response = response


def retry_delay(header: str | None, attempt: int) -> float:
    """Honor Retry-After seconds/date, or use bounded exponential backoff."""
    try:
        delay = float(header) if header else float(min(60, 2**attempt))
    except ValueError:
        try:
            delay = (parsedate_to_datetime(str(header)) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError):
            delay = float(min(60, 2**attempt))
    return max(1.0, delay)


class OpenMeteoClient:
    """One reusable connection pool; throttle every attempt including retries."""

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        """Accept a mockable session without changing production request behavior."""
        self.settings = settings
        self.session = session or requests.Session()
        self._last_request: float | None = None
        self._blocked_until = 0.0

    def close(self) -> None:
        """Release pooled HTTP connections."""
        self.session.close()

    def fetch(self, city: City, start: str, end: str) -> Fetched:
        """Fetch a bounded UTC hourly interval, retaining raw JSON/error bytes."""
        if time.monotonic() < self._blocked_until:
            raise ApiError("provider cooldown active; retry next run")
        params: dict[str, Any] = {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "timezone": "GMT",
            "temperature_unit": "celsius",
            "wind_speed_unit": "ms",
            "precipitation_unit": "mm",
            "hourly": HOURLY,
            "daily": DAILY,
            "start_date": start,
            "end_date": end,
        }
        for attempt in range(self.settings.max_retries + 1):
            if self._last_request is not None:
                elapsed = time.monotonic() - self._last_request
                time.sleep(max(0, self.settings.request_interval_seconds - elapsed))
            self._last_request = time.monotonic()
            try:
                response = self.session.get(
                    self.settings.api_url,
                    params=params,
                    timeout=self.settings.timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt == self.settings.max_retries:
                    raise ApiError(f"network failure: {type(exc).__name__}") from exc
                time.sleep(retry_delay(None, attempt))
                continue
            fetched = Fetched(response.content, params, response.status_code)
            if 200 <= response.status_code < 300:
                return fetched
            retryable = response.status_code in {429, 500, 502, 503, 504}
            delay = retry_delay(response.headers.get("Retry-After"), attempt)
            if retryable and delay > 60:
                self._blocked_until = time.monotonic() + delay
                raise ApiError("provider requested long cooldown; retry next run", fetched)
            if not retryable or attempt == self.settings.max_retries:
                raise ApiError(f"HTTP {response.status_code}", fetched)
            time.sleep(delay)
        raise AssertionError("unreachable retry state")
