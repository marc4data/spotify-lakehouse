from __future__ import annotations

from pathlib import Path

import pytest

from spotify_lakehouse import config


def test_session_missing_raises_with_fix(repo: Path) -> None:
    with pytest.raises(config.ConfigError, match=r"echo main > \.session"):
        config.session()


@pytest.mark.parametrize("token", ["prod", "MAIN", "wt", "wtab", "analytics", ""])
def test_session_unrecognized_raises(repo: Path, token: str) -> None:
    (repo / ".session").write_text(token)
    with pytest.raises(config.ConfigError):
        config.session()


@pytest.mark.parametrize("token", ["main", "wta", "wtz"])
def test_session_valid_tokens(repo: Path, token: str) -> None:
    (repo / ".session").write_text(f"{token}\n")
    assert config.session() == token
    assert config.stg_schema() == f"stg_{token}"
    assert config.mart_schema() == f"mart_{token}"


def test_load_settings_missing_file(config_home: Path) -> None:
    with pytest.raises(config.ConfigError, match="make bootstrap"):
        config.load_settings()


def test_load_settings_names_placeholder_keys(config_home: Path) -> None:
    (config_home / ".env").write_text(
        "SPOTIFY_CLIENT_ID=replace-me\nSPOTIFY_CLIENT_SECRET=\n"
        "SPOTIFY_REDIRECT_URI=http://127.0.0.1:3000\nSPOT_PG_USER=spot\nSPOT_PG_PASSWORD=pw\n"
    )
    with pytest.raises(config.ConfigError) as exc:
        config.load_settings()
    assert "SPOTIFY_CLIENT_ID" in str(exc.value)
    assert "SPOTIFY_CLIENT_SECRET" in str(exc.value)
    assert "SPOT_PG_USER" not in str(exc.value)


def test_database_only_callers_do_not_need_spotify_keys(config_home: Path) -> None:
    (config_home / ".env").write_text(
        "SPOTIFY_CLIENT_ID=replace-me\nSPOTIFY_CLIENT_SECRET=replace-me\n"
        "SPOT_PG_USER=spot\nSPOT_PG_PASSWORD=pw\n"
    )
    assert config.load_settings(require_spotify=False).pg_user == "spot"
    with pytest.raises(config.ConfigError, match="SPOTIFY_CLIENT_ID"):
        config.load_settings()


def test_settings_repr_hides_secrets(config_home: Path) -> None:
    (config_home / ".env").write_text(
        "SPOTIFY_CLIENT_ID=cid-value\nSPOTIFY_CLIENT_SECRET=secret-value\n"
        "SPOTIFY_REDIRECT_URI=http://127.0.0.1:3000\nSPOT_PG_USER=spot\nSPOT_PG_PASSWORD=pw-value\n"
    )
    text = repr(config.load_settings())
    assert "secret-value" not in text
    assert "cid-value" not in text
    assert "pw-value" not in text
