"""Raw responses, retry policies and immutable storage."""

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from weather_pipeline.api_client import ApiError, Fetched, OpenMeteoClient, retry_delay
from weather_pipeline.bronze import land_raw
from weather_pipeline.config import Settings
from weather_pipeline.models import City

CITY = City(city="Pune", latitude=18.52, longitude=73.85)


def response(status: int = 200, body: bytes = b"{}", **headers: str) -> Mock:
    return Mock(status_code=status, content=body, headers=headers)


def test_fetch_params_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Mock()
    session.get.return_value = response()
    client = OpenMeteoClient(Settings(), session)
    sleeper = Mock()
    monkeypatch.setattr("weather_pipeline.api_client.time.sleep", sleeper)
    first = client.fetch(CITY, "2026-11-04", "2026-11-05")
    assert first.body == b"{}"
    assert first.params["wind_speed_unit"] == "ms"
    assert first.params["timezone"] == "GMT"
    assert "apikey" not in first.params
    assert session.get.call_args.kwargs["timeout"] == 30
    client.fetch(CITY, "2026-11-04", "2026-11-05")
    assert sleeper.called
    client.close()
    session.close.assert_called_once()


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError])
def test_network_retries(error: type[Exception], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("weather_pipeline.api_client.time.sleep", Mock())
    session = Mock()
    session.get.side_effect = error()
    with pytest.raises(ApiError, match="network failure"):
        OpenMeteoClient(Settings(max_retries=2), session).fetch(CITY, "x", "y")
    assert session.get.call_count == 3


@pytest.mark.parametrize("status,attempts", [(400, 1), (429, 3), (503, 3)])
def test_http_failures(status: int, attempts: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("weather_pipeline.api_client.time.sleep", Mock())
    session = Mock()
    session.get.return_value = response(status, b'{"error":true}', **{"Retry-After": "2"})
    with pytest.raises(ApiError) as failure:
        OpenMeteoClient(Settings(max_retries=2), session).fetch(CITY, "x", "y")
    assert failure.value.response.body == b'{"error":true}'
    assert session.get.call_count == attempts


def test_retry_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("weather_pipeline.api_client.time.sleep", Mock())
    session = Mock()
    session.get.side_effect = [response(500), response()]
    assert OpenMeteoClient(Settings(), session).fetch(CITY, "x", "y").status == 200


def test_retry_delays() -> None:
    assert retry_delay(None, 2) == 4
    assert retry_delay("500", 0) == 500
    assert retry_delay("-2", 0) == 1
    assert retry_delay("invalid", 2) == 4
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=30))
    assert 20 <= retry_delay(future, 0) <= 30


def test_bronze_preserves_bytes(tmp_path: Path) -> None:
    fetched = Fetched(b' { "a": 1 }\n', {"latitude": 18.52}, 200)
    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    a = land_raw(tmp_path, fetched, "Pune", "run1", now)
    b = land_raw(tmp_path, fetched, "Pune", "run1", now)
    assert a["raw_path"] != b["raw_path"]
    raw = Path(a["raw_path"])
    assert raw.read_bytes() == fetched.body
    assert "date=2026-11-05/hour=12" in str(raw)
    sidecar = json.loads(raw.with_suffix(".metadata.json").read_text())
    assert sidecar["source"] == "open-meteo"
    assert sidecar["request_params"] == fetched.params
    assert len(sidecar["sha256"]) == 64


def test_long_provider_cooldown_does_not_retry_early() -> None:
    session = Mock()
    session.get.return_value = response(429, **{"Retry-After": "3600"})
    client = OpenMeteoClient(Settings(), session)
    with pytest.raises(ApiError, match="long cooldown"):
        client.fetch(CITY, "x", "y")
    with pytest.raises(ApiError, match="cooldown active"):
        client.fetch(CITY, "x", "y")
    assert session.get.call_count == 1
