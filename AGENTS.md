# Weather pipeline conventions

- Inspection/design were approved. The user subsequently authorized completing M2–M6 in one pass without milestone pauses. Run checks and report results throughout; preserve all remaining conventions.
- Use Open-Meteo, without an API key, instead of OpenWeatherMap.
- Never overwrite or delete the supplied data. Use backups and separate working outputs before any migration.
- Preserve the supplied 37-column weather CSV contract. SQLite is the existing database engine.
- Implement Bronze raw append-only responses, Silver typed and deduplicated records, and Gold analytics.
- Use normalized city + UTC timestamp for idempotency; preserve original IDs and source provenance.
- Quarantine invalid or conflicting rows with reasons; never silently drop them.
- Use Python 3.11+, lightweight dependencies from the requirements, type hints and docstrings for public functions, small pure functions, structured logging, and no print() in production code.
- Keep secrets and supplied raw datasets out of Git. The user explicitly authorized a generated final-data copy on a separate weather-data branch, updated by Actions. Publish only the validated compressed full CSV, daily CSV partitions, and their manifest/docs there. Do not expose .env contents.
- Mock all network calls in tests and use temporary databases and CSVs. Target >=85% src coverage; use ruff, mypy, pytest, pre-commit, and CI.
- Use transactional database writes and atomic CSV replacement, with documented recovery for failures between sinks.
- Ask before destructive operations or dependencies beyond the requested stack.
- Do not push or publish without authorization. Use Conventional Commits when Git is available.
