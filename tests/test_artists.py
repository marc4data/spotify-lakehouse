from __future__ import annotations

from spotify_lakehouse.artists import artist_ids_from_recently_played


def test_collects_track_and_album_artists_once() -> None:
    payload = {
        "items": [
            {
                "track": {
                    "artists": [{"id": "a1"}, {"id": "a2"}],
                    "album": {"artists": [{"id": "a1"}, {"id": "a3"}]},
                }
            },
            {"track": {"artists": [{"id": "a2"}], "album": {"artists": []}}},
        ]
    }
    assert artist_ids_from_recently_played(payload) == {"a1", "a2", "a3"}


def test_tolerates_missing_and_null_structures() -> None:
    payload = {
        "items": [
            {"track": None},
            {"track": {"artists": None, "album": None}},
            {"track": {"artists": [None, {"name": "no id"}]}},
            {},
        ]
    }
    assert artist_ids_from_recently_played(payload) == set()
    assert artist_ids_from_recently_played({}) == set()
