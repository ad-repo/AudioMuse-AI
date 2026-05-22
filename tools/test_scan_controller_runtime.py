#!/usr/bin/env python3
"""Runtime smoke test for the external scan controller.

This exercises the controller against real AudioMuse dependencies:

- PostgreSQL via app_helper.get_db()
- Redis/RQ via app_helper.rq_queue_default
- The patched analyze_album_task enqueue signature

It uses unique synthetic item IDs and deletes its DB rows and queued jobs at
the end. It does not download audio or run model analysis.
"""

from __future__ import annotations

import uuid
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse

from flask_app import app
from app_helper import get_db, rq_queue_default, redis_conn
from rq.job import Job

from audiomuse_scan_controller import (
    enqueue_statuses,
    load_statuses,
    summarize,
)


def _cleanup(prefix: str, job_ids: list[str] | None = None) -> None:
    with get_db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM score WHERE item_id LIKE %s", (f"{prefix}%",))
        conn.commit()

    for job_id in job_ids or []:
        try:
            rq_queue_default.remove(job_id)
        except Exception:
            pass
        try:
            Job.fetch(job_id, connection=redis_conn).delete()
        except Exception:
            pass


def _seed(prefix: str) -> tuple[list[dict], list[str]]:
    complete = f"{prefix}-complete"
    missing_musicnn = f"{prefix}-missing-musicnn"
    missing_clap = f"{prefix}-missing-clap"
    no_album = f"{prefix}-missing-no-album"

    with get_db() as conn, conn.cursor() as cur:
        for item_id, title in (
            (complete, "Complete Track"),
            (missing_clap, "Missing CLAP"),
            (no_album, "Missing No Album"),
        ):
            cur.execute(
                """
                INSERT INTO score
                    (item_id, title, author, album, tempo, key, scale,
                     mood_vector, energy, other_features)
                VALUES
                    (%s, %s, 'Runtime Artist', 'Runtime Album', 120, 'C',
                     'major', 'happy:0.9', 0.5, 'danceable:0.8')
                """,
                (item_id, title),
            )
            cur.execute(
                "INSERT INTO embedding (item_id, embedding) VALUES (%s, %s)",
                (item_id, b"test-embedding"),
            )

        cur.execute(
            "INSERT INTO clap_embedding (item_id, embedding) VALUES (%s, %s)",
            (complete, b"test-clap"),
        )
        conn.commit()

    songs = [
        {"id": complete, "albumId": f"{prefix}-album-a", "title": "Complete Track", "album": "Runtime Album A", "artist": "Runtime Artist", "artistId": "runtime-artist"},
        {"id": missing_musicnn, "albumId": f"{prefix}-album-a", "title": "Missing MusicNN", "album": "Runtime Album A", "artist": "Runtime Artist", "artistId": "runtime-artist"},
        {"id": missing_clap, "albumId": f"{prefix}-album-b", "title": "Missing CLAP", "album": "Runtime Album B", "artist": "Runtime Artist", "artistId": "runtime-artist"},
        {"id": no_album, "title": "Missing No Album", "album": "Runtime Album C", "artist": "Runtime Artist", "artistId": "runtime-artist"},
    ]
    return songs, [complete, missing_musicnn, missing_clap, no_album]


def _assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _start_fake_navidrome(songs: list[dict]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path != "/rest/search3.view":
                self.send_response(404)
                self.end_headers()
                return
            payload = {
                "subsonic-response": {
                    "status": "ok",
                    "searchResult3": {
                        "song": songs,
                    },
                }
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main() -> None:
    prefix = f"scan-controller-runtime-{uuid.uuid4()}"
    job_ids: list[str] = []
    navidrome = None

    with app.app_context():
        try:
            _cleanup(prefix)
            songs, track_ids = _seed(prefix)
            navidrome = _start_fake_navidrome(songs)
            fake_url = f"http://127.0.0.1:{navidrome.server_port}"

            import config
            config.MEDIASERVER_TYPE = "navidrome"
            config.NAVIDROME_URL = fake_url
            config.NAVIDROME_USER = "runtime-test"
            config.NAVIDROME_PASSWORD = "runtime-test"

            statuses = load_statuses(include_clap=True, include_lyrics=False)
            statuses = [
                status for status in statuses
                if status.track_id in set(track_ids)
            ]
            summary = summarize(statuses)
            _assert_equal(summary["tracks_checked"], 4, "tracks_checked")
            _assert_equal(summary["complete"], 1, "complete")
            _assert_equal(summary["missing_any"], 3, "missing_any")
            _assert_equal(summary["missing_musicnn"], 1, "missing_musicnn")
            _assert_equal(summary["missing_clap"], 3, "missing_clap")
            _assert_equal(summary["affected_albums"], 2, "affected_albums")
            _assert_equal(summary["missing_without_album_id"], 1, "missing_without_album_id")

            dry_run = enqueue_statuses(
                statuses,
                top_n_moods=5,
                batch_size=10,
                limit=None,
                from_csv=None,
                dry_run=True,
            )
            _assert_equal(dry_run["selected_tracks"], 2, "dry_run selected_tracks")
            _assert_equal(dry_run["batches"], 2, "dry_run batches")
            _assert_equal(dry_run["enqueued_jobs"], 0, "dry_run enqueued_jobs")

            enqueued = enqueue_statuses(
                statuses,
                top_n_moods=5,
                batch_size=10,
                limit=None,
                from_csv=None,
                dry_run=False,
            )
            job_ids = list(enqueued["job_ids"])
            _assert_equal(enqueued["selected_tracks"], 2, "enqueue selected_tracks")
            _assert_equal(enqueued["enqueued_jobs"], 2, "enqueue enqueued_jobs")

            fetched = [Job.fetch(job_id, connection=redis_conn) for job_id in job_ids]
            allowlists = [job.args[4] for job in fetched]
            _assert_equal(allowlists[0], [f"{prefix}-missing-musicnn"], "first allowlist")
            _assert_equal(allowlists[1], [f"{prefix}-missing-clap"], "second allowlist")

            print("scan controller runtime smoke: PASS")
        finally:
            try:
                if navidrome:
                    navidrome.shutdown()
            except Exception:
                pass
            _cleanup(prefix, job_ids)


if __name__ == "__main__":
    main()
