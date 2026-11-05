# Scheduled GitHub ingestion and maintained data files

The workflow `.github/workflows/scheduled_ingest.yml` runs every four hours at
minute 17 UTC: 05:47, 09:47, 13:47, 17:47, 21:47, and 01:47 next day Asia/Kolkata.
It can also be started manually in Actions. No Open-Meteo API key is required.

## Where to find the final data on GitHub

Select the **weather-data** branch in the same repository:

- `weather_dataset.csv.gz`: full accumulated 37-column CSV, gzip-compressed.
  Download and decompress it to get one complete CSV.
- `daily/YYYY/MM/YYYY-MM-DD.csv`: smaller ordinary CSV files, browsable by date.
- `manifest.json`: total row count, latest UTC observation, schema, SHA-256
  checksums and the daily file list.

The code stays on main. Dataset updates have their own commit history on
weather-data. Accepted observations are never rewritten by the pipeline; a
new commit adds newly accepted city/timestamp records. It replaces the compressed
snapshot with the new accumulated version and extends the affected daily files.
No-change runs create no data commit. These files inherit repository visibility.
Supplied raw input files, database binaries, backups, .env and API responses are
not committed to either branch.

## Initial setup

The initial data branch is prepared locally from validated Silver data. Publish
both branches once from your authenticated terminal:

```bash
git push origin main weather-data
```

In GitHub Actions, run **Scheduled ingestion** once and inspect its result. It
then runs every four hours with no enablement variable or seed URL. The workflow
must be on the default branch and Actions must be enabled. It explicitly grants
the job `contents: write` to commit data and `actions: read` to restore artifacts.
A branch rule that prevents bot pushes to weather-data must allow this job or
the publish step will fail; it never force-pushes or discards branch changes.

## What happens on each run

1. Check out code and the weather-data branch into separate directories.
2. Restore the newest unexpired weather-state artifact when available.
3. Verify/decompress the committed dataset and transactionally merge it into
   Silver. This also bootstraps an empty runner if no artifact exists. Existing
   conflicts cause a failure; missing published rows are added, never overwritten.
   This prevents loss if a prior data push succeeded but artifact upload failed.
4. Validate the restored data, fetch Open-Meteo for historical cities, and append
   unique valid records to the working database and CSV.
5. Repair/validate exports and pack detailed runtime state.
6. Regenerate the full compressed CSV, daily CSVs, and manifest; commit and push
   changed files to weather-data using github-actions[bot].
7. Upload the updated weather-state artifact, even if data publication failed,
   so accepted progress can be retried next run.

Partial city failures still publish accepted valid progress and report the job
as failed. If validation/recovery fails, publication is skipped. A concurrent
external edit to weather-data can reject a normal push; this is reported rather
than resolved with a force push. The next run checks out the new branch tip.
Concurrency groups serialize workflow runs; the pipeline lock serializes sinks.

GitHub's GITHUB_TOKEN pushes do not recursively trigger push workflows:
[workflow trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## Recovery and size management

The branch preserves accepted weather records independently of artifact expiry.
Artifacts (90-day retention) additionally contain Bronze, detailed lineage,
quarantine and prior run history. If they expire, Silver/Gold are recovered from
the branch, with new recovery lineage; old raw/run metadata cannot be recreated
from a CSV. Keep external backups if you need the complete audit trail.

The current uncompressed CSV is about 69 MiB. Daily partitioning keeps ordinary
files small, while the deterministic gzip full snapshot is about 10 MiB. The
publisher checks a 95 MiB per-file budget before committing. GitHub blocks files
above 100 MiB; see [large-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
Git history still grows with daily data; monitor repository size and migrate to
LFS/object storage if necessary. No new dependency is needed for this approach.
A seven-day API lookback cannot automatically fill longer data gaps.

For local branch regeneration or recovery into separately configured outputs:

```bash
.venv/bin/python scripts/publish_data.py publish /path/to/data-branch-checkout
.venv/bin/python scripts/publish_data.py restore /path/to/data-branch-checkout --config recovery.yaml
```

Published snapshot restoration verifies gzip and CSV hashes, byte count, schema,
row count and a 2 GiB decompression budget before merging. Validation or conflicts
roll the snapshot merge back. Repeating the same snapshot leaves accepted rows
unchanged. Archive packing/restoration is also available via
`scripts/state_archive.py` for retaining full runtime metadata, with path/hash
verification and refusal to overwrite existing runtime state.
