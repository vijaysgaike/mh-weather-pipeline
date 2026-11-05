"""Independent expected values for derivation, WMO mapping and physical validation."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from weather_pipeline.models import City, utc_timestamp
from weather_pipeline.quality import QualityError, missing_fields, validate_observation
from weather_pipeline.silver import (
    WMO,
    api_rows,
    enrich,
    heat_index,
    humidity_category,
    parse_payload,
    temp_category,
)

NOW = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)
CITY = City(city="Pune", latitude=18.52, longitude=73.85)


@pytest.mark.parametrize(
    "value",
    ["2026-11-05 00:00:00", "2026-11-05T05:30:00+05:30", "20261105_000000", "2026-11-05T00:00Z"],
)
def test_utc_normalization(value: str) -> None:
    assert utc_timestamp(value) == "2026-11-05T00:00:00+00:00"


@pytest.mark.parametrize(
    "temp,expected",
    [(14.9, "Cold"), (15, "Moderate"), (29.9, "Moderate"), (30, "Hot"), (40, "Extreme")],
)
def test_temperature_categories(temp: float, expected: str) -> None:
    assert temp_category(temp) == expected


@pytest.mark.parametrize(
    "humidity,expected", [(29, "Low"), (30, "Medium"), (70, "Medium"), (71, "High")]
)
def test_humidity_categories(humidity: float, expected: str) -> None:
    assert humidity_category(humidity) == expected


def test_heat_index_noaa_reference() -> None:
    # 90°F at 70% RH is approximately 105.9°F (41.1°C).
    assert heat_index((90 - 32) * 5 / 9, 70) == pytest.approx(41.07, abs=0.1)
    assert heat_index(20, 50) < 21
    assert heat_index((100 - 32) * 5 / 9, 10) < 38
    assert heat_index((85 - 32) * 5 / 9, 90) > 35


def test_derivations(history_row: dict[str, Any]) -> None:
    row = enrich(history_row)
    assert row["temp_fahrenheit"] == 77
    assert row["wind_speed_kmh"] == 7.2
    assert row["daylight_hrs"] == 11
    assert row["date"] == "2026-11-04"
    assert row["hour"] == 0
    assert row["day_of_week"] == "Wednesday"
    assert row["humidity_category"] == "Medium"
    assert (
        enrich({**history_row, "sunrise_utc": None, "wind_speed_mps": None})["daylight_hrs"] is None
    )


def test_parse_rows_and_completed_hours(payload: dict[str, Any]) -> None:
    parsed = parse_payload(json.dumps(payload).encode())
    rows = api_rows(parsed, CITY, NOW)
    assert len(rows) == 2
    row, provenance = rows[1]
    assert row["weather_main"] == "Rain"
    assert row["weather_id"] == 500
    assert row["weather_severity"] == "Rain"
    assert row["temp_celsius"] == 32
    assert row["temp_min_celsius"] == 21
    assert row["rain_1h_mm"] == 0.5
    assert provenance["wmo_code"] == 61
    assert row["latitude"] == CITY.latitude


@pytest.mark.parametrize("code", list(WMO))
def test_all_wmo_codes_have_descriptions(code: int) -> None:
    main, description, legacy, severity = WMO[code]
    assert main and description and legacy > 0 and severity


def test_optional_arrays_and_unknown_code(payload: dict[str, Any]) -> None:
    del payload["hourly"]["visibility"]
    payload["hourly"]["weather_code"][0] = 999
    payload["daily"] = {"time": []}
    rows = api_rows(parse_payload(json.dumps(payload).encode()), CITY, NOW)
    assert rows[0][0]["visibility_m"] is None
    assert rows[0][0]["sunrise_utc"] is None
    assert rows[0][0]["weather_id"] is None
    assert rows[0][1]["unknown_weather_code"]


@pytest.mark.parametrize(
    "body", [b"bad", b"[]", b'{"error":true}', b"{}", b'{"hourly":{"time":[]},"daily":null}']
)
def test_bad_payloads(body: bytes) -> None:
    with pytest.raises(ValueError):
        parse_payload(body)


@pytest.mark.parametrize("mutation", ["offset", "duplicate", "length", "unit", "daily_unit"])
def test_bad_response_contract(payload: dict[str, Any], mutation: str) -> None:
    if mutation == "offset":
        payload["utc_offset_seconds"] = 19800
    elif mutation == "duplicate":
        payload["hourly"]["time"][1] = payload["hourly"]["time"][0]
    elif mutation == "length":
        payload["hourly"]["rain"] = []
    elif mutation == "unit":
        payload["hourly_units"]["wind_speed_10m"] = "km/h"
    else:
        payload["daily_units"]["temperature_2m_min"] = "°F"
    with pytest.raises(ValueError):
        parse_payload(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "patch",
    [
        {"humidity_pct": 101},
        {"humidity_pct": -1},
        {"temp_celsius": 61},
        {"latitude": 91},
        {"longitude": 181},
        {"wind_speed_mps": -1},
        {"rain_1h_mm": -1},
        {"pressure_hpa": 200},
        {"temp_celsius": float("nan")},
        {"temp_min_celsius": 40, "temp_max_celsius": 30},
        {"hour": 3},
        {"date": "2026-01-01"},
        {"day_of_week": "Sunday"},
        {"sunrise_utc": "2026-11-04T14:00:00Z"},
    ],
)
def test_invalid_rows(history_row: dict[str, Any], patch: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        validate_observation({**history_row, **patch}, NOW, historical=True)


def test_freshness_and_missing_fields(history_row: dict[str, Any]) -> None:
    with pytest.raises(QualityError, match="freshness"):
        validate_observation(history_row, NOW + timedelta(days=10))
    with pytest.raises(QualityError, match="future"):
        validate_observation(history_row, NOW - timedelta(days=10), historical=True)
    observation = validate_observation({**history_row, "visibility_m": None}, NOW)
    assert missing_fields(observation) == ["visibility_m"]
    assert validate_observation(history_row, NOW + timedelta(days=10), historical=True)
