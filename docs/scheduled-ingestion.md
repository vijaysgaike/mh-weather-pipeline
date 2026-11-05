# Scheduled GitHub ingestion

The workflow is `.github/workflows/scheduled_ingest.yml`, every four hours at
minute 17 UTC (05:47, 09:47, 13:47, 17:47, 21:47, 01:47 next day Asia/Kolkata). It needs no weather API
secret. It uses the repository's scoped GITHUB_TOKEN for artifact access.

## Initial setup

1. Complete local backfill, ingestion and validation with your actual data.
2. Pack runtime state while the writer lock is held:

   ```bash
   .venv/bin/python scripts/state_archive.py pack /tmp/weather-state.zip
   ```

3. Store that archive at an HTTPS download location you control. Treat the
   archive as data: choose access and sharing deliberately. Do not commit it or
   expose private signed URLs in repository files. Add its download URL as the
   repository Actions secret `WEATHER_SEED_URL` (or supply the workflow's
   `seed_url` input for the first manual run). Prefer the secret for private URLs.
4. Manually run **Scheduled ingestion** in GitHub Actions. It downloads the seed,
   verifies its manifest and checksums, restores into empty runtime outputs,
   validates, ingests, and uploads a `weather-state` artifact.
5. Confirm the result. Scheduled invocations run automatically every four hours
   once the workflow is on the default branch and GitHub Actions is enabled. No
   enablement variable is required. Manual invocations remain available for
   setup/recovery. If there is no initial state, the job fails with setup instructions.

Each subsequent run restores the newest unexpired artifact named weather-state.
Concurrency groups serialize runs. It preserves Silver history, Bronze raw files,
Gold exports, quarantine and run metadata. Original source datasets and backfill
snapshots are not uploaded: keep their separate external backups. Runtime state
archives use portable paths; old provenance records retain original machine paths,
while Bronze sidecars also carry a relative raw_key for locating restored files.

Failed/partial ingestion still attempts export recovery and packs accepted progress.
The job reports failure for partial runs so alerts are not hidden. An ingestion
failure followed by a successful repair still retains the original run's failed
status. If repair/validation/packing fails, the upload is skipped and the previous
valid artifact remains the restore source. Durable restoration must succeed
before any ingestion starts. Missing/expired state fails explicitly rather than
silently reinitializing history. Artifact retention is 90 days and depends on
repository storage quotas; monitor those quotas and keep an external backup.

## Restore locally

Download the artifact, extract its contained weather-state.zip, and configure
**empty** output locations in another config before restoring:

```bash
.venv/bin/python scripts/state_archive.py restore /path/weather-state.zip --config recovery.yaml
.venv/bin/weather-pipeline --config recovery.yaml validate
```

Restore refuses to overwrite working files. Checksums, allowed namespaces,
archive size (2 GiB uncompressed maximum), and path traversal are checked before
writing outputs. Use fresh empty output paths after a failed/partial restore;
do not delete your only good state. Snapshot packing uses SQLite online backup
under the pipeline lock; it does not copy a live WAL database blindly.

## Trade-offs

Committing CSV updates creates noisy diffs, grows Git history and exposes the
dataset to the repository audience. Artifact persistence avoids Git bloat but
expires and is not a long-term backup. For a larger or business-critical workload,
replace artifacts with durable versioned object storage and archive Bronze
partitions under a retention policy. That requires credentials and infrastructure
outside this lightweight project's scope. A seven-day live lookback cannot fill
longer outages; see the runbook's historical-gap policy.
