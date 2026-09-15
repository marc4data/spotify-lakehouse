"""02_listening_patterns helpers (spot-main-R-009): clipping, coverage, phase, no-data path."""

from __future__ import annotations

from datetime import UTC, date, datetime

import matplotlib
import numpy as np
import pandas as pd
import pytest

from spotify_lakehouse import listening

matplotlib.use("Agg")


def test_clip_to_window_drops_outside_periods_and_flags_partial_edges() -> None:
    """The R-009 guard: an export ending mid-month keeps that month, flagged, and nothing after."""
    months = pd.DataFrame(
        {"period": pd.date_range("2024-01-01", periods=6, freq="MS"), "hours": range(6)}
    )
    out = listening.clip_to_window(months, "month", (date(2024, 2, 15), date(2024, 5, 10)))
    assert out["period"].dt.month.tolist() == [2, 3, 4, 5]
    assert out["is_partial"].tolist() == [True, False, False, True]

    weeks = pd.DataFrame({"period": pd.date_range("2024-01-01", periods=4, freq="7D")})
    clipped = listening.clip_to_window(weeks, "week", (date(2024, 1, 3), date(2024, 1, 21)))
    assert clipped["period"].dt.day.tolist() == [1, 8, 15]  # Mondays; the 22nd starts after
    assert clipped["is_partial"].tolist() == [True, False, False]  # the 21st is a Sunday


def test_time_series_keeps_silent_periods_as_zero_and_edges_partial() -> None:
    plays = pd.DataFrame(
        {
            "full_date": pd.to_datetime(["2024-01-20", "2024-03-05", "2024-03-06"]),
            "ended_at_utc": pd.to_datetime(["2024-01-20", "2024-03-05", "2024-03-06"]),
            "ms_played": [3_600_000.0, 1_800_000.0, np.nan],
            "is_qualified_play": [True, True, False],
            "was_skipped": [False, False, True],
            "was_shuffled": [False, True, False],
            "was_offline": [False, False, False],
        }
    )
    series = listening.time_series(plays, "month", (date(2024, 1, 15), date(2024, 3, 31)))
    assert series["period"].dt.month.tolist() == [1, 2, 3]
    assert series["hours"].tolist() == [1.0, 0.0, 0.5]  # February had no plays: zero, not missing
    assert series["is_partial"].tolist() == [True, False, False]
    assert series.loc[2, "pct_rows_with_duration"] == 50.0


def test_with_coverage_keeps_time_null_when_no_row_carries_a_duration() -> None:
    """data-contracts §2: a NULL that propagates is a correct signal; a fabricated zero is not."""
    plays = pd.DataFrame(
        {
            "group": ["a", "a", "b", "b"],
            "ended_at_utc": pd.to_datetime(["2024-01-01"] * 4),
            "ms_played": [1_000.0, np.nan, np.nan, np.nan],
            "is_qualified_play": [False] * 4,
            "was_skipped": [False] * 4,
            "was_shuffled": [False] * 4,
            "was_offline": [False] * 4,
        }
    )
    out = listening.with_coverage(plays, ["group"]).set_index("group")
    assert out.loc["a", "ms_played"] == 1_000.0
    assert out.loc["a", "pct_rows_with_duration"] == 50.0
    assert pd.isna(out.loc["b", "ms_played"]) and pd.isna(out.loc["b", "hours"])
    assert out.loc["b", "play_count"] == 2 and out.loc["b", "pct_rows_with_duration"] == 0.0


def test_grain_is_weekly_beyond_two_years() -> None:
    assert listening.grain_for((date(2014, 2, 24), date(2026, 9, 13))) == "week"
    assert listening.grain_for((date(2025, 1, 1), date(2026, 1, 1))) == "day"


def test_circular_mean_hour_wraps_midnight() -> None:
    mean = listening.circular_mean_hour([23.0, 1.0])
    assert min(mean, 24 - mean) < 1e-9
    assert listening.circular_mean_hour([9.0, 11.0]) == pytest.approx(10.0)
    assert listening.hour_difference(1.0, 23.0) == pytest.approx(2.0)


def test_a_two_month_shift_is_reported_as_dates_against_its_neighbours() -> None:
    rows = []
    for month in pd.date_range("2020-01-01", periods=36, freq="MS"):
        hour = 18 if month in (pd.Timestamp("2021-01-01"), pd.Timestamp("2021-02-01")) else 20
        for day in range(1, 26):
            for minute in (0, 30, 45, 50, 55, 59):
                rows.append(
                    {
                        "full_date": month + pd.Timedelta(days=day - 1),
                        "hour": hour,
                        "minute": minute,
                    }
                )
    runs = listening.shift_runs(listening.monthly_phase(pd.DataFrame(rows)))
    assert len(runs) == 1
    run = runs.iloc[0]
    assert (run["first_month"], run["last_month"]) == (
        pd.Timestamp("2021-01-01"),
        pd.Timestamp("2021-02-01"),
    )
    assert run["months"] == 2 and run["mean_shift_hours"] < -1.5
    assert list(runs.columns) == listening.RUN_COLUMNS  # dates and sizes; no place column exists


@pytest.mark.parametrize(
    ("raw", "family"),
    [
        ("Windows 10 (10.0.19045; x64)", "Windows"),
        ("Android OS 13 API 33 (Google, Pixel 7)", "Android"),
        ("iOS 17.1 (iPhone15,2)", "iOS"),
        ("OS X 10.15 [x86 8]", "other"),
        ("osx", "macOS"),
        ("Partner amazon_echo", "connected device"),
        ("not_applicable", "not recorded"),
        (None, "not recorded"),
    ],
)
def test_platform_family(raw: str | None, family: str) -> None:
    assert listening.platform_family(raw) == family


def test_every_panel_renders_for_a_profile_with_no_data_and_calls_no_api(db, monkeypatch) -> None:
    """R-009: a profile without an export gets told what is missing, not a traceback."""
    from spotify_lakehouse.config import mart_schema, session, stg_schema
    from spotify_lakehouse.notebook import NotebookContext

    def no_api(*args, **kwargs):
        raise AssertionError("02_listening_patterns must not call the Spotify API")

    monkeypatch.setattr(NotebookContext, "api", no_api)
    monkeypatch.setattr("spotify_lakehouse.api.SpotifyClient.for_profile", no_api)
    written: list[str] = []
    monkeypatch.setattr(listening, "_md", written.append)
    monkeypatch.setattr(listening, "_show", lambda frame: written.append(frame.to_string()))
    monkeypatch.setattr(
        listening, "_callout", lambda kind, text: written.append(f"[{kind}] {text}")
    )
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
    cov = listening.load_coverage(ctx)
    plays = listening.load_plays(ctx)
    assert cov.window is None and plays.empty
    listening.emit_intro(ctx, plays, cov)
    for panel in listening.PANELS:
        panel(ctx, plays, cov)
    text = "\n".join(written)
    assert text.count(listening.NO_DATA) >= len(listening.PANELS) - 1
