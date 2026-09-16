from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spotify_lakehouse import raw_store
from spotify_lakehouse.config import ConfigError


def test_scrub_removes_email_from_me_without_mutating_input() -> None:
    payload = {"id": "u", "email": "someone@example.org", "country": "US"}
    clean, removed = raw_store.scrub("/me", payload)
    assert "email" not in clean
    assert removed == ["email"]
    assert "email" in payload


def test_scrub_leaves_other_endpoints_alone() -> None:
    payload = {"items": [], "email": "not-a-profile-field"}
    clean, removed = raw_store.scrub("/me/player/recently-played", payload)
    assert clean == payload
    assert removed == []


@pytest.mark.parametrize(
    ("endpoint", "feed"),
    [
        ("/me", "me"),
        ("/me/player/recently-played", "recently_played"),
        ("/artists/0abc123XYZ", "artist"),
        ("/tracks/0abc123XYZ", "track"),
        ("/me/playlists", "playlist"),
        ("/playlists/3cEYpjA9oz9GiPac4AsH4n/items", "playlist_item"),
        ("/playlists/3cEYpjA9oz9GiPac4AsH4n/tracks", "playlist_item"),
    ],
)
def test_feed_for_endpoint(endpoint: str, feed: str) -> None:
    assert raw_store.feed_for_endpoint(endpoint) == feed


@pytest.mark.parametrize(
    "endpoint",
    ["/me/top/artists", "/artists", "/artists/a/b", "/playlists", "/tracks", "/tracks/a/b"],
)
def test_unknown_endpoint_has_no_feed(endpoint: str) -> None:
    with pytest.raises(ValueError, match="No feed is defined"):
        raw_store.feed_for_endpoint(endpoint)


def test_write_response_path_and_permissions(repo: Path) -> None:
    (repo / "data" / "raw").mkdir(parents=True)
    when = datetime(2026, 9, 13, 18, 30, 5, tzinfo=UTC)
    rel = raw_store.write_response("marc", "recently_played", {"a": 1}, "run1", when)
    assert rel == "api/recently_played/marc/20260913T183005Z_run1.json"
    target = repo / "data" / "raw" / rel
    assert json.loads(target.read_text()) == {"a": 1}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_response_key_separates_files_in_one_run(repo: Path) -> None:
    (repo / "data" / "raw").mkdir(parents=True)
    when = datetime(2026, 9, 14, tzinfo=UTC)
    first = raw_store.write_response("marc", "artist", {"id": "a"}, "run1", when, key="a1")
    second = raw_store.write_response("marc", "artist", {"id": "b"}, "run1", when, key="b2")
    assert first.endswith("_run1_a1.json")
    assert first != second


@pytest.mark.parametrize("key", ["../x", "a/b", "", "a b"])
def test_write_response_rejects_unsafe_key(repo: Path, key: str) -> None:
    (repo / "data" / "raw").mkdir(parents=True)
    with pytest.raises(ValueError):
        raw_store.write_response("marc", "artist", {}, "run1", datetime.now(UTC), key=key or "!")


def test_write_response_never_overwrites(repo: Path) -> None:
    (repo / "data" / "raw").mkdir(parents=True)
    when = datetime(2026, 9, 13, tzinfo=UTC)
    raw_store.write_response("marc", "me", {"a": 1}, "run1", when)
    with pytest.raises(FileExistsError):
        raw_store.write_response("marc", "me", {"a": 2}, "run1", when)


def test_missing_data_raw_says_run_bootstrap(repo: Path) -> None:
    with pytest.raises(ConfigError, match="make bootstrap"):
        raw_store.raw_dir()


# --- playlists carry other people's identities (spot-main-R-054) --------------------------------


def _playlist_page() -> dict:
    return {
        "items": [
            {
                "id": "abc",
                "name": "Smith",
                "owner": {
                    "id": "someone",
                    "display_name": "Some One",
                    "uri": "spotify:user:someone",
                    "href": "https://api.spotify.com/v1/users/someone",
                    "external_urls": {"spotify": "https://open.spotify.com/user/someone"},
                    "type": "user",
                },
                "images": [{"url": "https://i.scdn.co/image/photo"}],
                "tracks": {"total": 3},
            }
        ],
        "next": None,
    }


def test_scrub_discards_playlist_owner_identity_and_images() -> None:
    payload = _playlist_page()
    clean, removed = raw_store.scrub("/me/playlists", payload)
    owner = clean["items"][0]["owner"]
    assert owner == {"type": "user"}, owner
    assert "images" not in clean["items"][0]
    assert clean["items"][0]["name"] == "Smith"  # the playlist name is Marc's own word, kept
    assert clean["items"][0]["tracks"] == {"total": 3}
    assert set(removed) == {
        "items[].owner.id",
        "items[].owner.display_name",
        "items[].owner.uri",
        "items[].owner.href",
        "items[].owner.external_urls",
        "items[].images",
    }
    assert payload["items"][0]["owner"]["id"] == "someone"  # input untouched


def test_scrub_discards_added_by_on_every_item() -> None:
    payload = {
        "items": [
            {"added_at": "2024-01-01T00:00:00Z", "added_by": {"id": "a"}, "track": {"uri": "x"}},
            {"added_at": "2024-01-02T00:00:00Z", "added_by": {"id": "b"}, "track": {"uri": "y"}},
        ]
    }
    clean, removed = raw_store.scrub("/playlists/3cEYpjA9oz9GiPac4AsH4n/items", payload)
    assert all("added_by" not in item for item in clean["items"])
    assert [item["added_at"] for item in clean["items"]] == [
        "2024-01-01T00:00:00Z",
        "2024-01-02T00:00:00Z",
    ]
    assert removed == ["items[].added_by"]


def test_scrub_reports_nothing_when_there_was_nothing_to_discard() -> None:
    """The positive control's other half: absence must be distinguishable from 'never looked'."""
    payload = {"items": [{"added_at": "2024-01-01T00:00:00Z", "track": {"uri": "x"}}]}
    clean, removed = raw_store.scrub("/playlists/3cEYpjA9oz9GiPac4AsH4n/items", payload)
    assert clean == payload
    assert removed == []


# --- ownership is a boolean, computed before the id is discarded (spot-main-R-058) --------------


def test_ownership_is_flagged_before_owner_id_is_discarded() -> None:
    """R-058. The bit survives; the identifier does not."""
    payload = {
        "items": [
            {"id": "mine", "name": "Smith", "owner": {"id": "marc-real-id", "type": "user"}},
            {"id": "theirs", "name": "Someone else's", "owner": {"id": "other", "type": "user"}},
        ]
    }
    clean, removed = raw_store.scrub("/me/playlists", payload, owner_id="marc-real-id")
    assert clean["items"][0][raw_store.OWNERSHIP_FLAG] is True
    assert clean["items"][1][raw_store.OWNERSHIP_FLAG] is False
    # the identifier it was computed from is gone from BOTH rows
    for item in clean["items"]:
        assert "id" not in item["owner"], item["owner"]
    assert "items[].owner.id" in removed
    # and nothing anywhere in the scrubbed payload still contains either id
    assert "marc-real-id" not in repr(clean)
    assert "other" not in repr(clean)
    assert payload["items"][0]["owner"]["id"] == "marc-real-id"  # input untouched


def test_ownership_flag_is_absent_when_no_owner_id_is_supplied() -> None:
    """Every other caller passes nothing and must be unaffected."""
    payload = {"items": [{"id": "x", "owner": {"id": "someone", "type": "user"}}]}
    clean, _ = raw_store.scrub("/me/playlists", payload)
    assert raw_store.OWNERSHIP_FLAG not in clean["items"][0]


def test_an_unknown_owner_is_not_owned() -> None:
    """A missing or null owner.id must read as False, never as a match."""
    payload = {"items": [{"id": "x", "owner": {"type": "user"}}, {"id": "y"}]}
    clean, _ = raw_store.scrub("/me/playlists", payload, owner_id="marc-real-id")
    assert clean["items"][0][raw_store.OWNERSHIP_FLAG] is False
    assert clean["items"][1][raw_store.OWNERSHIP_FLAG] is False
