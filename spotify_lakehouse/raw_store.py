"""Persist API responses: immutable JSON under data/raw/ and one row in raw.api_response."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from spotify_lakehouse.config import ConfigError, repo_root

# Fields discarded before anything is persisted. data-contracts §3: email never reaches the
# extractor; the scrub stays as a backstop.
DISCARDED_FIELDS: dict[str, tuple[str, ...]] = {"/me": ("email",)}

# data-contracts §1: one raw table, one `feed` value per endpoint. Staging builds one view per feed.
FEEDS_BY_ENDPOINT: dict[str, str] = {
    "/me": "me",
    "/me/player/recently-played": "recently_played",
}
ARTIST_ENDPOINT = re.compile(r"/artists/[A-Za-z0-9]+")
FEED_NAME = re.compile(r"[a-z][a-z0-9_]*")
FILE_KEY = re.compile(r"[A-Za-z0-9]+")


def raw_dir() -> Path:
    path = repo_root() / "data" / "raw"
    if not path.is_dir():
        raise ConfigError(
            f"{path} does not exist. "
            "Fix: run `make bootstrap` (it links data/raw -> ~/spot-data/raw)."
        )
    return path


def feed_for_endpoint(endpoint: str) -> str:
    """The raw.api_response `feed` for an API path. An unknown endpoint is an error, not a guess."""
    if endpoint in FEEDS_BY_ENDPOINT:
        return FEEDS_BY_ENDPOINT[endpoint]
    if ARTIST_ENDPOINT.fullmatch(endpoint):
        return "artist"
    raise ValueError(f"No feed is defined for {endpoint!r}; add it to raw_store.FEEDS_BY_ENDPOINT.")


def scrub(endpoint: str, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a copy of `payload` without discarded fields, plus the names that were removed."""
    clean = copy.deepcopy(payload)
    removed = [name for name in DISCARDED_FIELDS.get(endpoint, ()) if name in clean]
    for name in removed:
        del clean[name]
    return clean, removed


def write_response(
    profile: str,
    feed: str,
    payload: dict[str, Any],
    run_id: str,
    fetched_at: datetime,
    key: str | None = None,
) -> str:
    """Write one response file and return its path relative to data/raw (the `source_file`).

    `key` distinguishes several responses of one feed in one run (e.g. one file per artist id).
    """
    if not FEED_NAME.fullmatch(feed):
        raise ValueError(f"Invalid feed name {feed!r}")
    if key is not None and not FILE_KEY.fullmatch(key):
        raise ValueError(f"Invalid file key {key!r}: letters and digits only")
    suffix = f"_{key}" if key else ""
    name = f"{fetched_at:%Y%m%dT%H%M%SZ}_{run_id}{suffix}.json"
    relative = Path("api") / feed / profile / name
    target = raw_dir() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as fh:  # "x": never overwrite an existing raw file
        json.dump(payload, fh, indent=2, sort_keys=True)
    target.chmod(0o600)
    return relative.as_posix()


def insert_response(
    conn: psycopg.Connection,
    payload: dict[str, Any],
    source_file: str,
    profile: str,
    feed: str,
) -> int:
    row = conn.execute(
        "insert into raw.api_response (feed, payload, source_file, profile_slug) "
        "values (%s, %s, %s, %s) returning id",
        (feed, Jsonb(payload), source_file, profile),
    ).fetchone()
    assert row is not None
    return int(row[0])
