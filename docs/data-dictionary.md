# Data dictionary and source semantics

The CSV retains these 37 columns, in the supplied order. SQLite Silver is
`weather_observations`; `(city,timestamp_utc)` is unique. CSV empty strings
represent SQL NULL. All normalized timestamps are ISO 8601 UTC with `+00:00`.

| Columns | Type / units | Meaning |
|---|---|---|
| observation_id | positive integer | Existing accepted numeric ID, or SQLite-generated ID; original source IDs remain in lineage |
| city | text, required | Original historical city identity, including aliases |
| district, division, region | nullable text | Historical administrative metadata; missing division/region filled only by an unambiguous exact name + district reference match |
| country | text | Historical ISO country identifier, normally IN |
| latitude, longitude | real, degrees | Requested historical coordinates, never provider grid-cell coordinates |
| timestamp_utc | text, required | Hourly model valid time for new rows; original history may have subhourly sampling |
| sunrise_utc, sunset_utc | nullable UTC text | Sunrise/sunset for the requested UTC date |
| weather_main, weather_desc | nullable text | Documented WMO-to-legacy descriptions; unknown codes stay null |
| weather_id | nullable integer | Approximate legacy ID from the explicit WMO mapping in silver.py; not the native WMO code |
| temp_celsius | real, required, °C | 2m temperature; validated -40 to 60 |
| feels_like_celsius | nullable real, °C | Open-Meteo apparent temperature |
| temp_min_celsius, temp_max_celsius | nullable real, °C | Forecast daily minimum/maximum for the UTC date; these may include forecast hours later than timestamp_utc |
| humidity_pct | real, required, % | Relative humidity at 2m, 0–100 |
| pressure_hpa | nullable real, hPa | Mean sea-level pressure (not surface pressure), 300–1100 |
| visibility_m | nullable real, m | Visibility; missing values remain null; historical zeroes are retained |
| wind_speed_mps | nullable real, m/s | Wind speed at 10m; explicitly requested with wind_speed_unit=ms |
| wind_deg | nullable real, degrees | Wind direction, 0–360 |
| cloud_pct | nullable real, % | Total cloud cover, 0–100 |
| rain_1h_mm | nullable real, mm | Open-Meteo hourly rain in the preceding hour; excludes separately reported showers/snow |
| ingestion_timestamp | UTC text, required | Time the response was collected; compact historical timestamps normalized |
| temp_fahrenheit | nullable real, °F | Celsius × 9/5 + 32, rounded to 2 places |
| wind_speed_kmh | nullable real, km/h | m/s × 3.6, rounded to 2 places |
| heat_index | nullable real, °C | NOAA simple estimate/Rothfusz regression with humidity adjustments, rounded to 2 places |
| date, hour, day_of_week | ISO date, integer, English text | UTC calendar fields derived from timestamp_utc |
| daylight_hrs | nullable real, hours | Sunset minus sunrise, rounded to 2 places |
| temp_category | nullable text | Cold <15, Moderate [15,30), Hot [30,40), Extreme ≥40°C |
| humidity_category | nullable text | Low <30, Medium [30,70], High >70% |
| weather_severity | nullable text | Clear, Cloudy, Fog, Rain, Snow, or Severe according to WMO mapping; descriptive, not a public warning system |
| record_created_at | UTC text | First insertion time; never rewritten on reruns |

Historical derivations are preserved rather than recomputed. Existing historical
heat index/category semantics may differ from the new documented formulas.
Backfill normalizes timestamps and numeric types and can fill missing division/
region; it is a migration into separate outputs, not a byte-for-byte CSV copy.
Once accepted into Silver, rows are never updated by later observations.

`lineage` links each accepted observation to source, original identifier, raw
response path, native WMO code and provider grid coordinates. Optional missing
values are recorded in `quality_warnings`; invalid required values/ranges go to
`quarantine` with the complete rejected payload and reason. Bronze holds even
malformed JSON and final HTTP error bodies; timeouts have no raw response.

Gold daily city/district/division aggregates are observation-weighted. Historical
sampling frequency varies and aliases remain separate cities. Rain metrics are
mean/max reported hourly rain, not rainfall totals: summing historical subhourly
reports would double-count overlapping intervals. Gold daily extrema are extrema
of accepted observations, distinct from forecast daily min/max on Silver rows.
