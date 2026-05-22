import sys
import types
from unittest.mock import Mock, patch

from tools import audiomuse_scan_controller as controller


def test_summarize_counts_missing_features_and_albums():
    statuses = [
        controller.TrackStatus("1", "album-1", "A", "Artist", "Album", False, False, False),
        controller.TrackStatus("2", "album-1", "B", "Artist", "Album", True, False, False),
        controller.TrackStatus("3", "album-2", "C", "Artist", "Album 2", False, True, False),
        controller.TrackStatus("4", None, "D", "Artist", "Album 3", False, False, True),
    ]

    assert controller.summarize(statuses) == {
        "tracks_checked": 4,
        "complete": 1,
        "missing_any": 3,
        "missing_musicnn": 1,
        "missing_clap": 1,
        "missing_lyrics": 1,
        "affected_albums": 2,
        "missing_without_album_id": 1,
    }


def test_enqueue_statuses_uses_track_allowlist_args_in_album_jobs():
    statuses = [
        controller.TrackStatus("1", "album-1", "A", "Artist", "Album", True, False, False),
        controller.TrackStatus("2", "album-1", "B", "Artist", "Album", True, False, False),
        controller.TrackStatus("3", "album-2", "C", "Artist", "Album 2", False, True, False),
        controller.TrackStatus("4", None, "D", "Artist", "Album 3", True, False, False),
    ]

    enqueue = Mock(side_effect=[
        type("Job", (), {"id": "job-1"})(),
        type("Job", (), {"id": "job-2"})(),
    ])
    app_helper = types.SimpleNamespace(rq_queue_default=types.SimpleNamespace(enqueue=enqueue))
    rq = types.SimpleNamespace(Retry=lambda max: ("retry", max))

    with patch.dict(sys.modules, {"app_helper": app_helper, "rq": rq}):
        result = controller.enqueue_statuses(
            statuses,
            top_n_moods=5,
            batch_size=10,
            limit=None,
            from_csv=None,
            dry_run=False,
        )

    assert result["selected_tracks"] == 3
    assert result["affected_albums"] == 2
    assert result["enqueued_jobs"] == 2

    first_call = enqueue.call_args_list[0].kwargs
    assert first_call["args"][0] == "album-1"
    assert first_call["args"][1] == "Album"
    assert first_call["args"][2] == 5
    assert first_call["args"][4] == ["1", "2"]

    second_call = enqueue.call_args_list[1].kwargs
    assert second_call["args"][0] == "album-2"
    assert second_call["args"][4] == ["3"]
