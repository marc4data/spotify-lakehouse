from __future__ import annotations

import httpx
import pytest

from spotify_lakehouse.api import SpotifyApiError, SpotifyClient


def _client(handler, **kwargs) -> tuple[SpotifyClient, list[float], list[str]]:
    sleeps: list[float] = []
    events: list[str] = []
    client = SpotifyClient(
        "marc",
        token_provider=lambda: "token",
        on_unauthorized=lambda: events.append("invalidated"),
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        clock=lambda: 0.0,
        **kwargs,
    )
    return client, sleeps, events


def test_honors_retry_after_on_429() -> None:
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"ok": 1})]
    )
    client, sleeps, _ = _client(lambda r: next(responses), min_interval=0)
    assert client.get("/me") == {"ok": 1}
    assert 7.0 in sleeps


def test_gives_up_after_max_retries() -> None:
    client, sleeps, _ = _client(lambda r: httpx.Response(429), min_interval=0, max_retries=2)
    with pytest.raises(SpotifyApiError) as exc:
        client.get("/me")
    assert exc.value.status == 429
    assert len(sleeps) == 2


def test_refreshes_once_on_401() -> None:
    responses = iter([httpx.Response(401), httpx.Response(200, json={"ok": 1})])
    client, _, events = _client(lambda r: next(responses), min_interval=0)
    assert client.get("/me") == {"ok": 1}
    assert events == ["invalidated"]


def test_second_401_is_an_error() -> None:
    client, _, events = _client(lambda r: httpx.Response(401), min_interval=0)
    with pytest.raises(SpotifyApiError):
        client.get("/me")
    assert events == ["invalidated"]


def test_403_names_the_dev_mode_causes() -> None:
    client, _, _ = _client(
        lambda r: httpx.Response(403, json={"error": {"message": "User not registered"}}),
        min_interval=0,
    )
    with pytest.raises(SpotifyApiError, match="User Management"):
        client.get("/me")


@pytest.mark.parametrize(
    "path",
    [
        "/audio-features",
        "/audio-features/abc",
        "/audio-analysis/abc",
        "/recommendations",
        "/artists/abc/related-artists",
        "/browse/featured-playlists",
        "/browse/categories/pop/playlists",
    ],
)
def test_dead_endpoints_refused_without_http(path: str) -> None:
    client, _, _ = _client(lambda r: pytest.fail("HTTP call made"))
    with pytest.raises(SpotifyApiError, match="closed to this app"):
        client.get(path)


def test_paces_consecutive_calls() -> None:
    client, sleeps, _ = _client(lambda r: httpx.Response(200, json={}), min_interval=1.5)
    client.get("/me")
    client.get("/me")
    assert sleeps == [1.5]
