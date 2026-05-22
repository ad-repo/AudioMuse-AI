#!/usr/bin/env python3
"""External scan controller for AudioMuse analysis.

This tool audits Navidrome tracks against AudioMuse's PostgreSQL analysis
tables and can enqueue precise track allowlists into the existing RQ workers.
Run it from the AudioMuse project root, using the same environment variables
as the AudioMuse containers.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


DEFAULT_BATCH_SIZE = 100
DB_CHUNK_SIZE = 1000


@dataclass(frozen=True)
class TrackStatus:
    track_id: str
    album_id: str | None
    title: str
    artist: str
    album: str
    missing_musicnn: bool
    missing_clap: bool
    missing_lyrics: bool

    @property
    def complete(self) -> bool:
        return not (self.missing_musicnn or self.missing_clap or self.missing_lyrics)

    @property
    def missing_any(self) -> bool:
        return not self.complete


def _chunked(items: list[str], size: int = DB_CHUNK_SIZE) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _table_exists(table_name: str) -> bool:
    from app_helper import get_db

    with get_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (table_name,))
        return cur.fetchone()[0] is not None


def _existing_musicnn_ids(track_ids: list[str]) -> set[str]:
    try:
        from tasks import analysis_helper as _ah

        existing: set[str] = set()
        for chunk in _chunked(track_ids):
            existing.update(_ah.get_existing_track_ids(chunk))
        return existing
    except ImportError:
        from app_helper import get_db

        existing: set[str] = set()
        for chunk in _chunked(track_ids):
            with get_db() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT s.item_id FROM score s JOIN embedding e ON s.item_id = e.item_id "
                    "WHERE s.item_id IN %s AND s.other_features IS NOT NULL "
                    "AND s.energy IS NOT NULL AND s.mood_vector IS NOT NULL "
                    "AND s.tempo IS NOT NULL",
                    (tuple(chunk),),
                )
                existing.update(row[0] for row in cur.fetchall())
        return existing


def _missing_ids(table_name: str, track_ids: list[str]) -> set[str]:
    if not _table_exists(table_name):
        return set()
    try:
        from tasks import analysis_helper as _ah

        missing: set[str] = set()
        for chunk in _chunked(track_ids):
            missing.update(_ah.get_missing_ids_in_table(table_name, chunk))
        return missing
    except ImportError:
        from app_helper import get_db

        if table_name not in {"clap_embedding", "lyrics_embedding", "mulan_embedding"}:
            raise ValueError(f"Unsupported table name: {table_name}")
        ids = [str(track_id) for track_id in track_ids]
        missing: set[str] = set()
        for chunk in _chunked(ids):
            with get_db() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT item_id FROM {table_name} WHERE item_id IN %s", (tuple(chunk),))
                existing = {row[0] for row in cur.fetchall()}
            missing.update(set(chunk) - existing)
        return missing


def load_statuses(include_clap: bool, include_lyrics: bool) -> list[TrackStatus]:
    from tasks.mediaserver import get_all_songs

    songs = get_all_songs()
    track_ids = [
        str(song.get("Id") or song.get("id"))
        for song in songs
        if song.get("Id") or song.get("id")
    ]

    existing_musicnn = _existing_musicnn_ids(track_ids)
    missing_clap = _missing_ids("clap_embedding", track_ids) if include_clap else set()
    missing_lyrics = _missing_ids("lyrics_embedding", track_ids) if include_lyrics else set()

    return build_statuses_from_songs(songs, existing_musicnn, missing_clap, missing_lyrics)


def build_statuses_from_songs(
    songs: list[dict],
    existing_musicnn: set[str],
    missing_clap: set[str],
    missing_lyrics: set[str],
) -> list[TrackStatus]:
    statuses: list[TrackStatus] = []
    for song in songs:
        track_id = song.get("Id") or song.get("id")
        if not track_id:
            continue
        track_id = str(track_id)
        album_id = song.get("AlbumId") or song.get("albumId")
        statuses.append(
            TrackStatus(
                track_id=track_id,
                album_id=str(album_id) if album_id else None,
                title=song.get("Name") or song.get("title") or "",
                artist=song.get("AlbumArtist") or song.get("artist") or "",
                album=song.get("Album") or song.get("album") or "",
                missing_musicnn=track_id not in existing_musicnn,
                missing_clap=track_id in missing_clap,
                missing_lyrics=track_id in missing_lyrics,
            )
        )
    return statuses


def summarize(statuses: list[TrackStatus]) -> dict[str, int]:
    missing = [status for status in statuses if status.missing_any]
    return {
        "tracks_checked": len(statuses),
        "complete": sum(1 for status in statuses if status.complete),
        "missing_any": len(missing),
        "missing_musicnn": sum(1 for status in statuses if status.missing_musicnn),
        "missing_clap": sum(1 for status in statuses if status.missing_clap),
        "missing_lyrics": sum(1 for status in statuses if status.missing_lyrics),
        "affected_albums": len({status.album_id for status in missing if status.album_id}),
        "missing_without_album_id": sum(1 for status in missing if not status.album_id),
    }


def filter_statuses(statuses: list[TrackStatus], album: str | None = None) -> list[TrackStatus]:
    if album is None:
        return statuses
    return [status for status in statuses if status.album == album]


def print_summary(summary: dict[str, int]) -> None:
    for key, value in summary.items():
        print(f"{key}: {value}")


def export_csv(statuses: list[TrackStatus], path: str, missing_only: bool) -> None:
    rows = [status for status in statuses if status.missing_any or not missing_only]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "track_id",
            "album_id",
            "title",
            "artist",
            "album",
            "missing_musicnn",
            "missing_clap",
            "missing_lyrics",
        ])
        for status in rows:
            writer.writerow([
                status.track_id,
                status.album_id or "",
                status.title,
                status.artist,
                status.album,
                int(status.missing_musicnn),
                int(status.missing_clap),
                int(status.missing_lyrics),
            ])
    print(f"Wrote {len(rows)} rows to {path}")


def load_track_ids_from_csv(path: str) -> set[str]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "track_id" not in (reader.fieldnames or []):
            raise ValueError("CSV must contain a track_id column")
        return {row["track_id"] for row in reader if row.get("track_id")}


def enqueue_statuses(
    statuses: list[TrackStatus],
    *,
    top_n_moods: int,
    batch_size: int,
    limit: int | None,
    from_csv: str | None,
    dry_run: bool,
) -> dict[str, int | list[str]]:
    selected = [status for status in statuses if status.missing_any and status.album_id]
    if from_csv:
        selected_ids = load_track_ids_from_csv(from_csv)
        selected = [status for status in selected if status.track_id in selected_ids]
    if limit is not None:
        selected = selected[:limit]

    by_album: dict[str, list[TrackStatus]] = defaultdict(list)
    for status in selected:
        by_album[status.album_id or ""].append(status)

    job_ids: list[str] = []
    batches = 0
    if not dry_run:
        from rq import Retry
        from app_helper import rq_queue_default

        parent_task_id = f"external-scan-{uuid.uuid4()}"
        for album_id, album_tracks in by_album.items():
            for batch_index in range(0, len(album_tracks), batch_size):
                batch = album_tracks[batch_index:batch_index + batch_size]
                album_name = batch[0].album or "Unknown Album"
                job = rq_queue_default.enqueue(
                    "tasks.analysis.analyze_album_task",
                    args=(
                        album_id,
                        album_name,
                        top_n_moods,
                        parent_task_id,
                        [status.track_id for status in batch],
                    ),
                    job_id=str(uuid.uuid4()),
                    job_timeout=-1,
                    retry=Retry(max=3),
                    description=f"External missing-track analysis: {album_name}",
                )
                job_ids.append(job.id)
                batches += 1
    else:
        batches = sum(
            (len(album_tracks) + batch_size - 1) // batch_size
            for album_tracks in by_album.values()
        )

    return {
        "selected_tracks": len(selected),
        "affected_albums": len(by_album),
        "batches": batches,
        "enqueued_jobs": len(job_ids),
        "job_ids": job_ids,
    }


def build_parser() -> argparse.ArgumentParser:
    try:
        from config import CLAP_ENABLED, LYRICS_ENABLED, TOP_N_MOODS
    except Exception:
        CLAP_ENABLED = True
        LYRICS_ENABLED = True
        TOP_N_MOODS = 5

    parser = argparse.ArgumentParser(description="Audit and control AudioMuse analysis jobs.")
    parser.add_argument(
        "--include-clap",
        action="store_true",
        default=CLAP_ENABLED,
        help="Check clap_embedding status. Defaults to config.CLAP_ENABLED.",
    )
    parser.add_argument("--skip-clap", action="store_false", dest="include_clap", help="Do not check clap_embedding status.")
    parser.add_argument(
        "--include-lyrics",
        action="store_true",
        default=LYRICS_ENABLED,
        help="Check lyrics_embedding status. Defaults to config.LYRICS_ENABLED.",
    )
    parser.add_argument("--skip-lyrics", action="store_false", dest="include_lyrics", help="Do not check lyrics_embedding status.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Print scan status counts.")
    status.add_argument("--album", help="Limit status counts to one album name.")
    status.add_argument("--json", action="store_true", help="Print JSON instead of text.")

    export = subparsers.add_parser("export-missing", help="Export track status rows as CSV.")
    export.add_argument("path", help="Output CSV path.")
    export.add_argument("--album", help="Limit export to one album name.")
    export.add_argument("--all", action="store_true", help="Export all tracks, not just missing/partial tracks.")

    enqueue = subparsers.add_parser("enqueue", help="Enqueue missing tracks grouped by album.")
    enqueue.add_argument("--album", help="Limit enqueueing to one album name.")
    enqueue.add_argument("--dry-run", action="store_true", help="Show what would be enqueued.")
    enqueue.add_argument("--from-csv", help="Limit enqueueing to track IDs from a CSV exported by this tool.")
    enqueue.add_argument("--limit", type=int, help="Maximum number of missing tracks to enqueue.")
    enqueue.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Track IDs per album job.")
    enqueue.add_argument("--top-n-moods", type=int, default=TOP_N_MOODS, help="Top moods to persist per track.")
    enqueue.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from flask_app import app
    except ImportError:
        from app import app

    with app.app_context():
        statuses = load_statuses(
            include_clap=args.include_clap,
            include_lyrics=args.include_lyrics,
        )
        if args.command == "status":
            statuses = filter_statuses(statuses, album=args.album)
            summary = summarize(statuses)
            if args.json:
                print(json.dumps(summary, indent=2, sort_keys=True))
            else:
                print_summary(summary)
            return 0

        if args.command == "export-missing":
            statuses = filter_statuses(statuses, album=args.album)
            export_csv(statuses, args.path, missing_only=not args.all)
            return 0

        if args.command == "enqueue":
            statuses = filter_statuses(statuses, album=args.album)
            result = enqueue_statuses(
                statuses,
                top_n_moods=args.top_n_moods,
                batch_size=args.batch_size,
                limit=args.limit,
                from_csv=args.from_csv,
                dry_run=args.dry_run,
            )
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print_summary({key: value for key, value in result.items() if key != "job_ids"})
                if result["job_ids"]:
                    print("job_ids:")
                    for job_id in result["job_ids"]:
                        print(job_id)
            return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
