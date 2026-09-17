"""Integration tests for spotify_lakehouse.notebook.setup against shared Postgres (read-only)."""

from __future__ import annotations

import pytest

from spotify_lakehouse.config import ConfigError


def test_aligned_table_html_puts_strings_left_and_numbers_right() -> None:
    """Marc, 2026-09-17. Booleans and timestamps count as strings here, not numbers."""
    import pandas as pd

    from spotify_lakehouse.notebook import aligned_table_html

    frame = pd.DataFrame(
        {
            "artist": ["Drake", "JAY-Z"],
            "plays": [12, 7],
            "share": [0.5, 0.25],
            "is_public": [True, False],
        }
    )
    html = aligned_table_html(frame)
    assert html is not None

    styles = html[html.index("<style") : html.index("</style>")]
    left = {block for block in styles.split("}") if "left !important" in block}
    right = {block for block in styles.split("}") if "right !important" in block}
    assert left and right, "both alignments must be emitted"
    # col0 is artist, col3 is is_public: left. col1/col2 are the numbers: right.
    assert any("row0_col0" in block for block in left)
    assert any("row0_col3" in block for block in left)
    assert any("row0_col1" in block for block in right)
    assert any("row0_col2" in block for block in right)
    # No cell may carry two competing declarations.
    for block in styles.split("}"):
        assert block.count("text-align") <= 1, f"contradictory rule: {block}"


def test_aligned_table_html_keeps_the_class_nb2report_detects() -> None:
    """nb2report finds tables with a plain `'dataframe' in html` test (parser.py:178).

    Styler drops `class="dataframe"` unless it is pinned back, and losing it would cost the report
    its table wrapper and column sorting without any error to notice.
    """
    import pandas as pd

    from spotify_lakehouse.notebook import aligned_table_html

    html = aligned_table_html(pd.DataFrame({"a": ["x"], "n": [1]}))
    assert html is not None
    assert 'class="dataframe"' in html
    assert "dataframe" in html


def test_aligned_table_html_declines_rather_than_raising_on_duplicate_columns() -> None:
    """THE GUARD. `Styler.apply`/`.map` raise KeyError on a non-unique index — measured, not feared.

    `_repr_html_` runs on every DataFrame displayed, and duplicate column names arrive from ordinary
    merges and `pd.concat(axis=1)`. A raise there breaks the cell's entire output, which is a far
    worse failure than misaligned text, so this must decline and let pandas render it.
    """
    import pandas as pd

    from spotify_lakehouse.notebook import aligned_table_html

    frame = pd.DataFrame([[1, "x"]], columns=["a", "a"])
    assert aligned_table_html(frame) is None

    duplicate_index = pd.DataFrame({"a": [1, 2]}, index=[0, 0])
    assert aligned_table_html(duplicate_index) is None


def test_aligned_table_html_declines_a_frame_pandas_would_truncate() -> None:
    """Styler ignores display.max_rows, so styling a long frame would render all of it."""
    import pandas as pd

    from spotify_lakehouse.notebook import aligned_table_html

    max_rows = pd.get_option("display.max_rows")
    assert aligned_table_html(pd.DataFrame({"a": range(max_rows + 1)})) is None
    assert aligned_table_html(pd.DataFrame({"a": range(3)})) is not None


def test_installing_the_alignment_is_idempotent_and_falls_back() -> None:
    """Re-running a setup cell must not wrap the wrapper, and the fallback must stay reachable."""
    import pandas as pd

    from spotify_lakehouse.notebook import _install_dataframe_alignment

    original = pd.DataFrame._repr_html_
    try:
        _install_dataframe_alignment()
        once = pd.DataFrame._repr_html_
        _install_dataframe_alignment()
        assert pd.DataFrame._repr_html_ is once, "second install must be a no-op"

        # A frame the styler declines still renders, via pandas' own path.
        declined = pd.DataFrame([[1, "x"]], columns=["a", "a"])._repr_html_()
        assert "<table" in declined
        styled = pd.DataFrame({"a": ["x"], "n": [1]})._repr_html_()
        assert "right !important" in styled
    finally:
        pd.DataFrame._repr_html_ = original


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
