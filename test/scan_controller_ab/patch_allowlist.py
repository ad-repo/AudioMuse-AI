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

nav_path = Path("/app/tasks/mediaserver_navidrome.py")
nav_text = nav_path.read_text()
old_search_dict = (
    "                    all_songs.append({\n"
    "                        'Id': s.get('id'), \n"
    "                        'Name': title, \n"
    "                        'AlbumArtist': artist_name,\n"
    "                        'ArtistId': artist_id,\n"
    "                        'Path': s.get('path')\n"
    "                    })\n"
)
new_search_dict = (
    "                    all_songs.append({\n"
    "                        'Id': s.get('id'), \n"
    "                        'Name': title, \n"
    "                        'AlbumId': s.get('albumId'),\n"
    "                        'Album': s.get('album'),\n"
    "                        'AlbumArtist': artist_name,\n"
    "                        'ArtistId': artist_id,\n"
    "                        'Path': s.get('path')\n"
    "                    })\n"
)
if old_search_dict in nav_text:
    nav_text = nav_text.replace(old_search_dict, new_search_dict, 1)

old_album_dict = (
    "                all_songs.append({\n"
    "                    'Id': song.get('Id'), \n"
    "                    'Name': song.get('Name'), \n"
    "                    'AlbumArtist': song.get('AlbumArtist'),\n"
    "                    'ArtistId': song.get('ArtistId'),\n"
    "                    'Path': song.get('Path')\n"
    "                })\n"
)
new_album_dict = (
    "                all_songs.append({\n"
    "                    'Id': song.get('Id'), \n"
    "                    'Name': song.get('Name'), \n"
    "                    'AlbumId': song.get('AlbumId') or song.get('albumId') or album_id,\n"
    "                    'Album': song.get('Album') or song.get('album'),\n"
    "                    'AlbumArtist': song.get('AlbumArtist'),\n"
    "                    'ArtistId': song.get('ArtistId'),\n"
    "                    'Path': song.get('Path')\n"
    "                })\n"
)
if old_album_dict in nav_text:
    nav_text = nav_text.replace(old_album_dict, new_album_dict, 1)

nav_path.write_text(nav_text)
