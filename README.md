# Maharashtra weather data

Automatically updated every four hours by the pipeline on the main branch.

- Full accumulated dataset: [weather_dataset.csv.gz](weather_dataset.csv.gz) (download and decompress to get the 37-column CSV).
- Browse smaller, uncompressed files under [daily/](daily/), grouped by year/month/day.
- Row count, latest timestamp, schema and SHA-256 checksums: [manifest.json](manifest.json).

Existing accepted observations are preserved; each update adds unique city/time records.
No-change runs do not create data commits. Database audit/lineage and raw responses are retained separately in workflow artifacts.

Weather source: [Open-Meteo](https://open-meteo.com/), plus the historical dataset described in the main branch. The code license does not license third-party data.
