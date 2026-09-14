from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spotify_lakehouse import raw_store
from spotify_lakehouse.config import ConfigError


def test_scrub_removes_email_from_me_without_mutating_input() -> None:
    payload = {"id": "u", "email": "someone@example.org", "country": "US"}
    clean, removed = raw_store.scrub("/me", payload)
    assert "email" not in clean
    assert removed == ["email"]
    assert "email" in payload


def test_scrub_leaves_other_endpoints_alone() -> None:
    payload = {"items": [], "email": "not-a-profile-field"}
    clean, removed = raw_store.scrub("/me/player/recently-played", payload)
    assert clean == payload
    assert removed == []


def test_write_response_path_and_permissions(repo: Path) -> None:
    (repo / "data" / "raw").mkdir(parents=True)
    when = datetime(2026, 9, 13, 18, 30, 5, tzinfo=UTC)
    rel = raw_store.write_response("marc", "/me/player/recently-played", {"a": 1}, "run1", when)
    assert rel == "api/me_player_recently-played/marc/20260913T183005Z_run1.json"
    target = repo / "data" / "raw" / rel
    assert json.loads(target.read_text()) == {"a": 1}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_response_never_overwrites(repo: Path) -> None:
    (repo / "data" / "raw").mkdir(parents=True)
    when = datetime(2026, 9, 13, tzinfo=UTC)
    raw_store.write_response("marc", "/me", {"a": 1}, "run1", when)
    with pytest.raises(FileExistsError):
        raw_store.write_response("marc", "/me", {"a": 2}, "run1", when)


def test_missing_data_raw_says_run_bootstrap(repo: Path) -> None:
    with pytest.raises(ConfigError, match="make bootstrap"):
        raw_store.raw_dir()
