from __future__ import annotations

import json
import stat
from pathlib import Path

import httpx
import pytest

from spotify_lakehouse import auth
from spotify_lakehouse.config import Settings

SETTINGS = Settings(
    client_id="cid",
    client_secret="csecret",
    redirect_uri="http://127.0.0.1:3000",
    pg_user="spot",
    pg_password="pw",
)


def _seed(config_home: Path, entry: dict) -> Path:
    path = config_home / "tokens.json"
    path.write_text(json.dumps({"marc": entry}))
    return path


def _no_http(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected HTTP call to {request.url}")


def test_unexpired_token_is_returned_without_http(config_home: Path) -> None:
    _seed(config_home, {"access_token": "at-1", "refresh_token": "rt-1", "expires_at": 10_000})
    token = auth.get_access_token(
        "marc", SETTINGS, transport=httpx.MockTransport(_no_http), now=lambda: 1_000
    )
    assert token == "at-1"


def test_expired_token_refreshes_keeps_refresh_token_and_is_chmod_600(config_home: Path) -> None:
    path = _seed(config_home, {"access_token": "old", "refresh_token": "rt-1", "expires_at": 0})
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    token = auth.get_access_token(
        "marc", SETTINGS, transport=httpx.MockTransport(handler), now=lambda: 5_000
    )
    stored = json.loads(path.read_text())["marc"]
    assert token == "new"
    assert "grant_type=refresh_token" in seen[0]
    assert stored["refresh_token"] == "rt-1"  # Spotify did not rotate it, so the old one stays
    assert stored["expires_at"] == 5_000 + 3600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_missing_profile_says_how_to_fix(config_home: Path) -> None:
    with pytest.raises(auth.AuthError, match="spot auth marc"):
        auth.get_access_token("marc", SETTINGS)


def test_refresh_failure_is_named(config_home: Path) -> None:
    _seed(config_home, {"access_token": "old", "refresh_token": "rt-1", "expires_at": 0})
    transport = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(auth.AuthError, match="invalid_grant"):
        auth.get_access_token("marc", SETTINGS, transport=transport, now=lambda: 1)


@pytest.mark.parametrize("slug", ["Marc", "marc!", "", "1marc", "../marc"])
def test_invalid_slug(slug: str) -> None:
    with pytest.raises(auth.AuthError):
        auth.validate_slug(slug)


def test_callback_state_mismatch() -> None:
    with pytest.raises(auth.AuthError, match="state mismatch"):
        auth.validate_callback({"state": "other", "code": "c"}, "expected")


def test_callback_denied_mentions_user_management() -> None:
    with pytest.raises(auth.AuthError, match="User Management"):
        auth.validate_callback({"state": "s", "error": "access_denied"}, "s")


def test_callback_returns_code() -> None:
    assert auth.validate_callback({"state": "s", "code": "abc"}, "s") == "abc"


def test_one_spotify_account_cannot_bind_two_profiles() -> None:
    store = {"marc": {"spotify_user_id": "user-1"}}
    with pytest.raises(auth.AuthError, match="already authorized as profile 'marc'"):
        auth.check_binding(store, "marie", "user-1")
    auth.check_binding(store, "marc", "user-1")  # re-authorizing the same profile is fine


def test_invalidate_forces_next_refresh(config_home: Path) -> None:
    path = _seed(config_home, {"access_token": "a", "refresh_token": "r", "expires_at": 99_999})
    auth.invalidate("marc")
    assert json.loads(path.read_text())["marc"]["expires_at"] == 0
