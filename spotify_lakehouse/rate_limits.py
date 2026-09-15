"""Record every Spotify 429 in spot_meta.rate_limit_event (spot-main-R-041, R-040 F1 and F2).

Whether Spotify's long Retry-After is a per-day quota is an inference from one header (R-040 F1).
This module never probes for the limit: it only writes down each 429 that normal operation receives,
so the table fills itself and the question is answered by evidence rather than by spending calls.
"""

from __future__ import annotations

import re

import psycopg

from spotify_lakehouse.api import RateLimitCallback

# Spotify ids are 22 base-62 characters. Stored as a template so the table holds endpoints, not ids.
_ID_SEGMENT = re.compile(r"/[0-9A-Za-z]{22}(?=/|$)")


def endpoint_template(path: str) -> str:
    """`/artists/<22-char id>?x=1` -> `/artists/{id}`."""
    return _ID_SEGMENT.sub("/{id}", path.split("?", 1)[0])


def recorder(
    conn: psycopg.Connection, *, run_id: str, command: str, profile: str
) -> RateLimitCallback:
    """A client callback that inserts one row per 429. `conn` must be in autocommit mode."""

    def record(path: str, header: str | None, retry_after_s: float, action: str) -> None:
        conn.execute(
            "insert into spot_meta.rate_limit_event (run_id, command, profile_slug, endpoint, "
            "retry_after_header, retry_after_s, action) values (%s, %s, %s, %s, %s, %s, %s)",
            (run_id, command, profile, endpoint_template(path), header, retry_after_s, action),
        )

    return record
