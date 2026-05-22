# External Scan Controller

`tools/audiomuse_scan_controller.py` audits Navidrome tracks against the
AudioMuse PostgreSQL analysis tables and can enqueue only selected missing
tracks. It is intended for large libraries where guessing `NUM_RECENT_ALBUMS`
is not practical.

Run the tool from the AudioMuse project root with the same environment
variables used by the AudioMuse containers, including `DATABASE_URL`,
`REDIS_URL`, `NAVIDROME_URL`, `NAVIDROME_USER`, and `NAVIDROME_PASSWORD`.

## Status

Print a read-only summary:

```bash
python tools/audiomuse_scan_controller.py status
```

JSON output:

```bash
python tools/audiomuse_scan_controller.py status --json
```

The summary includes checked tracks, complete tracks, missing MusicNN, missing
CLAP, missing lyrics, affected albums, and missing tracks without an album ID.

## Export Missing Tracks

Export missing or partially analyzed tracks to CSV:

```bash
python tools/audiomuse_scan_controller.py export-missing missing.csv
```

Export every checked track:

```bash
python tools/audiomuse_scan_controller.py export-missing all-tracks.csv --all
```

## Enqueue Controlled Work

Show what would be enqueued without starting workers:

```bash
python tools/audiomuse_scan_controller.py enqueue --dry-run
```

Enqueue the first 500 missing tracks:

```bash
python tools/audiomuse_scan_controller.py enqueue --limit 500
```

Enqueue only track IDs from a reviewed CSV:

```bash
python tools/audiomuse_scan_controller.py enqueue --from-csv missing.csv
```

The controller groups selected tracks by album and enqueues
`tasks.analysis.analyze_album_task` with a track allowlist. Existing AudioMuse
jobs still work because the new allowlist argument defaults to `None`.

## Feature Flags

By default, the controller follows `CLAP_ENABLED` and `LYRICS_ENABLED` from
`config.py`. Override them when needed:

```bash
python tools/audiomuse_scan_controller.py --skip-clap status
python tools/audiomuse_scan_controller.py --skip-lyrics enqueue --dry-run
```

