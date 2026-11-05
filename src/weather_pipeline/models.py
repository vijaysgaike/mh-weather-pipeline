"""Typed source locations and the exact legacy export contract."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_timestamp(value: Any) -> str:
    """Normalize ISO or legacy compact timestamps; naive legacy values mean UTC."""
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%Y%m%d_%H%M%S")
    except ValueError:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


class City(BaseModel):
    """A historical city identity; provider grid coordinates never replace it."""

    city: str = Field(min_length=1)
    district: str | None = None
    division: str | None = None
    region: str | None = None
    country: str = "IN"
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Observation(City):
    """Validated 37-column weather record, with optional unavailable measurements."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    observation_id: int | None = Field(default=None, gt=0)
    timestamp_utc: str
    sunrise_utc: str | None = None
    sunset_utc: str | None = None
    weather_main: str | None = None
    weather_desc: str | None = None
    weather_id: int | None = None
    temp_celsius: float = Field(ge=-40, le=60)
    feels_like_celsius: float | None = Field(default=None, ge=-60, le=85)
    temp_min_celsius: float | None = Field(default=None, ge=-40, le=60)
    temp_max_celsius: float | None = Field(default=None, ge=-40, le=60)
    humidity_pct: float = Field(ge=0, le=100)
    pressure_hpa: float | None = Field(default=None, ge=300, le=1100)
    visibility_m: float | None = Field(default=None, ge=0, le=200000)
    wind_speed_mps: float | None = Field(default=None, ge=0, le=120)
    wind_deg: float | None = Field(default=None, ge=0, le=360)
    cloud_pct: float | None = Field(default=None, ge=0, le=100)
    rain_1h_mm: float | None = Field(default=None, ge=0, le=500)
    ingestion_timestamp: str
    temp_fahrenheit: float | None = None
    wind_speed_kmh: float | None = None
    heat_index: float | None = None
    date: str
    hour: int = Field(ge=0, le=23)
    day_of_week: str
    daylight_hrs: float | None = Field(default=None, ge=0, le=24)
    temp_category: str | None = None
    humidity_category: str | None = None
    weather_severity: str | None = None
    record_created_at: str

    @field_validator(
        "timestamp_utc",
        "ingestion_timestamp",
        "record_created_at",
        "sunrise_utc",
        "sunset_utc",
        mode="before",
    )
    @classmethod
    def normalize_time(cls, value: Any) -> str | None:
        """Apply the source UTC contract to all time fields."""
        return None if value is None else utc_timestamp(value)

    @model_validator(mode="after")
    def check_relations(self) -> "Observation":
        """Reject inverted extrema and inconsistent UTC calendar fields."""
        if (
            self.temp_min_celsius is not None
            and self.temp_max_celsius is not None
            and self.temp_min_celsius > self.temp_max_celsius
        ):
            raise ValueError("temperature minimum exceeds maximum")
        dt = datetime.fromisoformat(self.timestamp_utc)
        if (self.date, self.hour, self.day_of_week) != (
            dt.date().isoformat(),
            dt.hour,
            dt.strftime("%A"),
        ):
            raise ValueError("calendar fields disagree with timestamp_utc")
        if self.sunrise_utc and self.sunset_utc and self.sunrise_utc > self.sunset_utc:
            raise ValueError("sunrise follows sunset")
        return self


# Keep order independent of inheritance and SQL implementation details.
COLUMNS = (
    "observation_id city district division region country latitude longitude timestamp_utc "
    "sunrise_utc sunset_utc weather_main weather_desc weather_id temp_celsius feels_like_celsius "
    "temp_min_celsius temp_max_celsius humidity_pct pressure_hpa visibility_m wind_speed_mps "
    "wind_deg cloud_pct rain_1h_mm ingestion_timestamp temp_fahrenheit wind_speed_kmh heat_index "
    "date hour day_of_week daylight_hrs temp_category humidity_category weather_severity "
    "record_created_at"
).split()
INTEGER_COLUMNS = {"observation_id", "weather_id", "hour"}
REAL_COLUMNS = {
    "latitude",
    "longitude",
    "temp_celsius",
    "feels_like_celsius",
    "temp_min_celsius",
    "temp_max_celsius",
    "humidity_pct",
    "pressure_hpa",
    "visibility_m",
    "wind_speed_mps",
    "wind_deg",
    "cloud_pct",
    "rain_1h_mm",
    "temp_fahrenheit",
    "wind_speed_kmh",
    "heat_index",
    "daylight_hrs",
}
