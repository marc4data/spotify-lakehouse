"""MusicBrainz genre resolver: ISRC -> recording -> first artist credit -> artist genres and tags.

spot-main-R-024. Spotify returns no `genres` key to this app (R-004), so genres come from
MusicBrainz, which needs no API key and so puts no credential near a public repo. The join is by
identifier only: an ISRC MusicBrainz does not know stays unresolved and is reported as a coverage
gap. There is no name-search fallback, not even behind a flag.

Resumable: every response, a 404 included, lands in raw.external_response keyed by `request_key`,
and a re-run skips every key already there. No URL a response supplies is ever requested: paths are
built only from ISRCs and MBIDs that pass a strict pattern, and redirects are refused.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

import httpx
import psycopg
import structlog

from spotify_lakehouse.raw_store import insert_external_response, write_external_response

log = structlog.get_logger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
SOURCE = "musicbrainz"
ISRC_FEED = "isrc_lookup"
ARTIST_FEED = "artist"
LOCK_NAME = "spot_musicbrainz"
# MusicBrainz documents 1 request/second for anonymous clients; a little under it, to be safe.
MIN_INTERVAL_SECONDS = 1.1
DEFAULT_RETRY_AFTER_SECONDS = 5
USER_AGENT_PRODUCT = "spotify-lakehouse/0.1"

ISRC_PATTERN = re.compile(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}")
MBID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class MusicBrainzError(RuntimeError):
    pass


def normalize_isrc(value: object) -> str | None:
    """An ISRC in canonical upper case, or None if it is not ISRC-shaped."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace("-", "").upper()
    return candidate if ISRC_PATTERN.fullmatch(candidate) else None


def is_mbid(value: object) -> bool:
    return isinstance(value, str) and MBID_PATTERN.fullmatch(value) is not None


def isrc_path(isrc: str) -> str:
    if not ISRC_PATTERN.fullmatch(isrc):
        raise ValueError(f"{isrc!r} is not a canonical ISRC; refusing to build a request from it")
    return f"/isrc/{isrc}"


def artist_path(mbid: str) -> str:
    if not is_mbid(mbid):
        raise ValueError(f"{mbid!r} is not an MBID; refusing to build a request from it")
    return f"/artist/{mbid}"


def user_agent(contact: str) -> str:
    if not contact.strip():
        raise ValueError("MusicBrainz requires contact information in the User-Agent")
    return f"{USER_AGENT_PRODUCT} ( {contact.strip()} )"


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(1.0, float(resp.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS)))
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


class MusicBrainzClient:
    """Paced client for this project's two lookups. Returns (status, payload); a 404 is data."""

    def __init__(
        self,
        contact: str,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = MIN_INTERVAL_SECONDS,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.Client(
            base_url=MB_BASE,
            timeout=30,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": user_agent(contact), "Accept": "application/json"},
        )
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = self._min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)

    def _get(self, path: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
        attempts = 0
        while True:
            self._pace()
            resp = self._http.get(path, params=params)
            self._last_call = self._clock()
            status = resp.status_code
            if status in (429, 503) and attempts < self._max_retries:
                wait = _retry_after(resp)
                log.warning("musicbrainz_throttled", path=path, status=status, retry_after_s=wait)
                self._sleep(wait)
                attempts += 1
                continue
            if status >= 500 and attempts < self._max_retries:
                self._sleep(float(2**attempts))
                attempts += 1
                continue
            if status == 404:
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                return 404, body if isinstance(body, dict) and body else {"error": "Not Found"}
            if not resp.is_success:
                # Redirects land here too: a Location header is a URL the response chose.
                raise MusicBrainzError(f"GET {path} -> {status}; not following or retrying")
            return status, resp.json()

    def lookup_isrc(self, isrc: str) -> tuple[int, dict[str, Any]]:
        return self._get(isrc_path(isrc), {"fmt": "json", "inc": "artist-credits"})

    def lookup_artist(self, mbid: str) -> tuple[int, dict[str, Any]]:
        return self._get(artist_path(mbid), {"fmt": "json", "inc": "genres tags"})


def isrcs_from_recently_played(payload: dict[str, Any]) -> set[str]:
    isrcs: set[str] = set()
    for item in payload.get("items") or []:
        track = (item or {}).get("track") or {}
        isrc = normalize_isrc((track.get("external_ids") or {}).get("isrc"))
        if isrc:
            isrcs.add(isrc)
    return isrcs


def primary_artist_mbids(payload: dict[str, Any]) -> set[str]:
    """The first-credited artist of every recording an ISRC lookup returned (phase-1 primary)."""
    mbids: set[str] = set()
    for recording in payload.get("recordings") or []:
        credits = (recording or {}).get("artist-credit") or []
        if credits:
            mbid = ((credits[0] or {}).get("artist") or {}).get("id")
            if is_mbid(mbid):
                mbids.add(mbid)
    return mbids


def _fetched_keys(conn: psycopg.Connection, feed: str) -> set[str]:
    return {
        key
        for (key,) in conn.execute(
            "select distinct request_key from raw.external_response "
            "where source = %s and feed = %s",
            (SOURCE, feed),
        )
    }


def isrcs_to_fetch(conn: psycopg.Connection) -> list[str]:
    """ISRCs on tracks in raw recently-played with no MusicBrainz lookup stored yet."""
    referenced: set[str] = set()
    for (payload,) in conn.execute(
        "select payload from raw.api_response where feed = 'recently_played'"
    ):
        referenced |= isrcs_from_recently_played(payload)
    return sorted(referenced - _fetched_keys(conn, ISRC_FEED))


def artist_mbids_to_fetch(conn: psycopg.Connection) -> list[str]:
    """Primary-artist MBIDs named by stored ISRC lookups with no artist lookup stored yet."""
    referenced: set[str] = set()
    for (payload,) in conn.execute(
        "select payload from raw.external_response where source = %s and feed = %s",
        (SOURCE, ISRC_FEED),
    ):
        referenced |= primary_artist_mbids(payload)
    return sorted(referenced - _fetched_keys(conn, ARTIST_FEED))


@dataclass
class FeedSummary:
    to_fetch: int = 0
    found: int = 0
    not_found: int = 0


@dataclass
class ResolveSummary:
    isrc: FeedSummary
    artist: FeedSummary


def _store_all(
    conn: psycopg.Connection,
    keys: Iterable[str],
    feed: str,
    lookup: Callable[[str], tuple[int, dict[str, Any]]],
    run_id: str,
    summary: FeedSummary,
) -> None:
    for key in keys:
        status, payload = lookup(key)
        source_file = write_external_response(
            SOURCE, feed, payload, run_id, datetime.now(UTC), key=key
        )
        insert_external_response(
            conn,
            source=SOURCE,
            feed=feed,
            request_key=key,
            payload=payload,
            source_file=source_file,
        )
        if status == 404:
            summary.not_found += 1
        else:
            summary.found += 1


def resolve(client: MusicBrainzClient, conn: psycopg.Connection, run_id: str) -> ResolveSummary:
    """Fetch every missing ISRC lookup, then every missing primary-artist lookup. Safe to re-run."""
    isrcs = isrcs_to_fetch(conn)
    summary = ResolveSummary(FeedSummary(to_fetch=len(isrcs)), FeedSummary())
    _store_all(conn, isrcs, ISRC_FEED, client.lookup_isrc, run_id, summary.isrc)
    mbids = artist_mbids_to_fetch(conn)  # after the ISRC pass, so it sees what that pass stored
    summary.artist.to_fetch = len(mbids)
    _store_all(conn, mbids, ARTIST_FEED, client.lookup_artist, run_id, summary.artist)
    return summary
