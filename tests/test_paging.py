from __future__ import annotations

import pytest

from spotify_lakehouse.paging import UnsafeNextLink, next_page_params

VALID = "https://api.spotify.com/v1/me/player/recently-played?before=1789430000000&limit=50"


def test_valid_next_link_yields_only_its_params() -> None:
    assert next_page_params(VALID) == {"before": "1789430000000", "limit": "50"}


def test_limit_is_optional() -> None:
    url = "https://api.spotify.com/v1/me/player/recently-played?before=1789430000000"
    assert next_page_params(url) == {"before": "1789430000000"}


@pytest.mark.parametrize(
    "url",
    [
        # a different host must never receive the bearer token
        "https://evil.example/v1/me/player/recently-played?before=1&limit=50",
        "https://api.spotify.com.evil.example/v1/me/player/recently-played?before=1",
        "http://api.spotify.com/v1/me/player/recently-played?before=1",
        "https://api.spotify.com:8443/v1/me/player/recently-played?before=1",
        "https://user:pw@api.spotify.com/v1/me/player/recently-played?before=1",
    ],
)
def test_foreign_or_downgraded_origin_is_refused(url: str) -> None:
    with pytest.raises(UnsafeNextLink):
        next_page_params(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.spotify.com/v1/me/top/artists?before=1",
        "https://api.spotify.com/v1/me/player/recently-played/../../audio-features?before=1",
        "https://api.spotify.com/v1/me/player/recently-played",
        "https://api.spotify.com/v1/me/player/recently-played?after=1",
        "https://api.spotify.com/v1/me/player/recently-played?before=abc",
        "https://api.spotify.com/v1/me/player/recently-played?before=1&market=US",
        "https://api.spotify.com/v1/me/player/recently-played?before=1&before=2",
        "https://api.spotify.com/v1/me/player/recently-played?before=1&limit=x",
    ],
)
def test_wrong_path_or_params_are_refused(url: str) -> None:
    with pytest.raises(UnsafeNextLink):
        next_page_params(url)
