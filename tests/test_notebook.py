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


def test_notebook_api_takes_its_own_lock_and_leaves_spot_refresh_to_the_poller(db) -> None:
    """R-042 (R-041 F3): a notebook's sampling burst must never make a scheduled poll skip."""
    from spotify_lakehouse.notebook import NOTEBOOK_LOCK_NAME, setup

    def free(name: str) -> bool:
        row = db.execute("select pg_try_advisory_lock(hashtext(%s))", (name,)).fetchone()
        taken = bool(row[0])
        if taken:
            db.execute("select pg_advisory_unlock(hashtext(%s))", (name,))
        return taken

    if not (free("spot_refresh") and free(NOTEBOOK_LOCK_NAME)):
        pytest.skip("spot_refresh or spot_notebook is held right now (a poll or a notebook runs)")
    ctx = setup(profile="marc", table_css=False)
    try:
        with ctx.api():
            assert free("spot_refresh"), "the notebook client is holding the poller's lock"
            assert not free(NOTEBOOK_LOCK_NAME)
        assert free(NOTEBOOK_LOCK_NAME)
    finally:
        ctx.close()


def test_setup_refuses_an_unregistered_profile_and_says_how_to_fix(db) -> None:
    from spotify_lakehouse.notebook import setup

    with pytest.raises(ConfigError, match="profiles.csv"):
        setup(profile="nobody_registered_this", table_css=False)


def test_setup_takes_the_profile_from_spot_profile(db, monkeypatch) -> None:
    """R-009: `make report-02 PROFILE=<slug>` reaches setup() through the environment."""
    from spotify_lakehouse.notebook import PROFILE_ENV, main, setup

    monkeypatch.setenv(PROFILE_ENV, "marc")
    ctx = setup(table_css=False)
    try:
        assert ctx.profile == "marc"
    finally:
        ctx.close()
    assert main() == 0
    monkeypatch.setenv(PROFILE_ENV, "nobody_registered_this")
    with pytest.raises(ConfigError, match=r"registered: .*marc"):
        setup(table_css=False)
    assert main() == 2
