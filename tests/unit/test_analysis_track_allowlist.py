from unittest.mock import patch

import pytest


pytest.importorskip("rq")


@patch("tasks.analysis._release_freed_ram_to_os")
@patch("tasks.analysis.cleanup_optional_models")
@patch("tasks.analysis.comprehensive_memory_cleanup")
@patch("tasks.analysis.cleanup_musicnn_sessions")
@patch("tasks.analysis._ah.upsert_artist_mappings_for_tracks")
@patch("tasks.analysis._ah.get_missing_ids_in_table", return_value=set())
@patch("tasks.analysis._ah.get_existing_track_ids", return_value={"2"})
@patch("tasks.analysis.get_tracks_from_album")
@patch("tasks.analysis.get_task_info_from_db", return_value=None)
@patch("tasks.analysis.save_task_status")
@patch("tasks.analysis.get_current_job", return_value=None)
@patch("tasks.clap_analyzer.is_clap_available", return_value=False)
def test_analyze_album_task_limits_work_to_only_track_ids(
    mock_clap_available,
    mock_get_current_job,
    mock_save_task_status,
    mock_get_task_info,
    mock_get_tracks_from_album,
    mock_get_existing_track_ids,
    mock_get_missing_ids_in_table,
    mock_upsert_artist_mappings,
    mock_cleanup_musicnn_sessions,
    mock_comprehensive_cleanup,
    mock_cleanup_optional_models,
    mock_release_ram,
):
    from tasks.analysis import analyze_album_task

    mock_get_tracks_from_album.return_value = [
        {"Id": "1", "Name": "Track 1", "AlbumArtist": "Artist", "ArtistId": "artist"},
        {"Id": "2", "Name": "Track 2", "AlbumArtist": "Artist", "ArtistId": "artist"},
    ]

    with patch("tasks.analysis.LYRICS_ENABLED", False), patch("tasks.analysis.download_track") as download_track:
        result = analyze_album_task("album-1", "Album", 5, None, only_track_ids=["2"])

    assert result["status"] == "SUCCESS"
    assert result["tracks_skipped"] == 1
    assert result["tracks_analyzed"] == 0
    mock_get_existing_track_ids.assert_called_once_with(["2"])
    mock_upsert_artist_mappings.assert_called_once()
    assert mock_upsert_artist_mappings.call_args.args[0] == [
        {"Id": "2", "Name": "Track 2", "AlbumArtist": "Artist", "ArtistId": "artist"}
    ]
    download_track.assert_not_called()
