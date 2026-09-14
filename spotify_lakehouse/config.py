"""Session identity, paths, and credentials.

Everything session-scoped derives from the untracked `.session` file. There is deliberately no
default: a worktree that silently believes it is `main` would build into `analytics`
(docs/worktree-protocol.md §1).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

SESSION_PATTERN = re.compile(r"(main|wt[a-z])")
SPOTIFY_ENV_KEYS = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI")
POSTGRES_ENV_KEYS = ("SPOT_PG_USER", "SPOT_PG_PASSWORD")
REQUIRED_ENV_KEYS = SPOTIFY_ENV_KEYS + POSTGRES_ENV_KEYS
PLACEHOLDER_MARKER = "replace-me"


class ConfigError(RuntimeError):
    """Configuration is missing or invalid. The message always says how to fix it."""


def repo_root() -> Path:
    """Root of the checkout this package was installed from (each worktree has its own venv)."""
    override = os.environ.get("SPOT_REPO_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[1]


def config_dir() -> Path:
    """Shared credential directory, outside every checkout."""
    override = os.environ.get("SPOT_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".config" / "spot"


def session(root: Path | None = None) -> str:
    """Return this checkout's session token. Raises if `.session` is missing or unrecognized."""
    path = (root or repo_root()) / ".session"
    if not path.is_file():
        raise ConfigError(
            f".session is missing at {path}. "
            "Fix: `echo main > .session` in the primary checkout, or `echo wta > .session` "
            "in a worktree, then run `make bootstrap`."
        )
    token = path.read_text().strip()
    if not SESSION_PATTERN.fullmatch(token):
        raise ConfigError(
            f".session at {path} contains an unrecognized token {token!r}. "
            "Expected `main` or `wt` plus one lowercase letter (`wta`, `wtb`, ...)."
        )
    return token


def stg_schema(session_name: str | None = None) -> str:
    return f"stg_{session_name or session()}"


def mart_schema(session_name: str | None = None) -> str:
    return f"mart_{session_name or session()}"


@dataclass(frozen=True)
class Settings:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    redirect_uri: str
    pg_user: str
    pg_password: str = field(repr=False)
    pg_host: str = "127.0.0.1"
    pg_port: int = 5433
    pg_database: str = "spot"


def missing_env_keys(
    values: dict[str, str | None], keys: tuple[str, ...] = REQUIRED_ENV_KEYS
) -> list[str]:
    """Required keys that are absent, empty, or still the template placeholder."""
    missing = []
    for key in keys:
        value = (values.get(key) or "").strip()
        if not value or PLACEHOLDER_MARKER in value:
            missing.append(key)
    return missing


def load_settings(*, require_spotify: bool = True) -> Settings:
    """Load ~/.config/spot/.env. Database-only callers pass `require_spotify=False`."""
    env_path = config_dir() / ".env"
    if not env_path.is_file():
        raise ConfigError(
            f"{env_path} does not exist. Fix: run `make bootstrap` to create it from "
            ".env.example, then fill in the Spotify Client ID and Client Secret."
        )
    values = dotenv_values(env_path)
    missing = missing_env_keys(values, REQUIRED_ENV_KEYS if require_spotify else POSTGRES_ENV_KEYS)
    if missing:
        raise ConfigError(
            f"{env_path} is missing required key(s): {', '.join(missing)}. "
            "Fix: edit that file and replace each `replace-me` value "
            "(Spotify values are under developer.spotify.com/dashboard -> your app -> Settings)."
        )
    return Settings(
        client_id=values.get("SPOTIFY_CLIENT_ID") or "",
        client_secret=values.get("SPOTIFY_CLIENT_SECRET") or "",
        redirect_uri=values.get("SPOTIFY_REDIRECT_URI") or "",
        pg_user=values["SPOT_PG_USER"] or "",
        pg_password=values["SPOT_PG_PASSWORD"] or "",
        pg_host=values.get("SPOT_PG_HOST") or "127.0.0.1",
        pg_port=int(values.get("SPOT_PG_PORT") or 5433),
    )
