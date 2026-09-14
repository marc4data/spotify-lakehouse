from __future__ import annotations

from pathlib import Path

import pytest

from spotify_lakehouse import profiles

HEADER = "profile_slug,household_role,home_timezone"


def test_parses_rows_and_ignores_comments_and_blank_lines() -> None:
    text = (
        f"# template comment\n\n{HEADER}\n"
        "alpha,self,America/New_York\n  # another\nbeta,child,UTC\n"
    )
    entries = profiles.parse_registry(text)
    assert entries == [
        profiles.ProfileEntry("alpha", "self", "America/New_York"),
        profiles.ProfileEntry("beta", "child", "UTC"),
    ]


def test_template_in_repo_parses_to_zero_profiles() -> None:
    template = Path(__file__).resolve().parents[1] / "profiles.csv.example"
    assert profiles.parse_registry(template.read_text()) == []


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("Alpha,self,UTC", "invalid profile_slug"),
        ("alpha,parent,UTC", "household_role"),
        ("alpha,self,Mars/Olympus_Mons", "IANA"),
        ("alpha,self,../../etc", "IANA"),
        ("alpha,self", "expected 3 columns"),
    ],
)
def test_invalid_rows_are_named(row: str, message: str) -> None:
    with pytest.raises(profiles.ProfileRegistryError, match=message):
        profiles.parse_registry(f"{HEADER}\n{row}\n")


def test_duplicate_slug_rejected() -> None:
    with pytest.raises(profiles.ProfileRegistryError, match="duplicate"):
        profiles.parse_registry(f"{HEADER}\nalpha,self,UTC\nalpha,child,UTC\n")


def test_wrong_header_rejected() -> None:
    with pytest.raises(profiles.ProfileRegistryError, match="expected profile_slug"):
        profiles.parse_registry("slug,role,tz\nalpha,self,UTC\n")


def test_missing_file_says_run_bootstrap(config_home: Path) -> None:
    with pytest.raises(profiles.ProfileRegistryError, match="make bootstrap"):
        profiles.load_registry()


def test_header_only_file_is_an_error_not_an_empty_registry(config_home: Path) -> None:
    (config_home / "profiles.csv").write_text(f"{HEADER}\n")
    with pytest.raises(profiles.ProfileRegistryError, match="declares no profiles"):
        profiles.load_registry()
