"""Resolve export track URIs with GET /tracks/{id} (spot-main-R-037).

The Extended export names an artist but carries no artist id, so its plays reach no primary artist
until each track is looked up. Batch `GET /tracks?ids=` returns 403 to this development-mode app
(measured 2026-09-14 with httpx and curl: 5 ids, 1 id, and with market=from_token), so this is one
call per track, paced by the client. At ~47,000 ids that is ~13 hours: `spot resolve-tracks` is a
dry run unless given --run, because that spend is Marc's to authorise.

Resumable: ids already in raw.api_response (a recently-played item or a stored `track` response)
and ids recorded as not found in spot_meta.track_lookup are subtracted, so an interrupted run costs
nothing.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from spotify_lakehouse.api import SpotifyApiError, SpotifyClient
from spotify_lakehouse.raw_store import feed_for_endpoint, insert_response, scrub, write_response

LOCK_NAME = "spot_tracks"
TRACK_URI = re.compile(r"spotify:track:([A-Za-z0-9]{22})")
SECONDS_PER_CALL_ESTIMATE = 1.0  # the client's pacing; network time comes on top
TERMINAL_STATUSES = frozenset({400, 404})
MAX_CONSECUTIVE_FAILURES = 10


def track_id_from_uri(uri: object) -> str | None:
    if not isinstance(uri, str):
        return None
    match = TRACK_URI.fullmatch(uri)
    return match.group(1) if match else None


def export_track_ids(conn: psycopg.Connection) -> set[str]:
    ids: set[str] = set()
    for (uri,) in conn.execute(
        "select distinct payload ->> 'spotify_track_uri' from raw.export_record "
        "where payload ->> 'spotify_track_uri' is not null"
    ):
        track_id = track_id_from_uri(uri)
        if track_id:
            ids.add(track_id)
    return ids


def settled_track_ids(conn: psycopg.Connection) -> set[str]:
    """Ids that need no lookup: already described by Spotify, or known not to exist."""
    settled: set[str] = set()
    for (payload,) in conn.execute(
        "select payload from raw.api_response where feed = 'recently_played'"
    ):
        for item in payload.get("items") or []:
            track_id = ((item or {}).get("track") or {}).get("id")
            if track_id:
                settled.add(track_id)
    for (track_id,) in conn.execute(
        "select distinct payload ->> 'id' from raw.api_response where feed = 'track'"
    ):
        if track_id:
            settled.add(track_id)
    for (track_id,) in conn.execute(
        "select distinct track_id from spot_meta.track_lookup where status in ('ok', 'not_found')"
    ):
        settled.add(track_id)
    return settled


def track_ids_to_fetch(conn: psycopg.Connection) -> list[str]:
    return sorted(export_track_ids(conn) - settled_track_ids(conn))


@dataclass
class TrackSummary:
    to_fetch: int = 0
    resolved: int = 0
    not_found: int = 0
    failed: int = 0
    relinked: int = 0


def _record(
    conn: psycopg.Connection,
    run_id: str,
    track_id: str,
    status: str,
    *,
    http_status: int | None,
    raw_response_id: int | None = None,
    returned_track_id: str | None = None,
    message: str | None = None,
) -> None:
    conn.execute(
        "insert into spot_meta.track_lookup (run_id, track_id, status, http_status, "
        "raw_response_id, returned_track_id, error_message) values (%s, %s, %s, %s, %s, %s, %s)",
        (run_id, track_id, status, http_status, raw_response_id, returned_track_id, message),
    )


def _store(
    conn: psycopg.Connection, profile: str, run_id: str, track_id: str, payload: dict[str, Any]
) -> str | None:
    endpoint = f"/tracks/{track_id}"
    feed = feed_for_endpoint(endpoint)
    clean, _ = scrub(endpoint, payload)
    source_file = write_response(profile, feed, clean, run_id, datetime.now(UTC), key=track_id)
    with conn.transaction():
        raw_id = insert_response(conn, clean, source_file, profile, feed)
        _record(
            conn,
            run_id,
            track_id,
            "ok",
            http_status=200,
            raw_response_id=raw_id,
            returned_track_id=clean.get("id"),
        )
    return clean.get("id")


def resolve(
    api: SpotifyClient,
    conn: psycopg.Connection,
    profile: str,
    run_id: str,
    *,
    limit: int | None = None,
    progress_every: int = 50,
    out: Callable[[str], None] = print,
) -> TrackSummary:
    """Look up every unsettled export track id (at most `limit`). Safe to interrupt and re-run."""
    pending = track_ids_to_fetch(conn)
    if limit is not None:
        pending = pending[:limit]
    summary = TrackSummary(to_fetch=len(pending))
    consecutive_failures = 0
    started = time.monotonic()
    for done, track_id in enumerate(pending, start=1):
        try:
            payload = api.get(f"/tracks/{track_id}")
        except SpotifyApiError as exc:
            if exc.status in (401, 403):
                raise
            if exc.status in TERMINAL_STATUSES:
                _record(
                    conn, run_id, track_id, "not_found", http_status=exc.status, message=str(exc)
                )
                summary.not_found += 1
                consecutive_failures = 0
            else:
                _record(conn, run_id, track_id, "error", http_status=exc.status, message=str(exc))
                summary.failed += 1
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise SpotifyApiError(
                        exc.status,
                        f"{consecutive_failures} consecutive failed lookups; stopping "
                        f"(last: {exc})",
                    ) from exc
        else:
            returned = _store(conn, profile, run_id, track_id, payload)
            summary.resolved += 1
            summary.relinked += returned != track_id
            consecutive_failures = 0
        if done % progress_every == 0 or done == len(pending):
            elapsed = time.monotonic() - started
            remaining = (len(pending) - done) * elapsed / done
            out(
                f"  {done}/{len(pending)}  resolved {summary.resolved}  not found "
                f"{summary.not_found}  failed {summary.failed}  elapsed {elapsed / 60:.1f} min  "
                f"remaining ~{remaining / 3600:.1f} h"
            )
    return summary
