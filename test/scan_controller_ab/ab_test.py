#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
import uuid

import psycopg2
import redis
import requests
from rq import Queue
from rq.job import Job


NAVIDROME_URL = os.environ["NAVIDROME_URL"].rstrip("/")
NAVIDROME_USER = os.environ.get("NAVIDROME_USER", "admin")
NAVIDROME_PASSWORD = os.environ.get("NAVIDROME_PASSWORD", "audiomusepassword")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "audiomuse")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "audiomusepassword")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "audiomusedb")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))


def subsonic_auth():
    return {
        "u": NAVIDROME_USER,
        "p": f"enc:{NAVIDROME_PASSWORD.encode('utf-8').hex()}",
        "v": "1.16.1",
        "c": "AudioMuse-AB-Test",
        "f": "json",
    }


def navidrome_request(endpoint, params=None):
    response = requests.get(
        f"{NAVIDROME_URL}/rest/{endpoint}.view",
        params={**subsonic_auth(), **(params or {})},
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()["subsonic-response"]
    if body.get("status") == "failed":
        raise RuntimeError(body.get("error", {}).get("message", body))
    return body


def wait_for_navidrome():
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            navidrome_request("ping")
            return
        except Exception as exc:
            print(f"waiting for navidrome: {exc}")
            time.sleep(2)
    raise TimeoutError("Navidrome did not become ready")


def wait_for_songs():
    try:
        navidrome_request("startScan")
    except Exception as exc:
        print(f"startScan failed; continuing to poll search3: {exc}")

    deadline = time.time() + 180
    while time.time() < deadline:
        body = navidrome_request("search3", {"query": "", "songCount": 50, "songOffset": 0})
        songs = body.get("searchResult3", {}).get("song") or []
        if isinstance(songs, dict):
            songs = [songs]
        songs = [
            song for song in songs
            if song.get("albumId") and song.get("album") == "AudioMuse Scan Controller AB"
        ]
        suffixes = {song.get("suffix") for song in songs}
        if len(songs) >= 4 and {"mp3", "flac"}.issubset(suffixes):
            return songs
        print(
            "waiting for Navidrome library scan to expose MP3 and FLAC test songs "
            f"(found={len(songs)}, suffixes={sorted(s for s in suffixes if s)})"
        )
        time.sleep(3)
    raise TimeoutError("Navidrome did not expose the MP3 and FLAC test songs")


def choose_album(songs):
    by_album = {}
    for song in songs:
        by_album.setdefault(song["albumId"], []).append(song)
    album_id, album_songs = max(by_album.items(), key=lambda item: len(item[1]))
    album_songs = sorted(album_songs, key=lambda song: int(song.get("track") or 0))
    album_name = album_songs[0].get("album") or "Unknown Album"
    return album_id, album_name, album_songs


def connect_db(host):
    return psycopg2.connect(
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=host,
        port=POSTGRES_PORT,
        connect_timeout=10,
    )


def wait_for_schema(host):
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            with connect_db(host) as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass('score'), to_regclass('embedding')")
                if all(cur.fetchone()):
                    return
        except Exception as exc:
            print(f"waiting for schema on {host}: {exc}")
        time.sleep(2)
    raise TimeoutError(f"AudioMuse schema not ready on {host}")


def clean_and_seed(host, selected_id, all_album_ids):
    with connect_db(host) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM score WHERE item_id = ANY(%s)", (all_album_ids,))
        cur.execute(
            """
            INSERT INTO score
                (item_id, title, author, album, tempo, key, scale,
                 mood_vector, energy, other_features)
            VALUES
                (%s, 'Allowlisted Complete', 'AB Test Artist', 'AB Test Album',
                 120, 'C', 'major', 'happy:0.9', 0.5, 'danceable:0.8')
            """,
            (selected_id,),
        )
        cur.execute(
            "INSERT INTO embedding (item_id, embedding) VALUES (%s, %s)",
            (selected_id, b"ab-test-embedding"),
        )
        conn.commit()


def score_exists(host, track_id):
    with connect_db(host) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM score WHERE item_id = %s", (track_id,))
        return cur.fetchone() is not None


def redis_conn(url):
    conn = redis.Redis.from_url(url)
    conn.ping()
    return conn


def clear_queue(conn):
    Queue("default", connection=conn).empty()
    Queue("high", connection=conn).empty()


def enqueue_allowlisted_job(redis_url, album_id, album_name, selected_id):
    conn = redis_conn(redis_url)
    clear_queue(conn)
    queue = Queue("default", connection=conn)
    job = queue.enqueue(
        "tasks.analysis.analyze_album_task",
        args=(album_id, album_name, 5, f"ab-parent-{uuid.uuid4()}", [selected_id]),
        job_id=f"ab-{uuid.uuid4()}",
        job_timeout=300,
    )
    return conn, job.id


def wait_for_job(conn, job_id):
    deadline = time.time() + 180
    last_status = None
    while time.time() < deadline:
        job = Job.fetch(job_id, connection=conn)
        status = job.get_status(refresh=True)
        if status != last_status:
            print(f"job {job_id}: {status}")
            last_status = status
        if job.is_finished:
            return "finished", job
        if job.is_failed:
            return "failed", job
        time.sleep(2)
    raise TimeoutError(f"Job {job_id} did not finish")


def run_side(label, pg_host, redis_url, album_id, album_name, selected_id, all_album_ids, expect_success):
    print(f"\n=== {label} ===")
    wait_for_schema(pg_host)
    clean_and_seed(pg_host, selected_id, all_album_ids)
    conn, job_id = enqueue_allowlisted_job(redis_url, album_id, album_name, selected_id)
    status, job = wait_for_job(conn, job_id)

    if expect_success and status != "finished":
        raise AssertionError(f"{label}: expected finished job, got {status}\n{job.exc_info}")
    if not expect_success and status != "failed":
        raise AssertionError(f"{label}: expected failed job, got {status}")

    if expect_success:
        result = job.return_value()
        print(f"{label} result: {result}")
        if not result or result.get("status") != "SUCCESS":
            raise AssertionError(f"{label}: unexpected result {result!r}")
        if result.get("tracks_skipped") != 1:
            raise AssertionError(f"{label}: expected one skipped allowlisted track, got {result!r}")

        for track_id in all_album_ids:
            if track_id != selected_id and score_exists(pg_host, track_id):
                raise AssertionError(f"{label}: non-allowlisted track was written to score: {track_id}")
    else:
        print(f"{label} failed as expected")
        if job.exc_info:
            print(job.exc_info.splitlines()[-1])

    try:
        job.delete()
    except Exception:
        pass


def main():
    wait_for_navidrome()
    songs = wait_for_songs()
    album_id, album_name, album_songs = choose_album(songs)
    selected = album_songs[0]
    selected_id = selected["id"]
    all_album_ids = [song["id"] for song in album_songs]

    print(f"Navidrome ready at {NAVIDROME_URL}")
    print(f"Selected album: {album_name} ({album_id}), tracks={len(album_songs)}")
    print(f"Album formats: {sorted({song.get('suffix') for song in album_songs if song.get('suffix')})}")
    print(f"Allowlisted track: {selected.get('title')} ({selected_id})")

    run_side(
        "baseline",
        os.environ["BASELINE_POSTGRES_HOST"],
        os.environ["BASELINE_REDIS_URL"],
        album_id,
        album_name,
        selected_id,
        all_album_ids,
        expect_success=False,
    )
    run_side(
        "patched",
        os.environ["PATCHED_POSTGRES_HOST"],
        os.environ["PATCHED_REDIS_URL"],
        album_id,
        album_name,
        selected_id,
        all_album_ids,
        expect_success=True,
    )

    print("\nA/B scan-controller deployment test: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"A/B scan-controller deployment test: FAIL: {exc}", file=sys.stderr)
        raise
