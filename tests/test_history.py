"""03_history_profile helpers (spot-main-R-050): NULL is not zero, and the no-export path."""

from __future__ import annotations

from datetime import UTC, datetime

import matplotlib
import pandas as pd
import pytest

from spotify_lakehouse import history

matplotlib.use("Agg")


def _frame(ms: list[float | None]) -> pd.DataFrame:
    return pd.DataFrame({"ms_played": pd.Series(ms, dtype="float")})


def test_a_play_with_no_duration_is_not_a_play_of_zero_seconds() -> None:
    """The R-050 guard. §5.1 is about real zeros; NULL is the API saying nothing."""
    profile = history.duration_profile(_frame([1000.0, 0.0, 0.0, None, 5.0]))
    assert profile == {"rows": 5, "zero": 2, "null": 1, "positive": 2}
    # The two counts must never be folded together, in either direction.
    assert profile["zero"] != profile["rows"] - profile["positive"] - profile["null"] + 2
    assert history.duration_profile(_frame([None, None]))["zero"] == 0
    assert history.duration_profile(_frame([0.0, 0.0]))["null"] == 0


def test_long_tail_counts_items_at_each_threshold() -> None:
    plays = ["a"] * 5 + ["b", "b"] + ["c"]
    export = pd.DataFrame({"content_uri": plays})
    tail = history.long_tail(export, buckets=[1, 2, 5]).set_index("played_at_least")
    assert tail.loc[1, "distinct_items"] == 3
    assert tail.loc[2, "distinct_items"] == 2
    assert tail.loc[5, "distinct_items"] == 1
    assert tail.loc[5, "share_of_plays"] == pytest.approx(62.5)


def test_silent_stretches_reports_the_longest_gap_with_its_dates() -> None:
    times = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-06-01", "2020-06-02"], utc=True)
    gaps = history.silent_stretches(pd.DataFrame({"ended_at_utc": times}), top=2)
    # 2020 is a leap year: 2 Jan → 1 Jun is 151 days, not 150.
    assert gaps.loc[0, "gap_days"] == pytest.approx(151.0)
    assert str(gaps.loc[0, "from"].date()) == "2020-01-02"
    assert str(gaps.loc[0, "to"].date()) == "2020-06-01"


def test_other_oddities_names_what_it_found_or_returns_nothing() -> None:
    clean = pd.DataFrame(
        {
            "ms_played": [1000.0, 2000.0],
            "conn_country": ["US", "US"],
            "was_incognito": [False, False],
            "was_skipped": [False, False],
            "reason_end": ["trackdone", "trackdone"],
        }
    )
    assert history._other_oddities(clean).empty
    odd = clean.assign(
        ms_played=[history.MS_PER_HOUR * 3, 2000.0],
        was_incognito=[True, False],
        reason_end=["unknown", "trackdone"],
    )
    found = history._other_oddities(odd)
    assert set(found["what"]) == {
        "plays longer than 2 hours",
        "played in incognito mode",
        "reason_end = 'unknown'",
    }


def test_every_panel_renders_for_a_profile_with_no_export(db, monkeypatch) -> None:
    """R-009's rule: a profile with no export is told what is missing, not shown a traceback."""
    from spotify_lakehouse.config import mart_schema, session, stg_schema
    from spotify_lakehouse.notebook import NotebookContext

    written: list[str] = []
    monkeypatch.setattr(history, "_md", written.append)
    monkeypatch.setattr(
        history, "_show", lambda frame: written.append(str(getattr(frame, "shape", "")))
    )
    monkeypatch.setattr(history, "_callout", lambda kind, text: written.append(f"[{kind}] {text}"))

    def no_api(*args, **kwargs):
        raise AssertionError("03_history_profile must not call the Spotify API")

    monkeypatch.setattr(NotebookContext, "api", no_api)
    name = session()
    ctx = NotebookContext(
        profile="pytest_nobody_registered",
        session=name,
        stg_schema=stg_schema(name),
        mart_schema=mart_schema(name),
        started_at=datetime.now(UTC),
        conn=db,
        settings=None,
    )
    export = history.load_export(ctx)
    assert export.empty
    for panel in history.PANELS:
        panel(ctx, export)
    text = "\n".join(written)
    assert text.count(history.NO_DATA) >= len(history.PANELS) - 1
    assert "make load-export" in text


def test_load_export_keeps_null_and_zero_apart(db) -> None:
    """The distinction has to survive the round trip from the database, not just the helper."""
    from spotify_lakehouse.config import mart_schema, session, stg_schema
    from spotify_lakehouse.notebook import NotebookContext

    name = session()
    ctx = NotebookContext(
        profile="marc",
        session=name,
        stg_schema=stg_schema(name),
        mart_schema=mart_schema(name),
        started_at=datetime.now(UTC),
        conn=db,
        settings=None,
    )
    export = history.load_export(ctx)
    if export.empty:
        pytest.skip("no export loaded for marc in this database")
    profile = history.duration_profile(export)
    assert profile["rows"] == profile["positive"] + profile["zero"] + profile["null"]
    assert profile["zero"] > 0, "the export is known to carry zero-duration plays"
    # A NULL introduced into the loaded frame is counted as NULL and never folded into the zeros.
    blanked = export.copy()
    blanked.loc[export.index[export["ms_played"] > 0][:5], "ms_played"] = None
    after = history.duration_profile(blanked)
    assert after["null"] == profile["null"] + 5
    assert after["zero"] == profile["zero"]


def test_field_profile_never_shows_a_raw_platform_string(db) -> None:
    """`platform` can name a device model (R-009): §2 profiles it by family, never by value."""
    from spotify_lakehouse.config import mart_schema, session, stg_schema
    from spotify_lakehouse.notebook import NotebookContext

    name = session()
    ctx = NotebookContext(
        profile="marc",
        session=name,
        stg_schema=stg_schema(name),
        mart_schema=mart_schema(name),
        started_at=datetime.now(UTC),
        conn=db,
        settings=None,
    )
    profile = history.field_profile(ctx)
    row = profile[profile["field"] == "platform"]
    if row.empty:
        pytest.skip("no export loaded for marc in this database")
    rendered = " ".join(str(value) for value in row.iloc[0].to_dict().values())
    raw = [
        value
        for (value,) in ctx.rows(
            "select distinct platform from {stg}.stg_export__play_record "
            "where profile_slug = %s and platform is not null",
            (ctx.profile,),
        )
    ]
    families = {history.platform_family(value) for value in raw}
    leaked = [value for value in raw if value in rendered and value not in families]
    assert not leaked, f"{len(leaked)} raw platform strings reached the field profile"
