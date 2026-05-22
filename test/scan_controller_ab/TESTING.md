# Scan Controller Testing Guide

This guide explains how to test the AudioMuse scan-controller workflow with a
real local Docker deployment. The goal is to prove that newly discovered tracks
can be identified and fed to AudioMuse without guessing `NUM_RECENT_ALBUMS` or
reprocessing an entire library.

## What This Stack Is

The A/B stack starts:

- real Navidrome at `http://localhost:14533`
- baseline AudioMuse at `http://localhost:18081`
- patched AudioMuse at `http://localhost:18082`
- separate PostgreSQL and Redis instances for baseline and patched AudioMuse

The watched music folder on the host is:

```text
/Users/ad/Projects/AudioMuse-AI-fork/test/scan_controller_ab/music/
```

Navidrome sees that folder as:

```text
/music
```

Navidrome login:

```text
admin / audiomusepassword
```

## Start Or Reset The Test Stack

From the repo root:

```bash
cd /Users/ad/Projects/AudioMuse-AI-fork
./test/scan_controller_ab/run-ab-test.sh
```

This creates a generated test album named:

```text
AudioMuse Scan Controller AB
```

The album contains both MP3 and FLAC tracks. The script also runs an A/B check:

- baseline AudioMuse fails when given the new allowlisted job signature
- patched AudioMuse accepts the same job
- patched AudioMuse processes only the allowlisted track

To destroy the stack and all test data:

```bash
cd /Users/ad/Projects/AudioMuse-AI-fork/test/scan_controller_ab
docker compose down -v
```

## Automated Tests

### A/B Worker Compatibility Test

```bash
./test/scan_controller_ab/run-ab-test.sh
```

This verifies the patched worker supports the new `only_track_ids` argument.

Expected result:

```text
A/B scan-controller deployment test: PASS
```

### CLI End-To-End Test

```bash
./test/scan_controller_ab/run-cli-e2e.sh
```

This uses the actual command-line utility against real Navidrome and the
patched AudioMuse worker. It runs:

- `status`
- `export-missing`
- `enqueue --dry-run`
- `enqueue --limit 1`
- waits for the real worker job to finish

Expected result:

```text
CLI scan-controller E2E test: PASS
```

## Manual User-Style Test

Use this when you want to verify the workflow the way a user would.

### 1. Open Navidrome

Open:

```text
http://localhost:14533
```

Log in with:

```text
admin / audiomusepassword
```

Confirm the generated album appears:

```text
AudioMuse Scan Controller AB
```

### 2. Add New Music

Copy files into:

```text
/Users/ad/Projects/AudioMuse-AI-fork/test/scan_controller_ab/music/
```

Example:

```bash
cp ~/Music/example.flac /Users/ad/Projects/AudioMuse-AI-fork/test/scan_controller_ab/music/
cp ~/Music/example.mp3 /Users/ad/Projects/AudioMuse-AI-fork/test/scan_controller_ab/music/
```

### 3. Trigger A Navidrome Scan

```bash
curl "http://localhost:14533/rest/startScan.view?u=admin&p=enc:617564696f6d75736570617373776f7264&v=1.16.1&c=test&f=json"
```

Refresh Navidrome and confirm the new tracks appear.

### 4. Check Missing AudioMuse Analysis

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint python3 \
  ab-runner \
  /app/tools/audiomuse_scan_controller.py --skip-clap --skip-lyrics status --json
```

Important fields:

- `tracks_checked`: tracks discovered from Navidrome
- `complete`: tracks already analyzed by AudioMuse
- `missing_musicnn`: tracks missing the main AudioMuse analysis
- `affected_albums`: albums containing missing tracks

### 5. Export Missing Tracks

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint python3 \
  ab-runner \
  /app/tools/audiomuse_scan_controller.py --skip-clap --skip-lyrics export-missing /tmp/missing.csv
```

To view the CSV:

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint bash \
  ab-runner \
  -lc "cat /tmp/missing.csv"
```

### 6. Dry-Run Enqueue

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint python3 \
  ab-runner \
  /app/tools/audiomuse_scan_controller.py --skip-clap --skip-lyrics enqueue --dry-run --json
```

This should not start analysis. It only shows how many tracks, albums, and
batches would be queued.

### 7. Enqueue A Small Batch

Start with one track:

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint python3 \
  ab-runner \
  /app/tools/audiomuse_scan_controller.py --skip-clap --skip-lyrics enqueue --limit 1 --json
```

Save the returned `job_ids`.

### 8. Watch The Worker

```bash
docker logs -f am-scan-ab-worker-patched
```

You should see the patched worker process only the selected allowlisted track.

### 9. Confirm The Missing Count Decreased

Run status again:

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint python3 \
  ab-runner \
  /app/tools/audiomuse_scan_controller.py --skip-clap --skip-lyrics status --json
```

Expected result:

- `complete` increases
- `missing_musicnn` decreases

## Album-Scoped Manual Test

To test only the generated MP3 + FLAC album:

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint python3 \
  ab-runner \
  /app/tools/audiomuse_scan_controller.py --skip-clap --skip-lyrics status \
  --album "AudioMuse Scan Controller AB" --json
```

Dry-run only that album:

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint python3 \
  ab-runner \
  /app/tools/audiomuse_scan_controller.py --skip-clap --skip-lyrics enqueue \
  --album "AudioMuse Scan Controller AB" --dry-run --json
```

Enqueue one missing track from that album:

```bash
docker compose -f test/scan_controller_ab/docker-compose.yaml run --rm \
  --entrypoint python3 \
  ab-runner \
  /app/tools/audiomuse_scan_controller.py --skip-clap --skip-lyrics enqueue \
  --album "AudioMuse Scan Controller AB" --limit 1 --json
```

## What Each Scenario Proves

| Scenario | What it proves |
| --- | --- |
| A/B test | The baseline worker does not support allowlisted jobs, while the patched worker does. |
| CLI E2E | The real CLI can discover missing tracks, dry-run, enqueue, and drive a real worker job. |
| MP3 + FLAC album | Navidrome scans multiple audio formats and the utility sees them. |
| Manual add music | A user can add files, scan Navidrome, and see AudioMuse missing counts change. |
| `enqueue --limit 1` | The utility can feed a controlled small batch instead of a full library run. |
| Re-running `status` | The DB status changes after worker analysis completes. |

## Notes

The test stack disables CLAP, lyrics, and MuLan:

```text
CLAP_ENABLED=false
LYRICS_ENABLED=false
MULAN_ENABLED=false
```

That keeps the test focused on MusicNN scan status and track allowlisting. To
test optional feature embeddings later, enable those flags and rerun the same
workflow with enough CPU/GPU capacity for the extra models.
