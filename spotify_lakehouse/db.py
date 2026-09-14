"""The only way notebooks and scripts reach Postgres. Never hardcode a connection string."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg

from spotify_lakehouse.config import Settings, load_settings, session

REFRESH_LOCK_NAME = "spot_refresh"


def connect(settings: Settings | None = None, **kwargs: Any) -> psycopg.Connection:
    s = settings or load_settings(require_spotify=False)
    return psycopg.connect(
        host=s.pg_host,
        port=s.pg_port,
        dbname=s.pg_database,
        user=s.pg_user,
        password=s.pg_password,
        application_name=f"spot-{session()}",
        **kwargs,
    )


@contextmanager
def refresh_lock(conn: psycopg.Connection) -> Iterator[None]:
    """Hold pg_advisory_lock(hashtext('spot_refresh')) so two sessions never poll Spotify at once.

    `conn` must be in autocommit mode (session-level lock).
    """
    acquired = conn.execute(
        "select pg_try_advisory_lock(hashtext(%s))", (REFRESH_LOCK_NAME,)
    ).fetchone()
    if not (acquired and acquired[0]):
        print(
            "Another session holds the spot_refresh lock (a refresh or probe is running). "
            "Waiting for it to finish...",
            file=sys.stderr,
        )
        conn.execute("select pg_advisory_lock(hashtext(%s))", (REFRESH_LOCK_NAME,))
    try:
        yield
    finally:
        conn.execute("select pg_advisory_unlock(hashtext(%s))", (REFRESH_LOCK_NAME,))


@contextmanager
def try_refresh_lock(conn: psycopg.Connection) -> Iterator[bool]:
    """Non-blocking variant for the scheduled poller: yields whether the lock was taken.

    A poll that finds the lock held must exit quietly: a probe or another poll is already talking
    to Spotify (spot-main-R-017). `conn` must be in autocommit mode (session-level lock).
    """
    row = conn.execute("select pg_try_advisory_lock(hashtext(%s))", (REFRESH_LOCK_NAME,)).fetchone()
    acquired = bool(row and row[0])
    try:
        yield acquired
    finally:
        if acquired:
            conn.execute("select pg_advisory_unlock(hashtext(%s))", (REFRESH_LOCK_NAME,))
