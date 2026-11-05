"""Immutable raw HTTP landing, partitioned by UTC ingestion date/hour."""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from weather_pipeline.api_client import Fetched


def land_raw(root: Path, fetched: Fetched, city: str, run_id: str, now: datetime) -> dict[str, Any]:
    """Exclusively create raw bytes and a sidecar; never rewrite an existing object."""
    folder = root / f"date={now:%Y-%m-%d}" / f"hour={now:%H}"
    folder.mkdir(parents=True, exist_ok=True)
    stem = uuid4().hex
    raw = folder / f"{stem}.json"
    metadata = {
        "source": "open-meteo",
        "city": city,
        "run_id": run_id,
        "ingestion_timestamp": now.isoformat(),
        "request_params": fetched.params,
        "http_status": fetched.status,
        "sha256": hashlib.sha256(fetched.body).hexdigest(),
        "raw_path": str(raw),
        "raw_key": raw.relative_to(root).as_posix(),
    }
    for path, content in (
        (raw, fetched.body),
        (folder / f"{stem}.metadata.json", json.dumps(metadata, sort_keys=True).encode()),
    ):
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    return metadata
