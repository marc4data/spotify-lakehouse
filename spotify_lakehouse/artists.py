"""Artist extraction: GET /artists/{id} for every artist credited in recently-played.

Batch `GET /artists?ids=` returns 403 to this development-mode app (measured 2026-09-14), so this
is one call per artist, paced by the client. The response carries no `genres` key (measured 45/45);
the feed is kept for names and ids, and so a genre field that reappears is captured unchanged.
(spot-main-R-004)
"""

from __future__ import annotations

from typing import Any

import psycopg

# Its own lock, not spot_refresh (spot-main-R-041, R-040 F2). This command calls Spotify but it is
# not the refresh: sharing the poller's lock is what turned R-040's twelve-hour Retry-After into a
# data-loss risk.
LOCK_NAME = "spot_artists"


def artist_ids_from_track(track: dict[str, Any]) -> set[str]:
    """Every artist id credited on one track object or its album."""
    album = track.get("album") or {}
    return {
        artist["id"]
        for artist in (track.get("artists") or []) + (album.get("artists") or [])
        if artist and artist.get("id")
    }


def artist_ids_from_recently_played(payload: dict[str, Any]) -> set[str]:
    """Every artist id credited on a track or its album in one recently-played response."""
    ids: set[str] = set()
    for item in payload.get("items") or []:
        ids |= artist_ids_from_track((item or {}).get("track") or {})
    return ids


def artist_ids_to_fetch(conn: psycopg.Connection, *, refresh: bool = False) -> list[str]:
    """Artist ids referenced in raw with no `artist` observation yet (every one, if refresh).

    Referenced = credited in a recently-played item or in a resolved export track (R-037).
    """
    referenced: set[str] = set()
    for feed, payload in conn.execute(
        "select feed, payload from raw.api_response where feed in ('recently_played', 'track')"
    ):
        if feed == "track":
            referenced |= artist_ids_from_track(payload)
        else:
            referenced |= artist_ids_from_recently_played(payload)
    if refresh:
        return sorted(referenced)
    fetched = {
        artist_id
        for (artist_id,) in conn.execute(
            "select payload ->> 'id' from raw.api_response where feed = 'artist'"
        )
    }
    return sorted(referenced - fetched)
