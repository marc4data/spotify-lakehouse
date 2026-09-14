"""C4 from R-018: the `_follow_next` loop through a MockTransport client (no network, no DB)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from spotify_lakehouse import cli
from spotify_lakehouse.api import SpotifyClient
from spotify_lakehouse.paging import UnsafeNextLink

BASE = "https://api.spotify.com/v1/me/player/recently-played"


class _FakeConn:
    """Stands in for psycopg: insert_response calls execute(...).fetchone()."""

    def __init__(self) -> None:
        self.inserts: list[tuple] = []

    def execute(self, sql: str, params: tuple | None = None) -> _FakeConn:
        self.inserts.append(params or ())
        return self

    def fetchone(self) -> tuple[int]:
        return (1000 + len(self.inserts),)


def _page(next_before: str | None, *played_at: str) -> dict:
    return {
        "items": [{"played_at": value} for value in played_at],
        "next": f"{BASE}?before={next_before}&limit=50" if next_before else None,
    }


@pytest.fixture
def raw_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (repo / "data" / "raw").mkdir(parents=True)
    monkeypatch.setattr(cli, "PAGE_PAUSE_SECONDS", 0)
    return repo


def _client(pages_by_before: dict[str, dict], requested: list[str]) -> SpotifyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        before = request.url.params.get("before", "")
        requested.append(before)
        return httpx.Response(200, json=pages_by_before[before])

    return SpotifyClient(
        "marc",
        token_provider=lambda: "token",
        on_unauthorized=lambda: None,
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    )


def test_follows_next_until_it_is_null_and_persists_each_page(raw_repo: Path) -> None:
    requested: list[str] = []
    pages = {
        "3000": _page("2000", "2026-09-14T09:00:00.000Z"),
        "2000": _page(None),
    }
    first = _page("3000", "2026-09-14T10:00:00.000Z")
    conn = _FakeConn()
    with _client(pages, requested) as api:
        lines = cli._follow_next(api, conn, "marc", "run1", first, 5)
    assert requested == ["3000", "2000"]
    assert len(conn.inserts) == 2
    # lines: [0] header, [1] page 1, [2] page 2, [3] page 3, [4] the stop reason
    assert "page 2: items=1" in lines[2]
    assert "page 3: items=0" in lines[3]
    assert lines[-1].strip() == "stopped before page 4: page 3 returned next = null"
    # File names are <UTC timestamp>_<run id>_<page key>.json; compare what follows the timestamp.
    written = sorted(p.name.split("_", 1)[1] for p in (raw_repo / "data" / "raw").rglob("*.json"))
    assert written == ["run1_page2.json", "run1_page3.json"]


def test_stops_at_the_requested_count(raw_repo: Path) -> None:
    requested: list[str] = []
    pages = {"3000": _page("2000", "2026-09-14T09:00:00.000Z")}
    with _client(pages, requested) as api:
        lines = cli._follow_next(api, _FakeConn(), "marc", "run1", _page("3000", "x"), 1)
    assert requested == ["3000"]
    assert not any("stopped before" in line for line in lines)


def test_a_foreign_next_link_is_refused_before_any_request(raw_repo: Path) -> None:
    requested: list[str] = []
    first = {
        "items": [{"played_at": "2026-09-14T10:00:00.000Z"}],
        "next": "https://evil.example/v1/me/player/recently-played?before=1",
    }
    with _client({}, requested) as api, pytest.raises(UnsafeNextLink):
        cli._follow_next(api, _FakeConn(), "marc", "run1", first, 3)
    assert requested == []
