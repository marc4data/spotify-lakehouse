"""Tableau Public extracts (spot-main-R-007).

The guard this round stages is `test_no_extract_carries_a_device_string`: flip the platform
exclusion off and it goes red. The rest are positive controls — R-052 F3 established that
"0 found" and "the field was never written" look identical, so every exclusion proves both
absence *and* that the row count moved the way the exclusion predicts.
"""

from __future__ import annotations

import pandas as pd
import pytest

from spotify_lakehouse import publish
from spotify_lakehouse.data_inventory import must_redact


def _options(**kwargs) -> publish.Options:
    return publish.Options(profile="marc", **kwargs)


def test_allocation_tag_type_is_read_from_dbt_not_copied() -> None:
    """A second copy of this value drifts the moment the dbt var changes (R-052's rule)."""
    assert publish.allocation_tag_type() == "genre"
    assert publish.DBT_PROJECT.exists()


def test_no_extract_carries_a_device_string(db) -> None:
    """THE GUARD. `platform` names a device (R-052 F1); a public workbook must never carry it."""
    opts = _options()
    for name, build in publish.EXTRACTS.items():
        frame = build(db, opts)
        assert "platform" not in frame.columns, f"{name} carries a raw platform column"
        for column in frame.columns:
            assert not must_redact(column), (
                f"{name}.{column} is redacted-class and must not publish"
            )


def test_every_option_actually_changes_the_output(db) -> None:
    """R-007 F4. A flag that changes nothing is worse than no flag: it reads as a control.

    `include_platform_family` and `include_conn_country` were both inert when first written, and
    the PII battery could not tell "excluded" from "never built" (R-052 F3). This is what tells it.
    """
    base = publish.plays_daily(db, _options())
    with_country = publish.plays_daily(db, _options(include_conn_country=True))
    with_family = publish.plays_daily(db, _options(include_platform_family=True))
    kept = publish.plays_daily(db, _options(exclude_incognito=False))

    # R-057 inverted this default. THIS is the assertion covering the new default: it fails if
    # conn_country creeps back into the default build, and the one below fails if the flag goes
    # inert — the two failure modes R-007 F4 could not tell apart.
    assert "conn_country" not in base.columns, "R-057: the default build carries no conn_country"
    assert "conn_country" in with_country.columns, "--conn-country must still put it back"
    assert len(with_country) != len(base), "conn_country extends the grain, so the row count moves"
    assert "platform_family" in with_family.columns
    assert "platform_family" not in base.columns
    assert "platform" not in with_family.columns, "the family, never the device string"
    assert kept["allocated_plays"].sum() > base["allocated_plays"].sum()


def test_no_extract_column_is_identifier_shaped(db) -> None:
    """R-052's `must_redact` applied to extract columns, as the round's §4 requires."""
    opts = _options()
    for name, build in publish.EXTRACTS.items():
        for column in build(db, opts).columns:
            assert not column.endswith(("_id", "_uri", "_mbid")), f"{name}.{column}"


def test_incognito_exclusion_removes_exactly_the_flagged_plays(db) -> None:
    """Positive control: the field is absent AND the row count moved by the predicted amount."""
    with db.cursor() as cur:
        cur.execute(
            publish._compose(
                "select count(*) from {stg}.stg_export__play_record "
                "where profile_slug = %s and was_incognito"
            ),
            ("marc",),
        )
        flagged = cur.fetchone()[0]
    if not flagged:
        pytest.skip("no incognito rows in this database")

    # `allocated_plays` is the additive measure: its weights sum to 1.0 per play, so summing it
    # across buckets counts plays. `plays` is per-bucket and would double-count (R-007 F3).
    kept = publish.plays_daily(db, _options(exclude_incognito=False))["allocated_plays"].sum()
    excluded = publish.plays_daily(db, _options(exclude_incognito=True))["allocated_plays"].sum()
    assert round(float(kept - excluded)) == flagged, (
        f"excluding incognito changed the play count by {kept - excluded}, expected {flagged}"
    )


def test_daily_allocation_sums_to_the_monthly_model(db) -> None:
    """The extract re-expresses the §4 chain at day grain; this is what stops it drifting."""
    daily = publish.plays_daily(db, _options(exclude_incognito=False))
    if daily.empty:
        pytest.skip("no plays for marc in this database")
    music = daily[
        (daily["content_type"] == "track") & (daily["bucket_name"] != publish.UNCLASSIFIED)
    ]
    by_month = (
        music.assign(month=pd.to_datetime(music["play_date"]).dt.to_period("M"))
        .groupby(["month", "bucket_name"])["allocated_ms"]
        .sum(min_count=1)  # an all-NULL group stays NULL rather than becoming 0.0
    )
    with db.cursor() as cur:
        cur.execute(
            publish._compose(
                "select date_trunc('month', month_start)::date, bucket_name, sum(allocated_ms) "
                "from {stg}.int_genre_bucket_allocation where profile_slug = %s group by 1, 2"
            ),
            ("marc",),
        )
        rows = cur.fetchall()
    # allocated_ms is NULL where every contributing play is an API row (no duration), so the
    # comparison has to be NULL-safe on both sides rather than coercing to a number.
    expected = {(pd.Period(month, freq="M"), bucket): ms for month, bucket, ms in rows}
    assert len(expected) > 0
    compared = 0
    for key, value in expected.items():
        assert key in by_month.index, f"{key} missing from the daily extract"
        actual = by_month.loc[key]
        if value is None:
            assert pd.isna(actual), f"{key}: model has NULL, extract has {actual}"
            continue
        # psycopg returns numeric as Decimal; compare like with like rather than mixing types.
        assert float(actual) == pytest.approx(float(value), abs=1e-3), key
        compared += 1
    assert compared > 0, "every group was NULL; the comparison proved nothing"


def test_artists_does_not_fan_out_on_the_artist_join(db) -> None:
    """One play must count once: br_content_artist could in principle give two primaries."""
    frame = publish.artists(db, _options(exclude_incognito=False))
    if frame.empty:
        pytest.skip("no plays for marc in this database")
    with db.cursor() as cur:
        cur.execute(
            publish._compose(
                "select count(*) from {mart}.fct_play_event as fct "
                "join {mart}.dim_profile as p on p.profile_key = fct.profile_key "
                "join {mart}.dim_content as c on c.content_key = fct.content_key "
                "where p.profile_slug = %s and c.content_type = 'track'"
            ),
            ("marc",),
        )
        expected = cur.fetchone()[0]
    assert int(frame["plays"].sum()) == expected


def test_coverage_matches_int_allocation_reconciliation_exactly(db) -> None:
    frame = publish.coverage(db, _options())
    with db.cursor() as cur:
        cur.execute(
            publish._compose(
                "select step_number, step_name, play_count, ms_played "
                "from {stg}.int_allocation_reconciliation where profile_slug = %s "
                "order by step_number"
            ),
            ("marc",),
        )
        rows = cur.fetchall()
    if not rows:
        pytest.skip("no reconciliation rows for marc")
    assert len(frame) == len(rows) == 4
    for (number, name, plays, ms), (_, row) in zip(rows, frame.iterrows(), strict=True):
        assert row["step_number"] == number
        assert row["step_name"] == name
        assert row["play_count"] == plays
        assert row["ms_played"] == ms


def test_genres_carries_the_unclassified_residual_as_a_row(db) -> None:
    """A share renormalised over allocated time only would read as full coverage."""
    frame = publish.genres(db, _options())
    if frame.empty:
        pytest.skip("no plays for marc in this database")
    assert publish.UNCLASSIFIED in set(frame["bucket_name"])


def test_write_extracts_writes_four_csvs_and_uploads_nothing(db, tmp_path) -> None:
    results = publish.write_extracts(db, _options(), out_dir=tmp_path)
    assert {r.name for r in results} == {"plays_daily", "artists", "genres", "coverage"}
    for result in results:
        assert result.path.exists() and result.path.suffix == ".csv"
        head = result.path.read_text(encoding="utf-8").splitlines()[0]
        assert "," in head
        assert "platform" not in head


def test_manifest_lists_every_column_and_every_exclusion(db, tmp_path) -> None:
    results = publish.write_extracts(db, _options(), out_dir=tmp_path)
    table = publish.manifest(results)
    for result in results:
        for column in result.columns:
            assert ((table["file"] == result.path.name) & (table["column"] == column)).any()
    assert (table["included"] == "NO").sum() == len(publish.EXCLUDED_FIELDS)
    excluded = table[table["included"] == "NO"]
    assert excluded["note"].str.len().gt(20).all(), "every exclusion states a reason"


def test_coverage_published_columns_are_measured_not_derived(db) -> None:
    """R-057, answering R-007 F2. The published figures must come from the extract's own query path.

    If they were computed by subtracting the exclusions from the warehouse totals they would be a
    restatement of those totals, and any error in the extracts would cancel out instead of showing.
    """
    frame = publish.coverage(db, _options())
    if frame.empty:
        pytest.skip("no reconciliation rows for marc")
    for column in ("published_play_count", "published_ms_played", "published_rows_with_duration"):
        assert column in frame.columns, column

    # step 1's published count must equal what plays_daily actually publishes, independently built
    daily = publish.plays_daily(db, _options())
    step_one = frame.loc[frame["step_number"] == 1].iloc[0]
    assert int(step_one["published_play_count"]) == int(round(daily["allocated_plays"].sum()))

    # and it must be strictly smaller than the warehouse total, because incognito plays are excluded
    assert int(step_one["published_play_count"]) < int(step_one["play_count"])

    # the warehouse columns are untouched: the identity test above still owns them
    assert step_one["step_name"] == "total_listening"


def test_conn_country_is_absent_from_every_written_file(db, tmp_path) -> None:
    """The default build writes no country column — checked on the files, not the frames."""
    results = publish.write_extracts(db, _options(), out_dir=tmp_path)
    for result in results:
        header = result.path.read_text(encoding="utf-8").splitlines()[0]
        assert "conn_country" not in header, f"{result.path.name}: {header}"
    # positive control: with the flag on it IS written, so the absence above is real
    with_country = publish.write_extracts(
        db, _options(include_conn_country=True), out_dir=tmp_path / "on"
    )
    headers = [r.path.read_text(encoding="utf-8").splitlines()[0] for r in with_country]
    assert any("conn_country" in header for header in headers), headers
