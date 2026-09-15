"""MusicBrainz resolver (spot-main-R-024): identifier-only paths, pacing, 404s, resumability."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from spotify_lakehouse import config, musicbrainz

MBID_A = "09887aa7-226e-4ecc-9a0c-02d2ae5777e1"
MBID_B = "b8a7c51f-362c-4dcb-a259-bc6e0095f0a6"


class _Recorder:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def _client(
    recorder: _Recorder, sleeps: list[float] | None = None
) -> musicbrainz.MusicBrainzClient:
    return musicbrainz.MusicBrainzClient(
        "contact.example",
        transport=httpx.MockTransport(recorder),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        min_interval=0,
    )


def test_user_agent_carries_the_configured_contact() -> None:
    recorder = _Recorder([httpx.Response(200, json={"recordings": []})])
    with _client(recorder) as client:
        client.lookup_isrc("USUM71703861")
    request = recorder.requests[0]
    assert request.headers["User-Agent"] == "spotify-lakehouse/0.1 ( contact.example )"
    assert request.url.host == "musicbrainz.org"
    assert request.url.path == "/ws/2/isrc/USUM71703861"
    assert request.url.params["inc"] == "artist-credits"


def test_an_empty_contact_is_refused() -> None:
    with pytest.raises(ValueError, match="contact"):
        musicbrainz.MusicBrainzClient("  ")


@pytest.mark.parametrize(
    "bad", ["", "usum7170386", "../artist/x", "USUM71703861/../../x", "USUM7170386?inc=x"]
)
def test_malformed_isrc_never_reaches_http(bad: str) -> None:
    recorder = _Recorder([])
    with _client(recorder) as client, pytest.raises(ValueError, match="canonical ISRC"):
        client.lookup_isrc(bad)
    assert recorder.requests == []


@pytest.mark.parametrize("bad", ["", "not-a-uuid", f"{MBID_A}/../x", MBID_A.upper()])
def test_malformed_mbid_never_reaches_http(bad: str) -> None:
    recorder = _Recorder([])
    with _client(recorder) as client, pytest.raises(ValueError, match="not an MBID"):
        client.lookup_artist(bad)
    assert recorder.requests == []


def test_503_honours_retry_after_then_succeeds() -> None:
    sleeps: list[float] = []
    recorder = _Recorder(
        [
            httpx.Response(503, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"id": MBID_A, "genres": [], "tags": []}),
        ]
    )
    with _client(recorder, sleeps) as client:
        status, payload = client.lookup_artist(MBID_A)
    assert (status, payload["id"]) == (200, MBID_A)
    assert sleeps == [7.0]


def test_404_is_data_not_an_error() -> None:
    recorder = _Recorder([httpx.Response(404, json={"error": "Not Found", "help": "..."})])
    with _client(recorder) as client:
        status, payload = client.lookup_isrc("USUM71703861")
    assert status == 404
    assert payload["error"] == "Not Found"


def test_a_redirect_is_never_followed() -> None:
    recorder = _Recorder([httpx.Response(301, headers={"Location": "https://evil.example/ws/2"})])
    with _client(recorder) as client, pytest.raises(musicbrainz.MusicBrainzError, match="301"):
        client.lookup_isrc("USUM71703861")
    assert len(recorder.requests) == 1


def test_client_paces_between_calls() -> None:
    sleeps: list[float] = []
    now = [100.0]
    recorder = _Recorder([httpx.Response(200, json={}), httpx.Response(200, json={})])
    client = musicbrainz.MusicBrainzClient(
        "contact.example",
        transport=httpx.MockTransport(recorder),
        sleep=sleeps.append,
        clock=lambda: now[0],
        min_interval=1.1,
    )
    with client:
        client.lookup_isrc("USUM71703861")
        now[0] += 0.3
        client.lookup_isrc("USUM71703862")
    assert sleeps == [pytest.approx(0.8)]


def test_isrcs_from_recently_played_normalizes_and_drops_junk() -> None:
    payload: dict[str, Any] = {
        "items": [
            {"track": {"external_ids": {"isrc": "usum71703861"}}},
            {"track": {"external_ids": {"isrc": "US-UM7-17-03862"}}},
            {"track": {"external_ids": {"isrc": "not an isrc"}}},
            {"track": {"external_ids": None}},
            {"track": None},
            None,
        ]
    }
    assert musicbrainz.isrcs_from_recently_played(payload) == {"USUM71703861", "USUM71703862"}


def test_primary_artist_is_the_first_credit_of_each_recording() -> None:
    payload = {
        "recordings": [
            {"artist-credit": [{"artist": {"id": MBID_A}}, {"artist": {"id": MBID_B}}]},
            {"artist-credit": [{"artist": {"id": "https://evil.example/"}}]},
            {"artist-credit": []},
            {},
        ]
    }
    assert musicbrainz.primary_artist_mbids(payload) == {MBID_A}


def test_contact_is_configuration_and_must_be_set(config_home: Path) -> None:
    base = "SPOT_PG_USER=spot\nSPOT_PG_PASSWORD=pw\n"
    (config_home / ".env").write_text(base + "MUSICBRAINZ_CONTACT=replace-me\n")
    with pytest.raises(config.ConfigError, match="MUSICBRAINZ_CONTACT"):
        config.musicbrainz_contact(config.load_settings(require_spotify=False))
    (config_home / ".env").write_text(base + "MUSICBRAINZ_CONTACT=contact-value\n")
    settings = config.load_settings(require_spotify=False)
    assert config.musicbrainz_contact(settings) == "contact-value"
    assert "contact-value" not in repr(settings)


# --- Integration: shared Postgres. Everything is written inside a rolled-back transaction. ---


class _Rollback(Exception):
    pass


def test_resolve_twice_second_run_fetches_nothing(db, repo: Path) -> None:
    from psycopg.types.json import Jsonb

    (repo / "data" / "raw").mkdir(parents=True)
    isrc = "ZZPYT2400001"  # "ZZ" is not an assigned country code: never a real recording
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.startswith("/ws/2/isrc/"):
            if request.url.path.endswith(isrc):
                return httpx.Response(
                    200,
                    json={
                        "isrc": isrc,
                        "recordings": [{"artist-credit": [{"artist": {"id": MBID_A}}]}],
                    },
                )
            return httpx.Response(404, json={"error": "Not Found"})
        return httpx.Response(200, json={"id": MBID_A, "genres": [{"name": "pop", "count": 1}]})

    def run() -> musicbrainz.ResolveSummary:
        client = musicbrainz.MusicBrainzClient(
            "contact.example", transport=httpx.MockTransport(handler), min_interval=0
        )
        with client:
            return musicbrainz.resolve(client, db, "pytest")

    try:
        with db.transaction():
            db.execute(
                "insert into raw.api_response (feed, payload, source_file, profile_slug) "
                "values ('recently_played', %s, 'pytest', 'pytest')",
                (Jsonb({"items": [{"track": {"external_ids": {"isrc": isrc}}}]}),),
            )
            first = run()
            assert isrc not in musicbrainz.isrcs_to_fetch(db)
            assert first.isrc.to_fetch >= 1
            calls.clear()
            second = run()
            assert (second.isrc.to_fetch, second.artist.to_fetch) == (0, 0)
            assert calls == []
            raise _Rollback
    except _Rollback:
        pass
