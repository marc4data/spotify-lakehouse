"""Export track resolver (R-037, R-039): ids, most-listened first, 404s kept, errors retried."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from spotify_lakehouse import cli, tracks
from spotify_lakehouse.api import SpotifyClient
from spotify_lakehouse.tracks import RankedTrack

# Fake 22-character ids. Their ms_played totals are far above any real track's, so they rank first.
# ID_LOW sorts first by id and has 1 ms: most-listened-first must still pick it last.
ID_OK = "0" * 21 + "1"
ID_MISSING = "0" * 21 + "2"
ID_FLAKY = "0" * 21 + "3"
ID_LOW = "0" * 22
FAKE_MS = {ID_OK: 2_100_000_000, ID_MISSING: 2_090_000_000, ID_FLAKY: 2_080_000_000, ID_LOW: 1}


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


def test_most_listened_first_with_deterministic_ties() -> None:
    """The R-039 ordering guard: a low-ms_played id is never selected ahead of a high one."""
    candidates = [
        RankedTrack("low_but_many_plays", 1_000, 500),
        RankedTrack("aaaa_sorts_first_by_id", 2_000, 1),
        RankedTrack("highest", 9_000_000, 2),
        RankedTrack("tie_b", 5_000, 3),
        RankedTrack("tie_a", 5_000, 3),
        RankedTrack("tie_more_plays", 5_000, 4),
    ]
    assert [t.track_id for t in tracks.order_for_resolution(candidates)] == [
        "highest",
        "tie_more_plays",
        "tie_a",
        "tie_b",
        "aaaa_sorts_first_by_id",
        "low_but_many_plays",
    ]


def test_coverage_table_reports_cumulative_shares_at_checkpoints() -> None:
    pending = [
        RankedTrack("a", 60, 1),
        RankedTrack("b", 30, 2),
        RankedTrack("c", 10, 3),
        RankedTrack("d", 0, 4),
    ]
    rows = tracks.coverage_table(pending, all_track_ms=200, checkpoints=(1, 2, 99))
    assert [(r.calls, round(r.pct_unresolved_ms), round(r.pct_all_track_ms)) for r in rows] == [
        (1, 60, 30),
        (2, 90, 45),
        (4, 100, 50),
    ]
    assert rows[1].pct_unresolved_plays == pytest.approx(30.0)


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


def test_resolve_takes_most_listened_first_remembers_404s_and_retries_errors(
    db, repo: Path
) -> None:
    (repo / "data" / "raw").mkdir(parents=True)
    requested: list[str] = []

    def first_handler(request: httpx.Request) -> httpx.Response:
        track_id = request.url.path.rsplit("/", 1)[-1]
        requested.append(track_id)
        if track_id == ID_OK:
            return httpx.Response(
                200, json={"id": ID_OK, "uri": f"spotify:track:{ID_OK}", "type": "track"}
            )
        if track_id == ID_MISSING:
            return httpx.Response(404, json={"error": {"status": 404, "message": "Not found"}})
        return httpx.Response(500, json={"error": {"status": 500, "message": "boom"}})

    def healthy_handler(request: httpx.Request) -> httpx.Response:
        track_id = request.url.path.rsplit("/", 1)[-1]
        requested.append(track_id)
        return httpx.Response(
            200, json={"id": track_id, "uri": f"spotify:track:{track_id}", "type": "track"}
        )

    lines: list[str] = []
    try:
        with db.transaction():
            for index, (track_id, ms) in enumerate(FAKE_MS.items()):
                db.execute(
                    "insert into raw.export_record "
                    "(profile_slug, source_file, record_index, payload) "
                    "values ('pytest', 'pytest.json', %s, %s::jsonb)",
                    (
                        index,
                        json.dumps(
                            {"spotify_track_uri": f"spotify:track:{track_id}", "ms_played": ms}
                        ),
                    ),
                )
            with _client(first_handler) as api:
                first = tracks.resolve(api, db, "pytest", "pytest-1", limit=3, out=lines.append)
            assert requested == [ID_OK, ID_MISSING, ID_FLAKY]  # by ms_played, not by id
            assert (first.resolved, first.not_found, first.failed) == (1, 1, 1)
            pending = tracks.track_ids_to_fetch(db)
            # the retry ranks first; the 1 ms id is still pending and was never requested
            assert pending[0] == ID_FLAKY and ID_LOW in pending[1:]
            assert ID_OK not in pending and ID_MISSING not in pending  # 200 and 404 are settled
            assert any("coverage" in line for line in lines)  # progress reads as coverage
            requested.clear()
            with _client(healthy_handler) as api:
                second = tracks.resolve(api, db, "pytest", "pytest-2", limit=1, out=lines.append)
            assert requested == [ID_FLAKY]
            assert (second.to_fetch, second.resolved) == (1, 1)
            remaining = set(tracks.track_ids_to_fetch(db)) & {ID_OK, ID_MISSING, ID_FLAKY}
            assert remaining == set()
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
    assert "% of unresolved ms_played" in out
