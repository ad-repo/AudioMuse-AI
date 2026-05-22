#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time

from rq import Queue
from rq.job import Job

from ab_test import (
    choose_album,
    clean_and_seed,
    connect_db,
    redis_conn,
    score_exists,
    wait_for_navidrome,
    wait_for_schema,
    wait_for_songs,
)


ALBUM_NAME = "AudioMuse Scan Controller AB"
CLI = "/app/tools/audiomuse_scan_controller.py"


def run_cli(*args):
    command = ["python3", CLI, "--skip-clap", "--skip-lyrics", *args]
    env = {
        **os.environ,
        "MEDIASERVER_TYPE": "navidrome",
        "POSTGRES_HOST": os.environ["PATCHED_POSTGRES_HOST"],
        "REDIS_URL": os.environ["PATCHED_REDIS_URL"],
        "CLAP_ENABLED": "false",
        "LYRICS_ENABLED": "false",
        "MULAN_ENABLED": "false",
    }
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout)
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return result.stdout


def wait_for_job_id(redis_url, job_id):
    conn = redis_conn(redis_url)
    deadline = time.time() + 180
    last_status = None
    while time.time() < deadline:
        job = Job.fetch(job_id, connection=conn)
        status = job.get_status(refresh=True)
        if status != last_status:
            print(f"cli job {job_id}: {status}")
            last_status = status
        if job.is_finished:
            return job
        if job.is_failed:
            raise AssertionError(f"CLI-enqueued job failed:\n{job.exc_info}")
        time.sleep(2)
    raise TimeoutError(f"CLI-enqueued job {job_id} did not finish")


def main():
    pg_host = os.environ["PATCHED_POSTGRES_HOST"]
    redis_url = os.environ["PATCHED_REDIS_URL"]

    wait_for_navidrome()
    songs = wait_for_songs()
    album_id, album_name, album_songs = choose_album(songs)
    if album_name != ALBUM_NAME:
        raise AssertionError(f"Expected album {ALBUM_NAME!r}, got {album_name!r}")
    selected = album_songs[0]
    selected_id = selected["id"]
    all_album_ids = [song["id"] for song in album_songs]

    wait_for_schema(pg_host)
    clean_and_seed(pg_host, selected_id, all_album_ids)
    Queue("default", connection=redis_conn(redis_url)).empty()

    print(f"CLI E2E album: {album_name} ({album_id}), tracks={len(album_songs)}")
    print(f"Seeded complete track: {selected.get('title')} ({selected_id})")

    status = json.loads(run_cli("status", "--album", ALBUM_NAME, "--json"))
    print(f"status: {status}")
    if status["tracks_checked"] != len(album_songs):
        raise AssertionError(status)
    if status["complete"] != 1:
        raise AssertionError(status)
    if status["missing_musicnn"] != len(album_songs) - 1:
        raise AssertionError(status)

    csv_path = "/tmp/audiomuse-scan-controller-missing.csv"
    run_cli("export-missing", "--album", ALBUM_NAME, csv_path)
    with open(csv_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    missing_ids = {row["track_id"] for row in rows}
    expected_missing = set(all_album_ids) - {selected_id}
    print(f"exported missing IDs: {sorted(missing_ids)}")
    if missing_ids != expected_missing:
        raise AssertionError(f"Expected missing {expected_missing}, got {missing_ids}")

    dry_run = json.loads(run_cli("enqueue", "--album", ALBUM_NAME, "--limit", "1", "--dry-run", "--json"))
    print(f"dry-run: {dry_run}")
    if dry_run["selected_tracks"] != 1 or dry_run["batches"] != 1 or dry_run["enqueued_jobs"] != 0:
        raise AssertionError(dry_run)

    enqueued = json.loads(run_cli("enqueue", "--album", ALBUM_NAME, "--limit", "1", "--json"))
    print(f"enqueue: {enqueued}")
    if enqueued["selected_tracks"] != 1 or enqueued["enqueued_jobs"] != 1:
        raise AssertionError(enqueued)

    job = wait_for_job_id(redis_url, enqueued["job_ids"][0])
    result = job.return_value()
    selected_missing_id = job.args[4][0]
    print(f"worker result: {result}")
    if not result or result.get("status") != "SUCCESS":
        raise AssertionError(result)
    if result.get("tracks_analyzed") != 1:
        raise AssertionError(f"Expected worker to analyze one CLI-selected missing track, got {result}")

    if not score_exists(pg_host, selected_missing_id):
        raise AssertionError(f"CLI-selected missing track was not written to score: {selected_missing_id}")
    for track_id in set(all_album_ids) - {selected_id, selected_missing_id}:
        if score_exists(pg_host, track_id):
            raise AssertionError(f"Non-selected track was written to score: {track_id}")

    print("CLI scan-controller E2E test: PASS")


if __name__ == "__main__":
    main()
