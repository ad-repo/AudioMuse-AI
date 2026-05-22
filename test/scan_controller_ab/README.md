# Scan Controller A/B Docker Test

This is a full local deployment for testing the scan-controller change against
real Navidrome and real AudioMuse workers.

It starts:

- one real Navidrome container serving a generated MP3 + FLAC album
- one baseline AudioMuse stack from `ghcr.io/neptunehub/audiomuse-ai:latest`
- one patched AudioMuse stack built from the same image plus the scan-controller patch
- separate PostgreSQL and Redis instances for baseline and patched AudioMuse

Run:

```bash
./test/scan_controller_ab/run-ab-test.sh
```

Expected result:

- baseline receives the new five-argument allowlisted job and fails
- patched receives the same job and finishes successfully
- patched sees both MP3 and FLAC tracks from real Navidrome
- patched skips the already-complete allowlisted track
- patched does not write analysis rows for the non-allowlisted MP3/FLAC tracks in the same real Navidrome album

Example passing transcript:

```text
Navidrome ready at http://navidrome:4533
Selected album: AudioMuse Scan Controller AB (...), tracks=4
Album formats: ['flac', 'mp3']
Allowlisted track: Allowlisted Complete (...)

=== baseline ===
job ...: JobStatus.QUEUED
job ...: JobStatus.FAILED
baseline failed as expected
TypeError: analyze_album_task() takes 4 positional arguments but 5 were given

=== patched ===
job ...: JobStatus.QUEUED
job ...: JobStatus.STARTED
job ...: JobStatus.FINISHED
patched result: {'status': 'SUCCESS', 'tracks_analyzed': 0, 'tracks_skipped': 1, 'total_tracks_in_album': 1}

A/B scan-controller deployment test: PASS
```

Run the utility-driven E2E test:

```bash
./test/scan_controller_ab/run-cli-e2e.sh
```

This runs the actual command-line utility against the patched stack:

- `status --album "AudioMuse Scan Controller AB" --json`
- `export-missing --album "AudioMuse Scan Controller AB"`
- `enqueue --album "AudioMuse Scan Controller AB" --dry-run --json`
- `enqueue --album "AudioMuse Scan Controller AB" --limit 1 --json`

Example passing transcript:

```text
status: {'affected_albums': 1, 'complete': 1, 'missing_any': 3, ...}
exported missing IDs: [...]
dry-run: {'affected_albums': 1, 'batches': 1, 'enqueued_jobs': 0, 'selected_tracks': 1}
enqueue: {'affected_albums': 1, 'batches': 1, 'enqueued_jobs': 1, 'selected_tracks': 1}
cli job ...: JobStatus.FINISHED
worker result: {'status': 'SUCCESS', 'tracks_analyzed': 1, 'tracks_skipped': 0, 'total_tracks_in_album': 1}
CLI scan-controller E2E test: PASS
```

Useful URLs:

- Navidrome: http://localhost:14533
- baseline AudioMuse: http://localhost:18081
- patched AudioMuse: http://localhost:18082

Credentials:

- Navidrome user: `admin`
- Navidrome password: `audiomusepassword`

Clean up:

```bash
cd test/scan_controller_ab
docker compose down -v
```
