"""`spot refresh`: catch recently-played plays before the 50-item window overflows.

recently-played does not page backwards (R-018), so a play that leaves the window before any
capture sees it is lost to the API. Each poll compares its window with the newest play already
captured for the profile, by any capture. If the window's oldest play is newer than that
high-water mark, plays may have fallen out between captures: the poll records the gap in
spot_meta.overflow_gap and prints it. (spot-main-R-017)
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from spotify_lakehouse import auth
from spotify_lakehouse.api import SpotifyClient
from spotify_lakehouse.paging import RECENTLY_PLAYED_PATH
from spotify_lakehouse.raw_store import feed_for_endpoint, insert_response, scrub, write_response

PAGE_LIMIT = 50
POLL_INTERVAL_SECONDS = 1800
LAUNCHD_LABEL = "local.spot.refresh"
ERROR_MESSAGE_MAX = 500


@dataclass(frozen=True)
class Window:
    items_returned: int
    oldest: datetime | None
    newest: datetime | None


@dataclass(frozen=True)
class Gap:
    start: datetime  # newest play captured before this poll
    end: datetime  # oldest play in this poll's window
    items_returned: int


@dataclass(frozen=True)
class PollOutcome:
    profile: str
    poll_run_id: int
    raw_response_id: int
    window: Window
    previous_high_water_mark: datetime | None
    gap: Gap | None


@dataclass(frozen=True)
class ProfileStatus:
    profile: str
    has_token: bool
    last_poll_at: datetime | None
    last_poll_status: str | None
    last_ok_at: datetime | None
    polls_24h: int
    errors_24h: int
    high_water_mark: datetime | None
    gap_count: int
    last_gap: Gap | None


def window_of(page: dict[str, Any]) -> Window:
    items = page.get("items") or []
    played = [datetime.fromisoformat(item["played_at"]) for item in items if item.get("played_at")]
    return Window(len(items), min(played, default=None), max(played, default=None))


def detect_gap(previous_high_water_mark: datetime | None, window: Window) -> Gap | None:
    """A gap exists when this window's oldest play is newer than the newest play captured before.

    Equal timestamps overlap (the high-water play is still in the window), so no gap. With no
    earlier capture, or an empty window, there is nothing to compare and no gap can be shown.
    """
    if previous_high_water_mark is None or window.oldest is None:
        return None
    if window.oldest > previous_high_water_mark:
        return Gap(previous_high_water_mark, window.oldest, window.items_returned)
    return None


def api_profiles(conn: psycopg.Connection) -> tuple[list[str], list[str]]:
    """(registry profiles with a stored token, registry profiles skipped for lack of one)."""
    registry = [
        row[0]
        for row in conn.execute(
            "select profile_slug from spot_meta.profile_registry order by profile_slug"
        )
    ]
    tokens = set(auth.stored_profiles())
    return [p for p in registry if p in tokens], [p for p in registry if p not in tokens]


def record_api_access(conn: psycopg.Connection, run_id: str) -> tuple[list[str], list[str]]:
    """Record whether each registry profile has a stored token; return (to poll, skipped).

    data-contracts §3's has_api_access needs the token's presence, which lives outside the
    database, so the poller makes it observable (R-033). Only a boolean is written, never the token.
    """
    to_poll, without_token = api_profiles(conn)
    rows = [(run_id, slug, True) for slug in to_poll]
    rows += [(run_id, slug, False) for slug in without_token]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "insert into spot_meta.api_access_observation (run_id, profile_slug, has_stored_token) "
            "values (%s, %s, %s)",
            rows,
        )
    return to_poll, without_token


def high_water_mark(conn: psycopg.Connection, profile: str) -> datetime | None:
    """Newest played_at ever captured for the profile, by any capture (probe or poll)."""
    row = conn.execute(
        "select max((item.value ->> 'played_at')::timestamptz) "
        "from raw.api_response as r "
        "cross join lateral jsonb_array_elements(r.payload -> 'items') as item "
        "where r.feed = 'recently_played' and r.profile_slug = %s",
        (profile,),
    ).fetchone()
    return row[0] if row else None


def record_poll(
    conn: psycopg.Connection,
    *,
    run_id: str,
    profile: str,
    started_at: datetime,
    raw_response_id: int,
    window: Window,
    previous_high_water_mark: datetime | None,
    gap: Gap | None,
) -> int:
    with conn.transaction():
        row = conn.execute(
            "insert into spot_meta.poll_run (run_id, profile_slug, started_at, status, "
            "raw_response_id, items_returned, window_oldest_played_at, window_newest_played_at, "
            "previous_high_water_mark) values (%s, %s, %s, 'ok', %s, %s, %s, %s, %s) returning id",
            (
                run_id,
                profile,
                started_at,
                raw_response_id,
                window.items_returned,
                window.oldest,
                window.newest,
                previous_high_water_mark,
            ),
        ).fetchone()
        assert row is not None
        poll_run_id = int(row[0])
        if gap is not None:
            conn.execute(
                "insert into spot_meta.overflow_gap "
                "(poll_run_id, profile_slug, gap_start, gap_end, items_returned) "
                "values (%s, %s, %s, %s, %s)",
                (poll_run_id, profile, gap.start, gap.end, gap.items_returned),
            )
    return poll_run_id


def record_poll_error(
    conn: psycopg.Connection, *, run_id: str, profile: str, started_at: datetime, message: str
) -> int:
    row = conn.execute(
        "insert into spot_meta.poll_run (run_id, profile_slug, started_at, status, error_message) "
        "values (%s, %s, %s, 'error', %s) returning id",
        (run_id, profile, started_at, message[:ERROR_MESSAGE_MAX]),
    ).fetchone()
    assert row is not None
    return int(row[0])


def poll_profile(
    api: SpotifyClient, conn: psycopg.Connection, profile: str, run_id: str
) -> PollOutcome:
    """One recently-played call for one profile: persist it, compare with the high-water mark."""
    started_at = datetime.now(UTC)
    previous = high_water_mark(conn, profile)  # read before this poll's own row exists
    payload = api.get(RECENTLY_PLAYED_PATH, {"limit": PAGE_LIMIT})
    clean, _ = scrub(RECENTLY_PLAYED_PATH, payload)
    feed = feed_for_endpoint(RECENTLY_PLAYED_PATH)
    source_file = write_response(profile, feed, clean, run_id, datetime.now(UTC))
    window = window_of(clean)
    gap = detect_gap(previous, window)
    with conn.transaction():
        raw_response_id = insert_response(conn, clean, source_file, profile, feed)
        poll_run_id = record_poll(
            conn,
            run_id=run_id,
            profile=profile,
            started_at=started_at,
            raw_response_id=raw_response_id,
            window=window,
            previous_high_water_mark=previous,
            gap=gap,
        )
    return PollOutcome(profile, poll_run_id, raw_response_id, window, previous, gap)


def profile_status(conn: psycopg.Connection, profile: str, now: datetime) -> ProfileStatus:
    last = conn.execute(
        "select finished_at, status from spot_meta.poll_run where profile_slug = %s "
        "order by finished_at desc limit 1",
        (profile,),
    ).fetchone()
    last_ok = conn.execute(
        "select max(finished_at) from spot_meta.poll_run where profile_slug = %s and status = 'ok'",
        (profile,),
    ).fetchone()
    counts = conn.execute(
        "select count(*), count(*) filter (where status = 'error') from spot_meta.poll_run "
        "where profile_slug = %s and finished_at >= %s",
        (profile, now - timedelta(hours=24)),
    ).fetchone()
    gaps = conn.execute(
        "select count(*) from spot_meta.overflow_gap where profile_slug = %s", (profile,)
    ).fetchone()
    last_gap = conn.execute(
        "select gap_start, gap_end, items_returned from spot_meta.overflow_gap "
        "where profile_slug = %s order by detected_at desc limit 1",
        (profile,),
    ).fetchone()
    return ProfileStatus(
        profile=profile,
        has_token=profile in set(auth.stored_profiles()),
        last_poll_at=last[0] if last else None,
        last_poll_status=last[1] if last else None,
        last_ok_at=last_ok[0] if last_ok else None,
        polls_24h=int(counts[0]) if counts else 0,
        errors_24h=int(counts[1]) if counts else 0,
        high_water_mark=high_water_mark(conn, profile),
        gap_count=int(gaps[0]) if gaps else 0,
        last_gap=Gap(*last_gap) if last_gap else None,
    )


def launchd_state(label: str = LAUNCHD_LABEL) -> str:
    """One line on whether the user agent is loaded, from `launchctl print` (macOS only)."""
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown (launchctl unavailable)"
    if result.returncode != 0:
        return "NOT LOADED (install with `make launchd-install` from the main checkout)"
    fields = {}
    for key in ("state", "runs", "last exit code"):
        match = re.search(rf"^\s*{key} = (.+)$", result.stdout, re.MULTILINE)
        fields[key] = match.group(1).strip() if match else "?"
    return (
        f"loaded (state {fields['state']}, runs {fields['runs']}, "
        f"last exit code {fields['last exit code']})"
    )
