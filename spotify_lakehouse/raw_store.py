"""Persist API responses: immutable JSON under data/raw/ and one row in raw.api_response."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from spotify_lakehouse.config import ConfigError, repo_root

# Fields discarded before anything is persisted. data-contracts §3: "email is never stored.
# The extractor reads it from /me to match accounts and discards it."
DISCARDED_FIELDS: dict[str, tuple[str, ...]] = {"/me": ("email",)}


def raw_dir() -> Path:
    path = repo_root() / "data" / "raw"
    if not path.is_dir():
        raise ConfigError(
            f"{path} does not exist. "
            "Fix: run `make bootstrap` (it links data/raw -> ~/spot-data/raw)."
        )
    return path


def scrub(endpoint: str, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a copy of `payload` without discarded fields, plus the names that were removed."""
    clean = copy.deepcopy(payload)
    removed = [name for name in DISCARDED_FIELDS.get(endpoint, ()) if name in clean]
    for name in removed:
        del clean[name]
    return clean, removed


def write_response(
    profile: str, endpoint: str, payload: dict[str, Any], run_id: str, fetched_at: datetime
) -> str:
    """Write one response file and return its path relative to data/raw (the `source_file`)."""
    feed = endpoint.strip("/").replace("/", "_")
    relative = Path("api") / feed / profile / f"{fetched_at:%Y%m%dT%H%M%SZ}_{run_id}.json"
    target = raw_dir() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as fh:  # "x": never overwrite an existing raw file
        json.dump(payload, fh, indent=2, sort_keys=True)
    target.chmod(0o600)
    return relative.as_posix()


def insert_response(
    conn: psycopg.Connection, payload: dict[str, Any], source_file: str, profile: str
) -> int:
    row = conn.execute(
        "insert into raw.api_response (payload, source_file, profile_key) "
        "values (%s, %s, %s) returning id",
        (Jsonb(payload), source_file, profile),
    ).fetchone()
    assert row is not None
    return int(row[0])
