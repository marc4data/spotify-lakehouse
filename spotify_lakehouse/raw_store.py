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

# Paths discarded before anything is persisted, keyed by FEED so an endpoint carrying an id
# resolves too. data-contracts §3: email never reaches the extractor; the scrub stays as a backstop.
#
# 🚨 Playlists are the first feed carrying OTHER PEOPLE'S identities (R-054): a playlist has an
# owner, and a collaborative one names a different person in `added_by` on every row. They are
# discarded here rather than redacted downstream — a field that is never written cannot leak from a
# table nobody thought to check (R-052 F2: that leak was in three tables, not one).
#
# Grammar: "a.b" descends a dict; "a[].b" descends every element of the list at `a`.
DISCARDED_PATHS_BY_FEED: dict[str, tuple[str, ...]] = {
    "me": ("email",),
    "playlist": (
        "items[].owner.id",
        "items[].owner.display_name",
        "items[].owner.uri",
        "items[].owner.href",
        "items[].owner.external_urls",
        "items[].images",  # a playlist image can be a photograph the owner uploaded
    ),
    "playlist_item": (
        "items[].added_by",  # whole object: id, uri, href and external_urls are all identifying
    ),
}

# data-contracts §1: one raw table, one `feed` value per endpoint. Staging builds one view per feed.
FEEDS_BY_ENDPOINT: dict[str, str] = {
    "/me": "me",
    "/me/player/recently-played": "recently_played",
    "/me/playlists": "playlist",  # R-054
}
ARTIST_ENDPOINT = re.compile(r"/artists/[A-Za-z0-9]+")
TRACK_ENDPOINT = re.compile(r"/tracks/[A-Za-z0-9]+")  # R-037
# R-054. Bare `/playlists` still has no feed and still raises, which tests/test_raw_store.py pins.
PLAYLIST_ITEMS_ENDPOINT = re.compile(r"/playlists/[A-Za-z0-9]+/(?:items|tracks)")
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
    if TRACK_ENDPOINT.fullmatch(endpoint):
        return "track"
    if PLAYLIST_ITEMS_ENDPOINT.fullmatch(endpoint):
        return "playlist_item"
    raise ValueError(f"No feed is defined for {endpoint!r}; add it to raw_store.FEEDS_BY_ENDPOINT.")


def _discard(node: Any, parts: tuple[str, ...], removed: set[str], prefix: str) -> None:
    """Delete one dotted path in place, recording every path actually removed."""
    if not parts or not isinstance(node, dict):
        return
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        key = head[:-2]
        sequence = node.get(key)
        if isinstance(sequence, list):
            for element in sequence:
                _discard(element, rest, removed, f"{prefix}{head}.")
        return
    if not rest:
        if head in node:
            del node[head]
            removed.add(f"{prefix}{head}")
        return
    _discard(node.get(head), rest, removed, f"{prefix}{head}.")


OWNERSHIP_FLAG = "is_owned_by_profile"


def _flag_ownership(payload: dict[str, Any], owner_id: str) -> int:
    """Set a BOOLEAN on each playlist from `owner.id`, before the id is discarded (R-058).

    🚨 The comparison has to happen here and nowhere else. `dim_playlist` cannot answer "is this
    Marc's?" because owner identity never reaches `raw` (R-054 F6) — and that stays true. What
    changes is that `scrub` sees `owner.id` on its way past, compares it, and keeps one bit. No
    identifier is written: the id is gone by the time this function returns.
    """
    flagged = 0
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        owner = item.get("owner")
        observed = owner.get("id") if isinstance(owner, dict) else None
        item[OWNERSHIP_FLAG] = bool(observed) and str(observed) == str(owner_id)
        flagged += 1
    return flagged


def scrub(
    endpoint: str, payload: dict[str, Any], *, owner_id: str | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Return a copy of `payload` without discarded fields, plus the paths that were removed.

    Nested paths are supported because playlist PII is nested (R-054): the owner sits under
    `items[].owner` and `added_by` under every item. An endpoint with no feed discards nothing
    rather than raising, which is what the callers relied on before feeds were involved.

    `owner_id` is optional and only the playlist feed uses it: given the profile's own Spotify id,
    each playlist gains a boolean `is_owned_by_profile` **computed before the discards run**. Every
    other caller is unaffected and passes nothing.
    """
    clean = copy.deepcopy(payload)
    try:
        feed = feed_for_endpoint(endpoint)
    except ValueError:
        return clean, []
    if feed == "playlist" and owner_id:
        _flag_ownership(clean, owner_id)
    removed: set[str] = set()
    for path in DISCARDED_PATHS_BY_FEED.get(feed, ()):
        _discard(clean, tuple(path.split(".")), removed, "")
    return clean, sorted(removed)


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


EXTERNAL_SOURCES = frozenset({"musicbrainz"})
EXTERNAL_FILE_KEY = re.compile(r"[A-Za-z0-9-]+")  # ISRCs and MBIDs (UUIDs carry hyphens)


def write_external_response(
    source: str,
    feed: str,
    payload: dict[str, Any],
    run_id: str,
    fetched_at: datetime,
    key: str,
) -> str:
    """Write one non-Spotify response under data/raw/external/<source>/<feed>/ (spot-main-R-024)."""
    if source not in EXTERNAL_SOURCES:
        raise ValueError(f"Unknown external source {source!r}")
    if not FEED_NAME.fullmatch(feed):
        raise ValueError(f"Invalid feed name {feed!r}")
    if not EXTERNAL_FILE_KEY.fullmatch(key):
        raise ValueError(f"Invalid file key {key!r}: letters, digits and hyphens only")
    relative = Path("external") / source / feed / f"{fetched_at:%Y%m%dT%H%M%SZ}_{run_id}_{key}.json"
    target = raw_dir() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as fh:  # "x": never overwrite an existing raw file
        json.dump(payload, fh, indent=2, sort_keys=True)
    target.chmod(0o600)
    return relative.as_posix()


def insert_external_response(
    conn: psycopg.Connection,
    *,
    source: str,
    feed: str,
    request_key: str,
    payload: dict[str, Any],
    source_file: str,
) -> int:
    row = conn.execute(
        "insert into raw.external_response (source, feed, request_key, payload, source_file) "
        "values (%s, %s, %s, %s, %s) returning id",
        (source, feed, request_key, Jsonb(payload), source_file),
    ).fetchone()
    assert row is not None
    return int(row[0])


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
