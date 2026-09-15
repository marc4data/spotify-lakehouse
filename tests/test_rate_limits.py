"""The Retry-After cap and the 429 record (spot-main-R-041, R-040 F1 and F2)."""

from __future__ import annotations

import httpx
import pytest

from spotify_lakehouse import artists, cli, rate_limits
from spotify_lakehouse.api import SpotifyClient

FAKE_ARTIST_ID = "0" * 22
HUGE_RETRY_AFTER = "44356"  # the header R-040's extract-artists received


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"/artists/{FAKE_ARTIST_ID}", "/artists/{id}"),
        (f"/tracks/{FAKE_ARTIST_ID}?market=from_token", "/tracks/{id}"),
        ("/me/player/recently-played", "/me/player/recently-played"),
    ],
)
def test_endpoint_template_holds_no_id(path: str, expected: str) -> None:
    assert rate_limits.endpoint_template(path) == expected


def test_extract_artists_stops_on_a_huge_retry_after_and_releases_its_own_lock(
    db, monkeypatch, capsys
) -> None:
    """The R-041 guard, end to end: raise instead of sleep, record the 429, release spot_artists."""
    sleeps: list[float] = []
    lock_states: list[bool] = []

    def artist_lock_is_free() -> bool:
        row = db.execute("select pg_try_advisory_lock(hashtext(%s))", (artists.LOCK_NAME,))
        taken = bool(row.fetchone()[0])
        if taken:
            db.execute("select pg_advisory_unlock(hashtext(%s))", (artists.LOCK_NAME,))
        return taken

    def handler(request: httpx.Request) -> httpx.Response:
        lock_states.append(artist_lock_is_free())  # during the call: must be held by the command
        return httpx.Response(429, headers={"Retry-After": HUGE_RETRY_AFTER})

    def fake_for_profile(profile, settings, **kwargs):
        return SpotifyClient(
            profile,
            token_provider=lambda: "pytest-token",
            on_unauthorized=lambda: None,
            transport=httpx.MockTransport(handler),
            min_interval=0,
            sleep=sleeps.append,
            **kwargs,
        )

    def refuse_refresh_lock(conn):
        raise AssertionError("extract-artists must not take spot_refresh")

    if not artist_lock_is_free():
        pytest.skip(f"the {artists.LOCK_NAME} lock is held by a running extract-artists")
    monkeypatch.setattr(cli.SpotifyClient, "for_profile", fake_for_profile)
    monkeypatch.setattr(cli, "artist_ids_to_fetch", lambda conn, refresh: [FAKE_ARTIST_ID])
    monkeypatch.setattr(cli, "refresh_lock", refuse_refresh_lock)
    try:
        assert cli.main(["extract-artists", "--profile", "pytest"]) == 2
        assert sleeps == []  # not one second slept on a twelve-hour Retry-After
        assert lock_states == [False]  # spot_artists was held while the call was made
        assert artist_lock_is_free()  # and released when the command stopped
        err = capsys.readouterr().err
        assert f"Retry-After {HUGE_RETRY_AFTER} s" in err
        recorded = db.execute(
            "select command, endpoint, retry_after_header, retry_after_s, action "
            "from spot_meta.rate_limit_event where profile_slug = 'pytest'"
        ).fetchall()
        assert recorded == [("extract-artists", "/artists/{id}", HUGE_RETRY_AFTER, 44356, "raised")]
    finally:
        db.execute("delete from spot_meta.rate_limit_event where profile_slug = 'pytest'")
        db.commit()
