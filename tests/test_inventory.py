from __future__ import annotations

import httpx

from spotify_lakehouse import inventory
from spotify_lakehouse.api import SpotifyClient
from spotify_lakehouse.redact import REDACTED


def test_records_from_paths() -> None:
    assert inventory.records_from({"a": 1}, "self") == [{"a": 1}]
    assert inventory.records_from({"items": [{"a": 1}, None, 2]}, "items") == [{"a": 1}]
    assert inventory.records_from({"artists": {"items": [{"b": 2}]}}, "artists.items") == [{"b": 2}]
    assert inventory.records_from(None, "items") == []
    assert inventory.records_from({"items": None}, "items") == []


def test_is_spotify_id() -> None:
    assert inventory.is_spotify_id("079QiYtMEMsGPv0TNAWZPe")
    for bad in ("", "../x", "a/b", "a b", None, 5, "x" * 65):
        assert not inventory.is_spotify_id(bad)


def test_schema_frame_types_presence_null_rate_and_absent_documented_fields() -> None:
    records = [
        {
            "id": "u1",
            "name": "One",
            "popularity": None,
            "images": [],
            "owner": {"display_name": "Someone"},
        },
        {"id": "u2", "name": "Two", "images": [{"url": "x"}], "owner": {"display_name": "Else"}},
    ]
    frame = inventory.schema_frame(records, "artist").set_index("field")
    assert frame.loc["name", "type"] == "str"
    assert frame.loc["popularity", "present_in"] == "1/2"
    assert frame.loc["popularity", "null_rate"] == 1.0
    assert frame.loc["name", "null_rate"] == 0.0
    assert frame.loc["owner.display_name", "type"] == "str"
    assert frame.loc["genres", "type"] == "ABSENT"
    assert frame.loc["owner", "documented"] == "no"


def test_schema_examples_are_redacted_like_samples() -> None:
    records = [
        {
            "id": "user-1",
            "display_name": "Someone",
            "country": "US",
            "owner": {"id": "o1", "type": "user"},
        }
    ]
    frame = inventory.schema_frame(records, "user").set_index("field")
    assert frame.loc["id", "example"] == REDACTED
    assert frame.loc["display_name", "example"] == REDACTED
    assert frame.loc["owner.id", "example"] == REDACTED
    assert frame.loc["country", "example"] == "US"
    assert frame.loc["owner.type", "example"] == "user"


def test_sample_frame_redacts_owner_fields_and_summarises_nested_values() -> None:
    records = [
        {
            "name": "Road trip",
            "owner": {"display_name": "Someone", "id": "owner-1"},
            "images": [{"url": "https://i.scdn.co/x"}],
            "tracks": {"total": 12},
        }
    ]
    frame = inventory.sample_frame(records)
    row = frame.iloc[0]
    assert row["name"] == "Road trip"
    assert row["owner.display_name"] == REDACTED
    assert row["owner.id"] == REDACTED
    assert row["images"] == REDACTED
    assert row["tracks.total"] == 12
    assert "owner" not in frame.columns  # the object itself is not shown, only its fields


def test_missing_documented_separates_conditional_fields() -> None:
    required, conditional = inventory.missing_documented([{"id": "t", "name": "n"}], "track")
    assert "popularity" in required
    assert "linked_from" in conditional and "linked_from" not in required
    assert inventory.undocumented([{"id": "t", "surprise": 1}], "track") == ["surprise"]


def _client(routes: dict[str, httpx.Response], requested: list[str]) -> SpotifyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        requested.append(path)
        return routes.get(path, httpx.Response(404, json={"error": {"message": "not in test"}}))

    return SpotifyClient(
        "marc",
        token_provider=lambda: "token",
        on_unauthorized=lambda: None,
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    )


def test_playlist_items_refusal_falls_back_to_tracks_route_and_records_both() -> None:
    routes = {
        "/me/playlists": httpx.Response(200, json={"items": [{"id": "pl1", "name": "A"}]}),
        "/playlists/pl1/items": httpx.Response(403, json={"error": {"message": "Forbidden"}}),
        "/playlists/pl1/tracks": httpx.Response(
            200, json={"items": [{"added_at": "x", "track": {"id": "t"}}]}
        ),
    }
    requested: list[str] = []
    with _client(routes, requested) as api:
        captures = inventory.collect_live(api, album_id=None, track_id=None)
    capture = captures["3.2"]
    assert [part.ok for part in capture.parts] == [False, True]
    assert capture.parts[0].status.startswith("HTTP 403")
    assert capture.ok and capture.records_total == 1
    assert capture.notes and "was refused" in capture.notes[0]
    assert "/playlists/pl1/tracks" in requested


def test_an_unsafe_id_from_a_response_is_never_placed_in_a_path() -> None:
    routes = {"/me/playlists": httpx.Response(200, json={"items": [{"id": "../../me/player"}]})}
    requested: list[str] = []
    with _client(routes, requested) as api:
        captures = inventory.collect_live(api, album_id="../x", track_id=None)
    assert not captures["3.2"].ok
    assert captures["3.2"].parts[0].status.startswith("not requested")
    assert captures["5.2"].parts[0].status.startswith("not requested")
    assert not any("player" in path or ".." in path for path in requested)


def test_live_errors_are_captured_not_raised_and_counted_in_coverage() -> None:
    requested: list[str] = []
    with _client({}, requested) as api:
        captures = inventory.collect_live(api, album_id="a1", track_id="t1")
    assert not captures["2.1"].ok
    coverage = inventory.coverage_frame(captures).set_index("section")
    assert coverage.loc["2.1", "reachable"].startswith("no: HTTP 404")
    assert coverage.loc["1.1", "source"] == "not collected"
    counts = inventory.coverage_counts(captures)
    assert counts["not sampled"] == len(inventory.SPECS)
    assert counts["live calls"] == len(requested)
