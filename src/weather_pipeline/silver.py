"""Pure weather transformations and Open-Meteo to legacy-schema adaptation."""

import json
import math
from datetime import datetime
from typing import Any

from weather_pipeline.models import City, utc_timestamp

# Approximate legacy weather IDs. Native WMO codes remain in lineage and Bronze.
WMO: dict[int, tuple[str, str, int, str]] = {
    0: ("Clear", "clear sky", 800, "Clear"),
    1: ("Clouds", "mainly clear", 801, "Cloudy"),
    2: ("Clouds", "partly cloudy", 802, "Cloudy"),
    3: ("Clouds", "overcast", 804, "Cloudy"),
    45: ("Fog", "fog", 741, "Fog"),
    48: ("Fog", "depositing rime fog", 741, "Fog"),
    51: ("Drizzle", "light drizzle", 300, "Rain"),
    53: ("Drizzle", "moderate drizzle", 301, "Rain"),
    55: ("Drizzle", "dense drizzle", 302, "Rain"),
    56: ("Drizzle", "light freezing drizzle", 511, "Severe"),
    57: ("Drizzle", "dense freezing drizzle", 511, "Severe"),
    61: ("Rain", "slight rain", 500, "Rain"),
    63: ("Rain", "moderate rain", 501, "Rain"),
    65: ("Rain", "heavy rain", 502, "Severe"),
    66: ("Rain", "light freezing rain", 511, "Severe"),
    67: ("Rain", "heavy freezing rain", 511, "Severe"),
    71: ("Snow", "slight snow", 600, "Snow"),
    73: ("Snow", "moderate snow", 601, "Snow"),
    75: ("Snow", "heavy snow", 602, "Severe"),
    77: ("Snow", "snow grains", 600, "Snow"),
    80: ("Rain", "slight rain showers", 520, "Rain"),
    81: ("Rain", "moderate rain showers", 521, "Rain"),
    82: ("Rain", "violent rain showers", 522, "Severe"),
    85: ("Snow", "slight snow showers", 620, "Snow"),
    86: ("Snow", "heavy snow showers", 622, "Severe"),
    95: ("Thunderstorm", "thunderstorm", 211, "Severe"),
    96: ("Thunderstorm", "thunderstorm with slight hail", 212, "Severe"),
    99: ("Thunderstorm", "thunderstorm with heavy hail", 212, "Severe"),
}
HOURLY_MAP = {
    "temperature_2m": "temp_celsius",
    "relative_humidity_2m": "humidity_pct",
    "apparent_temperature": "feels_like_celsius",
    "pressure_msl": "pressure_hpa",
    "visibility": "visibility_m",
    "wind_speed_10m": "wind_speed_mps",
    "wind_direction_10m": "wind_deg",
    "cloud_cover": "cloud_pct",
    "rain": "rain_1h_mm",
}


def heat_index(temp: float, humidity: float) -> float:
    """NOAA heat index in Celsius, including the low/high humidity adjustments."""
    f = temp * 9 / 5 + 32
    simple = 0.5 * (f + 61 + (f - 68) * 1.2 + humidity * 0.094)
    result = (simple + f) / 2
    if result >= 80:
        result = (
            -42.379
            + 2.04901523 * f
            + 10.14333127 * humidity
            - 0.22475541 * f * humidity
            - 0.00683783 * f * f
            - 0.05481717 * humidity * humidity
            + 0.00122874 * f * f * humidity
            + 0.00085282 * f * humidity * humidity
            - 0.00000199 * f * f * humidity * humidity
        )
        if humidity < 13 and 80 <= f <= 112:
            result -= ((13 - humidity) / 4) * math.sqrt((17 - abs(f - 95)) / 17)
        elif humidity > 85 and 80 <= f <= 87:
            result += ((humidity - 85) / 10) * ((87 - f) / 5)
    return round((result - 32) * 5 / 9, 2)


def temp_category(temp: float) -> str:
    """Bucket Celsius temperatures using documented portfolio thresholds."""
    return "Cold" if temp < 15 else "Moderate" if temp < 30 else "Hot" if temp < 40 else "Extreme"


def humidity_category(humidity: float) -> str:
    """Bucket relative humidity: below 30 dry, 30–70 medium, above 70 high."""
    return "Low" if humidity < 30 else "Medium" if humidity <= 70 else "High"


def enrich(row: dict[str, Any]) -> dict[str, Any]:
    """Derive calendar, unit conversions, heat index, daylight and categories."""
    result = dict(row)
    dt = datetime.fromisoformat(utc_timestamp(row["timestamp_utc"]))
    result.update(date=dt.date().isoformat(), hour=dt.hour, day_of_week=dt.strftime("%A"))
    temp, humidity = float(row["temp_celsius"]), float(row["humidity_pct"])
    result.update(
        temp_fahrenheit=round(temp * 9 / 5 + 32, 2),
        heat_index=heat_index(temp, humidity),
        temp_category=temp_category(temp),
        humidity_category=humidity_category(humidity),
    )
    speed = row.get("wind_speed_mps")
    result["wind_speed_kmh"] = None if speed is None else round(float(speed) * 3.6, 2)
    sunrise, sunset = row.get("sunrise_utc"), row.get("sunset_utc")
    result["daylight_hrs"] = (
        None
        if sunrise is None or sunset is None
        else round(
            (
                datetime.fromisoformat(utc_timestamp(sunset))
                - datetime.fromisoformat(utc_timestamp(sunrise))
            ).total_seconds()
            / 3600,
            2,
        )
    )
    return result


def parse_payload(body: bytes) -> dict[str, Any]:
    """Validate envelope shape, aligned arrays and units before generating rows."""
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("API returned an error or non-object envelope")
    if payload.get("utc_offset_seconds", 0) != 0:
        raise ValueError("API response is not UTC")
    expected = {
        "temperature_2m": "°C",
        "relative_humidity_2m": "%",
        "apparent_temperature": "°C",
        "pressure_msl": "hPa",
        "visibility": "m",
        "wind_speed_10m": "m/s",
        "wind_direction_10m": "°",
        "cloud_cover": "%",
        "rain": "mm",
        "weather_code": "wmo code",
    }
    for section, fields in (
        ("hourly", list(HOURLY_MAP) + ["weather_code"]),
        ("daily", ["sunrise", "sunset", "temperature_2m_min", "temperature_2m_max"]),
    ):
        data = payload.get(section)
        if not isinstance(data, dict) or not isinstance(data.get("time"), list):
            raise ValueError(f"missing {section} time array")
        if len(set(data["time"])) != len(data["time"]):
            raise ValueError(f"duplicate {section} timestamps")
        for field in fields:
            if field in data and (
                not isinstance(data[field], list) or len(data[field]) != len(data["time"])
            ):
                raise ValueError(f"misaligned {section}.{field}")
    units = payload.get("hourly_units", {})
    for name, unit in expected.items():
        if name in payload["hourly"] and units.get(name) != unit:
            raise ValueError(f"unexpected or missing unit for {name}")
    daily_units = payload.get("daily_units", {})
    for name in ("temperature_2m_min", "temperature_2m_max"):
        if name in payload["daily"] and daily_units.get(name) != "°C":
            raise ValueError(f"unexpected or missing unit for {name}")
    return payload


def api_rows(
    payload: dict[str, Any],
    city: City,
    now: datetime,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Adapt completed hours only; defer numeric/range errors to row quarantine."""
    hourly, daily = payload["hourly"], payload["daily"]
    days = {day: i for i, day in enumerate(daily["time"])}
    rows = []
    cutoff = now.replace(minute=0, second=0, microsecond=0)
    for i, value in enumerate(hourly["time"]):
        timestamp = utc_timestamp(value)
        if datetime.fromisoformat(timestamp) >= cutoff:
            continue
        row: dict[str, Any] = {
            **city.model_dump(),
            "timestamp_utc": timestamp,
            "ingestion_timestamp": now.isoformat(),
            "record_created_at": now.isoformat(),
        }
        for source, target in HOURLY_MAP.items():
            row[target] = hourly.get(source, [None] * len(hourly["time"]))[i]
        day_index = days.get(timestamp[:10])
        for source, target in (
            ("sunrise", "sunrise_utc"),
            ("sunset", "sunset_utc"),
            ("temperature_2m_min", "temp_min_celsius"),
            ("temperature_2m_max", "temp_max_celsius"),
        ):
            row[target] = (
                None
                if day_index is None
                else daily.get(
                    source,
                    [None] * len(daily["time"]),
                )[day_index]
            )
        code = hourly.get("weather_code", [None] * len(hourly["time"]))[i]
        mapping = WMO.get(code) if isinstance(code, int) else None
        main, description, legacy_id, severity = mapping or (None, None, None, None)
        row.update(
            weather_main=main,
            weather_desc=description,
            weather_id=legacy_id,
            weather_severity=severity,
        )
        rows.append(
            (
                row,
                {
                    "wmo_code": code,
                    "unknown_weather_code": mapping is None,
                    "grid_latitude": payload.get("latitude"),
                    "grid_longitude": payload.get("longitude"),
                },
            )
        )
    return rows
