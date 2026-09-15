"""Export track resolver (spot-main-R-037): ids, 404s kept, errors retried, dry run by default."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from spotify_lakehouse import cli, tracks
from spotify_lakehouse.api import SpotifyClient

# 22-character ids of leading zeros sort before every real base62 track id, so `limit` picks them.
ID_OK = "0" * 21 + "1"
ID_MISSING = "0" * 21 + "2"
ID_FLAKY = "0" * 21 + "3"


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        (f"spotify:track:{ID_OK}", ID_OK),
        ("spotify:episode:" + "a" * 22, None),
        ("spotify:track:short", None),
        ("spotify:track:" + "a" * 22 + "/../x", None),
        (None, None),
    ],
)
def test_track_id_from_uri(uri: object, expected: str | None) -> None:
    assert tracks.track_id_from_uri(uri) == expected


class _Rollback(Exception):
    pass


def _client(handler) -> SpotifyClient:
    return SpotifyClient(
        "pytest",
        token_provider=lambda: "pytest-token",
        on_unauthorized=lambda: None,
        transport=httpx.MockTransport(handler),
        min_interval=0,
        max_retries=0,
        sleep=lambda _: None,
    )


def test_resolve_remembers_404_retries_errors_and_second_run_resolves_zero(db, repo: Path) -> None:
    (repo / "data" / "raw").mkdir(parents=True)

    def first_handler(request: httpx.Request) -> httpx.Response:
        track_id = request.url.path.rsplit("/", 1)[-1]
        if track_id == ID_OK:
            return httpx.Response(
                200, json={"id": ID_OK, "uri": f"spotify:track:{ID_OK}", "type": "track"}
            )
        if track_id == ID_MISSING:
            return httpx.Response(404, json={"error": {"status": 404, "message": "Not found"}})
        return httpx.Response(500, json={"error": {"status": 500, "message": "boom"}})

    def healthy_handler(request: httpx.Request) -> httpx.Response:
        track_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200, json={"id": track_id, "uri": f"spotify:track:{track_id}", "type": "track"}
        )

    lines: list[str] = []
    try:
        with db.transaction():
            for index, track_id in enumerate((ID_OK, ID_MISSING, ID_FLAKY)):
                db.execute(
                    "insert into raw.export_record "
                    "(profile_slug, source_file, record_index, payload) "
                    "values ('pytest', 'pytest.json', %s, %s::jsonb)",
                    (index, json.dumps({"spotify_track_uri": f"spotify:track:{track_id}"})),
                )
            with _client(first_handler) as api:
                first = tracks.resolve(api, db, "pytest", "pytest-1", limit=3, out=lines.append)
            assert (first.resolved, first.not_found, first.failed) == (1, 1, 1)
            pending = tracks.track_ids_to_fetch(db)
            assert ID_FLAKY in pending  # an error is retried
            assert ID_OK not in pending and ID_MISSING not in pending  # 200 and 404 are settled
            with _client(healthy_handler) as api:
                second = tracks.resolve(api, db, "pytest", "pytest-2", limit=1, out=lines.append)
            assert (second.to_fetch, second.resolved) == (1, 1)
            remaining = set(tracks.track_ids_to_fetch(db)) & {ID_OK, ID_MISSING, ID_FLAKY}
            assert remaining == set()
            assert any("resolved 1" in line for line in lines)  # progress is legible
            raise _Rollback
    except _Rollback:
        pass


def test_resolve_tracks_is_a_dry_run_without_run(db, monkeypatch, capsys) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("a dry run must not construct an API client")

    monkeypatch.setattr(cli.SpotifyClient, "for_profile", refuse)
    assert cli.main(["resolve-tracks", "--profile", "marc"]) == 0
    out = capsys.readouterr().out
    assert "dry run: no API call made" in out
