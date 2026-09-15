"""Resolve export track URIs with GET /tracks/{id} (spot-main-R-037), most-listened first (R-039).

The Extended export names an artist but carries no artist id, so its plays reach no primary artist
until each track is looked up. Batch `GET /tracks?ids=` returns 403 to this development-mode app
(measured 2026-09-14 with httpx and curl: 5 ids, 1 id, and with market=from_token), so this is one
call per track, paced by the client. At ~47,000 ids that is ~13 hours: `spot resolve-tracks` is a
dry run unless given --run, because that spend is Marc's to authorise.

Order: every figure this feeds is weighted by ms_played, so ids are looked up in descending order
of total ms_played (then plays, then id, so a resumed run continues rather than reshuffles). There
is no option to turn that off: nothing is gained by resolving the long tail first. Totals are read
from raw.export_record, never from a dbt mart; they are not deduplicated, which changes no ordering
that matters (R-037: 1,217 exact-duplicate rows in 177,747).

Resumable: ids already in raw.api_response (a recently-played item or a stored `track` response)
and ids recorded as not found in spot_meta.track_lookup are subtracted, so an interrupted run costs
nothing.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from spotify_lakehouse.api import SpotifyApiError, SpotifyClient
from spotify_lakehouse.raw_store import feed_for_endpoint, insert_response, scrub, write_response

LOCK_NAME = "spot_tracks"
TRACK_URI = re.compile(r"spotify:track:([A-Za-z0-9]{22})")
# Measured, not configured: R-039's trial ran 1.13 s per lookup (p50 1.12, p90 1.16, max 1.42
# over 74 samples) against the client's 1.0 s pacing, because network time comes on top (R-040 F2).
SECONDS_PER_CALL_ESTIMATE = 1.13
TERMINAL_STATUSES = frozenset({400, 404})
MAX_CONSECUTIVE_FAILURES = 10
COVERAGE_CHECKPOINTS = (100, 500, 1_000, 2_500, 5_000, 10_000, 20_000)


def track_id_from_uri(uri: object) -> str | None:
    if not isinstance(uri, str):
        return None
    match = TRACK_URI.fullmatch(uri)
    return match.group(1) if match else None


@dataclass(frozen=True)
class RankedTrack:
    track_id: str
    ms_played: int
    plays: int


def order_for_resolution(candidates: Iterable[RankedTrack]) -> list[RankedTrack]:
    """Most-listened first: total ms_played desc, then plays desc, then track id (deterministic)."""
    return sorted(candidates, key=lambda t: (-t.ms_played, -t.plays, t.track_id))


def export_track_totals(conn: psycopg.Connection) -> dict[str, RankedTrack]:
    """Total ms_played and play count per export track id, straight from raw.export_record."""
    totals: dict[str, RankedTrack] = {}
    for uri, ms_played, plays in conn.execute(
        "select payload ->> 'spotify_track_uri', "
        "coalesce(sum((payload ->> 'ms_played')::bigint), 0), count(*) "
        "from raw.export_record where payload ->> 'spotify_track_uri' is not null group by 1"
    ):
        track_id = track_id_from_uri(uri)
        if not track_id:
            continue
        seen = totals.get(track_id)
        totals[track_id] = RankedTrack(
            track_id,
            int(ms_played) + (seen.ms_played if seen else 0),
            int(plays) + (seen.plays if seen else 0),
        )
    return totals


def export_track_ids(conn: psycopg.Connection) -> set[str]:
    return set(export_track_totals(conn))


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


def tracks_to_fetch(conn: psycopg.Connection) -> list[RankedTrack]:
    """Unsettled export tracks, most-listened first."""
    settled = settled_track_ids(conn)
    return order_for_resolution(
        t for track_id, t in export_track_totals(conn).items() if track_id not in settled
    )


def track_ids_to_fetch(conn: psycopg.Connection) -> list[str]:
    return [t.track_id for t in tracks_to_fetch(conn)]


@dataclass(frozen=True)
class CoverageRow:
    calls: int
    pct_unresolved_ms: float
    pct_unresolved_plays: float
    pct_all_track_ms: float
    hours: float


def coverage_table(
    pending: Sequence[RankedTrack],
    all_track_ms: int,
    checkpoints: Iterable[int] = COVERAGE_CHECKPOINTS,
) -> list[CoverageRow]:
    """What the first N calls buy, at each checkpoint below len(pending) and at len(pending)."""
    if not pending:
        return []
    points = {c for c in checkpoints if 0 < c < len(pending)} | {len(pending)}
    unresolved_ms = sum(t.ms_played for t in pending) or 1
    unresolved_plays = sum(t.plays for t in pending) or 1
    rows: list[CoverageRow] = []
    cumulative_ms = cumulative_plays = 0
    for calls, track in enumerate(pending, start=1):
        cumulative_ms += track.ms_played
        cumulative_plays += track.plays
        if calls in points:
            rows.append(
                CoverageRow(
                    calls,
                    100 * cumulative_ms / unresolved_ms,
                    100 * cumulative_plays / unresolved_plays,
                    100 * cumulative_ms / (all_track_ms or 1),
                    calls * SECONDS_PER_CALL_ESTIMATE / 3600,
                )
            )
    return rows


@dataclass
class TrackSummary:
    to_fetch: int = 0
    resolved: int = 0
    not_found: int = 0
    failed: int = 0
    relinked: int = 0
    resolved_ms: int = 0
    unresolved_ms_at_start: int = 0


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
    """Look up the most-listened unsettled export tracks (at most `limit`). Safe to re-run."""
    all_pending = tracks_to_fetch(conn)
    pending = all_pending if limit is None else all_pending[:limit]
    summary = TrackSummary(
        to_fetch=len(pending), unresolved_ms_at_start=sum(t.ms_played for t in all_pending)
    )
    consecutive_failures = 0
    started = time.monotonic()
    for done, track in enumerate(pending, start=1):
        track_id = track.track_id
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
            summary.resolved_ms += track.ms_played
            summary.relinked += returned != track_id
            consecutive_failures = 0
        if done % progress_every == 0 or done == len(pending):
            elapsed = time.monotonic() - started
            remaining = (len(pending) - done) * elapsed / done
            coverage = 100 * summary.resolved_ms / (summary.unresolved_ms_at_start or 1)
            out(
                f"  {done}/{len(pending)}  resolved {summary.resolved}  not found "
                f"{summary.not_found}  failed {summary.failed}  coverage {coverage:.1f}% of "
                f"unresolved ms_played  elapsed {elapsed / 60:.1f} min  "
                f"remaining ~{remaining / 3600:.1f} h"
            )
    return summary
