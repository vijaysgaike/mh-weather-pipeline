# Maharashtra weather pipeline

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Coverage target](https://img.shields.io/badge/coverage_target-85%25-green)

A lightweight SQLite pipeline using Open-Meteo without an API key. **M1 is
implemented:** configuration, structured logging, CLI status, tooling and CI.
Ingestion, backfill and analytics arrive in M2–M6. No supplied data is modified.

```bash
make install
make check
.venv/bin/weather-pipeline --config config.yaml status
```

Copy `.env.example` to `.env` only if no `.env` already exists. Existing `.env`
is retained; old provider credentials are unused. Configuration is JSON-compatible
YAML to avoid adding a YAML parser. Relative paths resolve against config.yaml.
Precedence: environment > adjacent .env > config > defaults. Supported overrides:
`WEATHER_BATCH_SIZE`, `WEATHER_TIMEOUT_SECONDS`, `WEATHER_REQUEST_INTERVAL_SECONDS`,
`WEATHER_MAX_RETRIES`, `WEATHER_LOG_LEVEL`. An empty city list means the cities in
historical data, to be resolved during backfill. Status validates config only;
it does not verify source availability or perform network requests.

Install Git hooks with `.venv/bin/pre-commit install` once Git is initialized.
CI runs ruff, mypy and pytest with >=85% coverage on Python 3.11 and 3.12.
After creating your repository, add its CI badge using
`https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg`.
The coverage badge above is a target, not a hosted measurement; CI uploads the
measured coverage report. Inputs and runtime data are ignored by Git. Keep only
small synthetic fixtures in version control; large datasets belong in external
storage (or Git LFS if explicitly needed).

See [design](docs/design-proposal.md), [inspection](docs/data-inspection.json),
and [architecture](docs/architecture.md). Open-Meteo supplies model-derived weather;
see its [documentation](https://open-meteo.com/en/docs) for field semantics.
