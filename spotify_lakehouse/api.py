"""Thin Spotify Web API client: pacing, Retry-After, one refresh on 401, dead-endpoint refusal."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, Self

import httpx
import structlog

from spotify_lakehouse import auth
from spotify_lakehouse.config import Settings

log = structlog.get_logger(__name__)

API_BASE = "https://api.spotify.com/v1"
DEFAULT_RETRY_AFTER_SECONDS = 5
# Cut off for apps registered after 2024-11-27 (CLAUDE.md §4). Refused before any HTTP call.
DEAD_ENDPOINTS = re.compile(
    r"^/(audio-features|audio-analysis|recommendations|browse/featured-playlists"
    r"|artists/[^/]+/related-artists|browse/categories/[^/]+/playlists)(/|$|\?)"
)


class SpotifyApiError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _error_message(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message", "") or resp.reason_phrase
    except (ValueError, AttributeError):
        return resp.reason_phrase


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(1.0, float(resp.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS)))
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


class SpotifyClient:
    def __init__(
        self,
        profile: str,
        token_provider: Callable[[], str],
        on_unauthorized: Callable[[], None],
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = 1.0,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profile = profile
        self._token_provider = token_provider
        self._on_unauthorized = on_unauthorized
        self._http = httpx.Client(base_url=API_BASE, timeout=30, transport=transport)
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    @classmethod
    def for_profile(cls, profile: str, settings: Settings, **kwargs: Any) -> SpotifyClient:
        return cls(
            profile,
            token_provider=lambda: auth.get_access_token(profile, settings),
            on_unauthorized=lambda: auth.invalidate(profile),
            **kwargs,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = self._min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if DEAD_ENDPOINTS.match(path):
            raise SpotifyApiError(410, f"{path} is closed to this app (CLAUDE.md §4); not calling.")
        attempts = 0
        refreshed = False
        while True:
            self._pace()
            headers = {"Authorization": f"Bearer {self._token_provider()}"}
            resp = self._http.get(path, params=params, headers=headers)
            self._last_call = self._clock()
            status = resp.status_code

            if status == 429 and attempts < self._max_retries:
                wait = _retry_after(resp)
                log.warning("rate_limited", path=path, retry_after_s=wait, attempt=attempts + 1)
                self._sleep(wait)
                attempts += 1
                continue
            if status == 401 and not refreshed:
                log.info("access_token_rejected", path=path, profile=self.profile)
                self._on_unauthorized()
                refreshed = True
                continue
            if status >= 500 and attempts < self._max_retries:
                wait = float(2**attempts)
                log.warning("server_error", path=path, status=status, backoff_s=wait)
                self._sleep(wait)
                attempts += 1
                continue
            if status == 403:
                raise SpotifyApiError(
                    403,
                    f"Spotify refused {path} for profile '{self.profile}' (403: "
                    f"{_error_message(resp)}). In development mode this usually means the "
                    "account is not listed under Dashboard -> Settings -> User Management, "
                    "or the account lacks Premium (open question R-016).",
                )
            if not resp.is_success:
                raise SpotifyApiError(status, f"GET {path} -> {status}: {_error_message(resp)}")
            return resp.json()
