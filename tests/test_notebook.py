"""Integration tests for spotify_lakehouse.notebook.setup against shared Postgres (read-only)."""

from __future__ import annotations

import pytest

from spotify_lakehouse.config import ConfigError


def test_setup_opens_a_context_without_exposing_credentials(db) -> None:
    from spotify_lakehouse.notebook import setup

    ctx = setup(profile="marc", table_css=False)
    try:
        assert ctx.session == "main"
        assert ctx.mart_schema == "mart_main"
        assert ctx.scalar("select 1") == 1
        frame = ctx.frame("select count(*) as n from {mart}.dim_date where date_key > %s", (0,))
        assert int(frame.loc[0, "n"]) > 7000
        text = repr(ctx)
        assert "password" not in text.lower()
        assert ctx.settings.pg_password not in text
        assert ctx.settings.client_secret not in text
    finally:
        ctx.close()


def test_setup_refuses_an_unregistered_profile_and_says_how_to_fix(db) -> None:
    from spotify_lakehouse.notebook import setup

    with pytest.raises(ConfigError, match="profiles.csv"):
        setup(profile="nobody_registered_this", table_css=False)
