"""Spotify OAuth Authorization Code flow and the shared per-profile token store.

Tokens live in ~/.config/spot/tokens.json (chmod 600), keyed by profile slug, shared by every
checkout. Reads and refreshes hold an flock so two sessions never race a refresh.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import secrets
import tempfile
import time
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import structlog

from spotify_lakehouse.config import Settings, config_dir

log = structlog.get_logger(__name__)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
ME_URL = "https://api.spotify.com/v1/me"
SCOPES = (
    "user-read-recently-played",
    "user-top-read",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
    "user-follow-read",
    "user-read-private",
)
EXPIRY_SKEW_SECONDS = 60
PROFILE_SLUG = re.compile(r"[a-z][a-z0-9_]{0,31}")


class AuthError(RuntimeError):
    """Authorization failed or no usable token exists. The message says what to do."""


def validate_slug(slug: str) -> str:
    if not PROFILE_SLUG.fullmatch(slug):
        raise AuthError(f"Invalid profile slug {slug!r}: use a lowercase handle such as `marc`.")
    return slug


def tokens_path() -> Path:
    return config_dir() / "tokens.json"


@contextmanager
def _locked_store() -> Iterator[Path]:
    path = tokens_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.with_name(".tokens.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_store(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text()) if path.is_file() else {}


def _write_store(path: Path, store: dict[str, dict[str, Any]]) -> None:
    """Atomic write; the temp file is chmod 600 before any secret touches it."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tokens-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(store, fh, indent=2, sort_keys=True)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    path.chmod(0o600)


def _token_entry(
    token: dict[str, Any], previous: dict[str, Any] | None, now: float
) -> dict[str, Any]:
    entry = dict(previous or {})
    entry["access_token"] = token["access_token"]
    # Spotify may or may not rotate the refresh token; keep the old one when it doesn't.
    entry["refresh_token"] = token.get("refresh_token") or entry.get("refresh_token")
    entry["expires_at"] = now + int(token.get("expires_in", 3600))
    entry["scope"] = token.get("scope", entry.get("scope", ""))
    if not entry["refresh_token"]:
        raise AuthError("Spotify returned no refresh token. Re-run `spot auth <profile>`.")
    return entry


def _token_request(
    settings: Settings, data: dict[str, str], transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    basic = base64.b64encode(f"{settings.client_id}:{settings.client_secret}".encode()).decode()
    with httpx.Client(timeout=30, transport=transport) as client:
        resp = client.post(TOKEN_URL, data=data, headers={"Authorization": f"Basic {basic}"})
    if resp.status_code != 200:
        try:
            detail = resp.json().get("error_description") or resp.json().get("error")
        except ValueError:
            detail = "no JSON body"
        raise AuthError(f"Spotify token endpoint returned {resp.status_code}: {detail}")
    return resp.json()


def validate_callback(params: dict[str, str], expected_state: str) -> str:
    """Return the authorization code from the redirect, or raise with a useful message."""
    if params.get("state") != expected_state:
        raise AuthError("OAuth state mismatch — the callback did not come from this login attempt.")
    if "error" in params:
        raise AuthError(
            f"Spotify denied authorization ({params['error']}). If the account is not listed "
            "under Dashboard -> your app -> Settings -> User Management, add it and retry."
        )
    if not params.get("code"):
        raise AuthError("Spotify redirect carried no authorization code.")
    return params["code"]


def check_binding(store: dict[str, dict[str, Any]], slug: str, spotify_user_id: str) -> None:
    """Refuse to bind one Spotify account to two profiles (e.g. Marie logged in as Marc)."""
    for other_slug, entry in store.items():
        if other_slug != slug and entry.get("spotify_user_id") == spotify_user_id:
            raise AuthError(
                f"This Spotify account is already authorized as profile '{other_slug}'. "
                f"The browser was probably still logged in as that person. Log out at "
                f"spotify.com, then re-run `spot auth {slug}`."
            )


def _await_callback(redirect_uri: str, auth_url: str, open_browser: bool, timeout: int) -> dict:
    parsed = urlparse(redirect_uri)
    expected_path = parsed.path or "/"
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API
            request = urlparse(self.path)
            if request.path != expected_path:
                self.send_response(404)
                self.end_headers()
                return
            received.update({k: v[0] for k, v in parse_qs(request.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"spot: authorization received. You can close this tab.")

        def log_message(self, *args: object) -> None:
            # The query string carries the authorization code; never log it.
            pass

    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 80), Handler)
    server.timeout = 5
    print("Opening Spotify login in your browser. If nothing opens, visit:\n")
    print(f"  {auth_url}\n")
    if open_browser:
        webbrowser.open(auth_url)
    deadline = time.monotonic() + timeout
    try:
        while not received and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not received:
        raise AuthError(f"No redirect received on {redirect_uri} within {timeout}s.")
    return received


def authorize(
    slug: str, settings: Settings, *, open_browser: bool = True, timeout: int = 300
) -> dict[str, Any]:
    """Run the interactive login for one profile and store its refresh token."""
    validate_slug(slug)
    state = secrets.token_urlsafe(24)
    auth_url = f"{AUTHORIZE_URL}?" + urlencode(
        {
            "client_id": settings.client_id,
            "response_type": "code",
            "redirect_uri": settings.redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            # Force the account chooser so a family member isn't silently bound as whoever
            # the browser is already logged in as.
            "show_dialog": "true",
        }
    )
    params = _await_callback(settings.redirect_uri, auth_url, open_browser, timeout)
    code = validate_callback(params, state)
    token = _token_request(
        settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.redirect_uri,
        },
    )
    me = httpx.get(ME_URL, headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=30)
    if me.status_code != 200:
        raise AuthError(
            f"Login succeeded but GET /me returned {me.status_code}. In development mode this "
            "usually means the account is not in User Management, or Premium is required (R-016)."
        )
    profile = me.json()
    with _locked_store() as path:
        store = _read_store(path)
        check_binding(store, slug, profile["id"])
        entry = _token_entry(token, store.get(slug), time.time())
        entry["spotify_user_id"] = profile["id"]
        store[slug] = entry
        _write_store(path, store)
    log.info("profile_authorized", profile=slug, product=profile.get("product"))
    return {"product": profile.get("product"), "scope": entry["scope"]}


def get_access_token(
    slug: str,
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    now: Callable[[], float] = time.time,
) -> str:
    """Return a valid access token, refreshing (and persisting) it when near expiry."""
    validate_slug(slug)
    with _locked_store() as path:
        store = _read_store(path)
        entry = store.get(slug)
        if not entry:
            raise AuthError(f"No token for profile '{slug}'. Fix: `uv run spot auth {slug}`.")
        if entry.get("expires_at", 0) - EXPIRY_SKEW_SECONDS > now():
            return entry["access_token"]
        token = _token_request(
            settings,
            {"grant_type": "refresh_token", "refresh_token": entry["refresh_token"]},
            transport=transport,
        )
        store[slug] = _token_entry(token, entry, now())
        _write_store(path, store)
        log.info("access_token_refreshed", profile=slug)
        return store[slug]["access_token"]


def invalidate(slug: str) -> None:
    """Mark a profile's access token expired so the next call refreshes it (used on HTTP 401)."""
    with _locked_store() as path:
        store = _read_store(path)
        if slug in store:
            store[slug]["expires_at"] = 0
            _write_store(path, store)
