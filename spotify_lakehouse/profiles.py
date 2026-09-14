"""Profile registry: household role and home timezone per profile, kept OUT of the public repo.

The source of truth is ~/.config/spot/profiles.csv (chmod 600). `spot sync-profiles` validates it
and replaces spot_meta.profile_registry, which dbt reads for dim_profile. The repo ships only
profiles.csv.example: roles and timezones of family members are personal data about other people.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from spotify_lakehouse.auth import PROFILE_SLUG
from spotify_lakehouse.config import ConfigError, config_dir

HEADER = ("profile_slug", "household_role", "home_timezone")
HOUSEHOLD_ROLES = ("self", "spouse", "child", "friend")


class ProfileRegistryError(ConfigError):
    """The registry file is missing or invalid. The message names the row and the fix."""


@dataclass(frozen=True)
class ProfileEntry:
    profile_slug: str
    household_role: str
    home_timezone: str


def registry_path() -> Path:
    return config_dir() / "profiles.csv"


def parse_registry(text: str) -> list[ProfileEntry]:
    """Parse and validate registry CSV text. Blank lines and lines starting with `#` are ignored."""
    lines = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    expected = ",".join(HEADER)
    if not lines:
        raise ProfileRegistryError(f"Registry has no header row. Expected: {expected}")
    reader = csv.reader(lines)
    header = tuple(cell.strip() for cell in next(reader))
    if header != HEADER:
        raise ProfileRegistryError(f"Registry header is {','.join(header)}; expected {expected}")

    entries: list[ProfileEntry] = []
    seen: set[str] = set()
    for number, row in enumerate(reader, start=1):
        if len(row) != len(HEADER):
            raise ProfileRegistryError(f"Data row {number}: expected 3 columns, got {len(row)}")
        slug, role, timezone = (cell.strip() for cell in row)
        if not PROFILE_SLUG.fullmatch(slug):
            raise ProfileRegistryError(f"Data row {number}: invalid profile_slug {slug!r}")
        if slug in seen:
            raise ProfileRegistryError(f"Data row {number}: duplicate profile_slug {slug!r}")
        if role not in HOUSEHOLD_ROLES:
            raise ProfileRegistryError(
                f"Data row {number}: household_role {role!r} is not one of "
                f"{', '.join(HOUSEHOLD_ROLES)}"
            )
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ProfileRegistryError(
                f"Data row {number}: home_timezone {timezone!r} is not an IANA zone name "
                "(e.g. America/Los_Angeles)"
            ) from exc
        seen.add(slug)
        entries.append(ProfileEntry(slug, role, timezone))
    return entries


def load_registry(path: Path | None = None) -> list[ProfileEntry]:
    path = path or registry_path()
    if not path.is_file():
        raise ProfileRegistryError(
            f"{path} does not exist. Fix: run `make bootstrap` to create it from "
            "profiles.csv.example, then add one row per profile: "
            "profile_slug,household_role,home_timezone"
        )
    entries = parse_registry(path.read_text())
    if not entries:
        raise ProfileRegistryError(
            f"{path} declares no profiles. Add one row per profile, e.g. yourslug,self,Area/City"
        )
    return entries


def sync_registry(conn: psycopg.Connection, entries: list[ProfileEntry]) -> int:
    """Replace spot_meta.profile_registry with `entries` in one transaction."""
    with conn.transaction():
        conn.execute("delete from spot_meta.profile_registry")
        with conn.cursor() as cur:
            cur.executemany(
                "insert into spot_meta.profile_registry "
                "(profile_slug, household_role, home_timezone) values (%s, %s, %s)",
                [(e.profile_slug, e.household_role, e.home_timezone) for e in entries],
            )
    return len(entries)
