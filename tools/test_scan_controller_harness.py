#!/usr/bin/env python3
"""Dependency-free smoke harness for the external scan controller.

This script simulates the parts of the deployment that matter for the scan
controller:

- Navidrome track metadata, including album IDs.
- AudioMuse DB completeness sets for MusicNN, CLAP, and lyrics.
- RQ enqueueing of selected track allowlists.

It does not import Flask, Redis, psycopg2, numpy, or AudioMuse's model stack.
Run it from the repository root:

    python tools/test_scan_controller_harness.py
"""

from __future__ import annotations

import sys
import types

from audiomuse_scan_controller import (
    build_statuses_from_songs,
    enqueue_statuses,
    summarize,
)


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


class FakeJob:
    def __init__(self, job_id):
        self.id = job_id


class FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return FakeJob(f"job-{len(self.calls)}")


def install_fake_rq(fake_queue):
    app_helper = types.SimpleNamespace(rq_queue_default=fake_queue)
    rq = types.SimpleNamespace(Retry=lambda max: ("retry", max))
    sys.modules["app_helper"] = app_helper
    sys.modules["rq"] = rq


def main():
    fake_songs = [
        {"Id": "track-1", "AlbumId": "album-a", "Name": "Already Done", "AlbumArtist": "Artist", "Album": "Album A"},
        {"Id": "track-2", "AlbumId": "album-a", "Name": "Missing MusicNN", "AlbumArtist": "Artist", "Album": "Album A"},
        {"Id": "track-3", "AlbumId": "album-b", "Name": "Missing CLAP", "AlbumArtist": "Artist", "Album": "Album B"},
        {"Id": "track-4", "AlbumId": "album-b", "Name": "Missing Lyrics", "AlbumArtist": "Artist", "Album": "Album B"},
        {"Id": "track-5", "Name": "No Album ID", "AlbumArtist": "Artist", "Album": "Album C"},
    ]
    existing_musicnn = {"track-1", "track-3", "track-4", "track-5"}
    missing_clap = {"track-3"}
    missing_lyrics = {"track-4", "track-5"}

    statuses = build_statuses_from_songs(
        fake_songs,
        existing_musicnn,
        missing_clap,
        missing_lyrics,
    )
    summary = summarize(statuses)
    assert_equal(summary["tracks_checked"], 5, "tracks_checked")
    assert_equal(summary["complete"], 1, "complete")
    assert_equal(summary["missing_any"], 4, "missing_any")
    assert_equal(summary["missing_musicnn"], 1, "missing_musicnn")
    assert_equal(summary["missing_clap"], 1, "missing_clap")
    assert_equal(summary["missing_lyrics"], 2, "missing_lyrics")
    assert_equal(summary["affected_albums"], 2, "affected_albums")
    assert_equal(summary["missing_without_album_id"], 1, "missing_without_album_id")

    dry_run = enqueue_statuses(
        statuses,
        top_n_moods=5,
        batch_size=10,
        limit=None,
        from_csv=None,
        dry_run=True,
    )
    assert_equal(dry_run["selected_tracks"], 3, "dry_run selected_tracks")
    assert_equal(dry_run["affected_albums"], 2, "dry_run affected_albums")
    assert_equal(dry_run["batches"], 2, "dry_run batches")
    assert_equal(dry_run["enqueued_jobs"], 0, "dry_run enqueued_jobs")

    fake_queue = FakeQueue()
    install_fake_rq(fake_queue)
    enqueued = enqueue_statuses(
        statuses,
        top_n_moods=7,
        batch_size=10,
        limit=None,
        from_csv=None,
        dry_run=False,
    )
    assert_equal(enqueued["selected_tracks"], 3, "enqueue selected_tracks")
    assert_equal(enqueued["enqueued_jobs"], 2, "enqueue enqueued_jobs")
    assert_equal(len(fake_queue.calls), 2, "fake queue call count")

    first_args = fake_queue.calls[0]["kwargs"]["args"]
    second_args = fake_queue.calls[1]["kwargs"]["args"]
    assert_equal(first_args[0], "album-a", "first album_id")
    assert_equal(first_args[1], "Album A", "first album_name")
    assert_equal(first_args[2], 7, "first top_n_moods")
    assert_equal(first_args[4], ["track-2"], "first allowlist")
    assert_equal(second_args[0], "album-b", "second album_id")
    assert_equal(second_args[4], ["track-3", "track-4"], "second allowlist")

    print("scan controller harness: PASS")


if __name__ == "__main__":
    main()
