"""Pack/restore portable runtime state for ephemeral scheduled runners."""

import argparse
import json
import logging
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path

from weather_pipeline.backfill import file_hash
from weather_pipeline.config import Settings, load_settings
from weather_pipeline.logging_setup import configure_logging
from weather_pipeline.pipeline import inspect_status
from weather_pipeline.storage import writer_lock

MAX_SIZE = 2 * 1024**3


def destinations(settings: Settings) -> dict[str, Path]:
    """Map portable archive namespaces onto configured runtime outputs."""
    return {
        "silver.db": settings.working_db,
        "silver.csv": settings.export_csv,
        "bronze": settings.bronze_dir,
        "gold": settings.gold_dir,
        "quarantine": settings.quarantine_dir,
    }


def pack(settings: Settings, archive: Path) -> None:
    """Archive consistent SQLite and CSV state under the pipeline's writer lock."""
    archive = archive.resolve()
    if archive in {
        settings.working_db.resolve(),
        settings.export_csv.resolve(),
        settings.historical_db.resolve(),
        settings.historical_csv.resolve(),
        settings.city_reference.resolve(),
    }:
        raise ValueError("archive cannot replace a source or output file")
    if any(
        root.resolve() in archive.parents
        for root in (
            settings.bronze_dir,
            settings.gold_dir,
            settings.quarantine_dir,
            settings.backup_dir,
        )
    ):
        raise ValueError("archive must be outside raw, analytics and backup directories")
    with writer_lock(settings.working_db), tempfile.TemporaryDirectory() as scratch:
        if inspect_status(settings)["status"] != "ready":
            raise ValueError("validate/repair initialized state before packing")
        database = Path(scratch) / "snapshot.db"
        original = sqlite3.connect(settings.working_db.resolve().as_uri() + "?mode=ro", uri=True)
        backup = sqlite3.connect(database)
        try:
            original.backup(backup)
        finally:
            original.close()
            backup.close()
        files = {"silver.db": database, "silver.csv": settings.export_csv}
        for prefix, root in destinations(settings).items():
            if prefix.startswith("silver."):
                continue
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise ValueError("state archive cannot contain symlinks")
                if path.is_file():
                    files[f"{prefix}/{path.relative_to(root).as_posix()}"] = path
        manifest = {"version": 1, "sha256": {key: file_hash(path) for key, path in files.items()}}
        archive.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=archive.parent, delete=False) as temporary:
            temp = Path(temporary.name)
        try:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as output:
                output.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
                for key, path in files.items():
                    output.write(path, key)
            temp.replace(archive)
        finally:
            temp.unlink(missing_ok=True)


def restore(settings: Settings, archive: Path) -> None:
    """Verify hashes/paths before restoring exclusively into empty working outputs."""
    roots = destinations(settings)
    with writer_lock(settings.working_db), tempfile.TemporaryDirectory() as scratch:
        for prefix, root in roots.items():
            if root.exists() and (prefix.startswith("silver.") or any(root.iterdir())):
                raise ValueError("restore requires empty working outputs; never overwrite state")
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or sum(item.file_size for item in entries) > MAX_SIZE:
                raise ValueError("duplicate entries or oversized state archive")
            manifest = json.loads(source.read("manifest.json"))
            if manifest.get("version") != 1 or not isinstance(manifest.get("sha256"), dict):
                raise ValueError("unsupported state manifest")
            hashes = manifest["sha256"]
            if set(names) != {"manifest.json", *hashes} or not {"silver.db", "silver.csv"} <= set(
                hashes
            ):
                raise ValueError("state manifest file list differs from archive")
            targets: dict[str, Path] = {}
            for entry in entries:
                key = entry.filename
                if key == "manifest.json":
                    continue
                path = Path(key)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in key
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.is_dir()
                ):
                    raise ValueError("unsafe state archive path")
                if key in {"silver.db", "silver.csv"}:
                    targets[key] = roots[key]
                elif len(path.parts) > 1 and path.parts[0] in {"bronze", "gold", "quarantine"}:
                    targets[key] = roots[path.parts[0]].joinpath(*path.parts[1:])
                else:
                    raise ValueError("unknown state archive namespace")
                staged = Path(scratch) / path
                staged.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as incoming, staged.open("xb") as output:
                    shutil.copyfileobj(incoming, output)
                if file_hash(staged) != hashes[key]:
                    raise ValueError("state archive checksum mismatch")
            # All checks precede writes to any configured destination.
            for key, target in targets.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                with (Path(scratch) / key).open("rb") as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output)


def main() -> int:
    """Run portable state commands without exposing authentication material."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["pack", "restore"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()
    configure_logging()
    try:
        settings = load_settings(args.config)
        (pack if args.command == "pack" else restore)(settings, args.archive)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, zipfile.BadZipFile, KeyError):
        logging.getLogger("weather_pipeline").exception("State archive command failed")
        return 2
    logging.getLogger("weather_pipeline").info("State archive %s completed", args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
