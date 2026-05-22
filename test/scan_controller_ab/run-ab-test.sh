#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
MUSIC_DIR="$SCRIPT_DIR/music"

mkdir -p "$MUSIC_DIR"

if [ ! -f "$MUSIC_DIR/01-allowlisted-complete.mp3" ] || \
   [ ! -f "$MUSIC_DIR/02-nonallowlisted-missing.mp3" ] || \
   [ ! -f "$MUSIC_DIR/03-nonallowlisted-flac.flac" ] || \
   [ ! -f "$MUSIC_DIR/04-nonallowlisted-flac-2.flac" ]; then
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg is required to generate the Navidrome test album." >&2
    exit 1
  fi

  ffmpeg -y \
    -i "$ROOT_DIR/test/songs/Aaron Dunn - Minuet - Notebook for Anna Magdalena.mp3" \
    -codec copy \
    -metadata album="AudioMuse Scan Controller AB" \
    -metadata artist="AudioMuse AB Artist" \
    -metadata album_artist="AudioMuse AB Artist" \
    -metadata title="Allowlisted Complete" \
    -metadata track="1" \
    "$MUSIC_DIR/01-allowlisted-complete.mp3" >/dev/null 2>&1

  ffmpeg -y \
    -i "$ROOT_DIR/test/songs/Art Flower - Art Flower - Creamy Snowflakes.mp3" \
    -codec copy \
    -metadata album="AudioMuse Scan Controller AB" \
    -metadata artist="AudioMuse AB Artist" \
    -metadata album_artist="AudioMuse AB Artist" \
    -metadata title="Non Allowlisted Missing" \
    -metadata track="2" \
    "$MUSIC_DIR/02-nonallowlisted-missing.mp3" >/dev/null 2>&1

  ffmpeg -y \
    -i "$ROOT_DIR/test/songs/Michael Hawley - Sonata 'Waldstein', Op. 53 - II. Introduzione-Adagio molto.mp3" \
    -codec:a flac \
    -metadata album="AudioMuse Scan Controller AB" \
    -metadata artist="AudioMuse AB Artist" \
    -metadata album_artist="AudioMuse AB Artist" \
    -metadata title="Non Allowlisted FLAC" \
    -metadata track="3" \
    "$MUSIC_DIR/03-nonallowlisted-flac.flac" >/dev/null 2>&1

  ffmpeg -y \
    -i "$ROOT_DIR/test/songs/Aaron Dunn - Minuet - Notebook for Anna Magdalena.mp3" \
    -codec:a flac \
    -metadata album="AudioMuse Scan Controller AB" \
    -metadata artist="AudioMuse AB Artist" \
    -metadata album_artist="AudioMuse AB Artist" \
    -metadata title="Non Allowlisted FLAC 2" \
    -metadata track="4" \
    "$MUSIC_DIR/04-nonallowlisted-flac-2.flac" >/dev/null 2>&1
fi

cd "$SCRIPT_DIR"
docker compose -f docker-compose.yaml down -v --remove-orphans >/dev/null 2>&1 || true

docker compose -f docker-compose.yaml up -d --build \
  navidrome \
  redis-baseline postgres-baseline flask-baseline worker-baseline \
  redis-patched postgres-patched flask-patched worker-patched

docker rm -f am-scan-ab-runner >/dev/null 2>&1 || true
set +e
docker compose -f docker-compose.yaml run \
  --name am-scan-ab-runner \
  --entrypoint python3 \
  ab-runner \
  /app/test/scan_controller_ab/ab_test.py
RUN_EXIT=$?
set -e

docker logs am-scan-ab-runner
docker rm -f am-scan-ab-runner >/dev/null 2>&1 || true
exit "$RUN_EXIT"
