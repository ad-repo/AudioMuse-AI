from pathlib import Path

path = Path("/app/tasks/analysis.py")
text = path.read_text()

old_signature = "def analyze_album_task(album_id, album_name, top_n_moods, parent_task_id):"
new_signature = "def analyze_album_task(album_id, album_name, top_n_moods, parent_task_id, only_track_ids=None):"
if old_signature not in text:
    raise SystemExit("Could not find analyze_album_task signature to patch")
text = text.replace(old_signature, new_signature, 1)

needle = (
    "            tracks = get_tracks_from_album(album_id)\n"
    "            if not tracks:\n"
)
insert = (
    "            tracks = get_tracks_from_album(album_id)\n"
    "            if only_track_ids is not None:\n"
    "                allowed_track_ids = {str(track_id) for track_id in only_track_ids}\n"
    "                original_track_count = len(tracks or [])\n"
    "                tracks = [\n"
    "                    track for track in (tracks or [])\n"
    "                    if str(track.get('Id')) in allowed_track_ids\n"
    "                ]\n"
    "                logger.info(\n"
    "                    f\"Track allowlist active for album '{album_name}': \"\n"
    "                    f\"{len(tracks)}/{original_track_count} tracks selected.\"\n"
    "                )\n"
    "            if not tracks:\n"
)
if needle not in text:
    raise SystemExit("Could not find track fetch block to patch")
text = text.replace(needle, insert, 1)

path.write_text(text)
