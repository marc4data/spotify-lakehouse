"""The playlist worksheet (Marc, 2026-10-09): blank columns, and duration from the payload."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from spotify_lakehouse import playlist_sheet


def test_duration_text_formats_minutes_and_blanks_the_unknown() -> None:
    assert playlist_sheet.duration_text(204128) == "3:24"
    assert playlist_sheet.duration_text(376480) == "6:16"
    assert playlist_sheet.duration_text(59000) == "0:59"
    assert playlist_sheet.duration_text(60000) == "1:00"
    # Unknown stays blank rather than becoming 0:00, which would read as a zero-length track.
    assert playlist_sheet.duration_text(None) == ""
    assert playlist_sheet.duration_text(float("nan")) == ""


def test_the_sheet_keeps_blank_columns_for_hand_annotation() -> None:
    """THE GUARD. The blank columns are the sheet's purpose, not an oversight.

    Marc asked for `key` and `rpm` so he could "create some metadata". Neither exists in this
    warehouse — measured, not assumed: 0 rows carry a `"tempo": <number>` field and `danceability`
    appears in 0 rows, and an audio-features payload always carries danceability beside tempo. So
    the columns ship empty for him to fill. A tidy-up that drops "empty" columns would quietly
    remove the thing the notebook was built for, and nothing else would fail.
    """
    assert "key" in playlist_sheet.ANNOTATION_COLUMNS
    assert "bpm" in playlist_sheet.ANNOTATION_COLUMNS
    for column in playlist_sheet.ANNOTATION_COLUMNS:
        assert column in playlist_sheet.SHEET_COLUMNS, f"{column} must reach the spreadsheet"
    # and they must be last, so the columns Marc types into sit together at the right-hand edge
    assert playlist_sheet.SHEET_COLUMNS[-len(playlist_sheet.ANNOTATION_COLUMNS) :] == (
        playlist_sheet.ANNOTATION_COLUMNS
    )


def test_coverage_counts_populated_cells_rather_than_present_columns() -> None:
    """`album` existing as a column says nothing about whether any row carries an album."""
    frame = pd.DataFrame(
        {
            "song": ["a", "b", "c", "d"],
            "album": ["X", None, "", None],
            "key": ["", "", "", ""],
        }
    )
    coverage = playlist_sheet.coverage(frame).set_index("column")
    assert coverage.loc["song", "populated"] == 4
    assert coverage.loc["album", "populated"] == 1, "None and '' are both unpopulated"
    assert coverage.loc["album", "pct"] == "25%"
    # the annotation columns are blank by design, so reporting them as 0% would be noise
    assert "key" not in coverage.index


def test_write_sheet_lands_a_csv_that_round_trips(tmp_path: Path) -> None:
    frame = pd.DataFrame({"#": [1, 2], "song": ["Low", "Nix Hex"], "key": ["", ""]})
    written = playlist_sheet.write_sheet(frame, "Dad's College Indie Pop/Rock", tmp_path)

    assert written.parent == tmp_path
    # a playlist name is not a filename: spaces, apostrophes and slashes all appear in real ones
    assert "/" not in written.name and "'" not in written.name
    assert written.name.endswith(".csv")
    back = pd.read_csv(written)
    assert list(back["song"]) == ["Low", "Nix Hex"]
    assert list(back.columns) == ["#", "song", "key"]


def test_a_playlist_with_nothing_loaded_returns_the_full_empty_frame(db) -> None:
    """An empty result must still carry every column, or §3 renders a frame with no headers."""
    from spotify_lakehouse.notebook import setup

    ctx = setup(profile="marc", table_css=False)
    try:
        frame = playlist_sheet.sheet(ctx, "no playlist is called this, 2026-10-09")
        assert frame.empty
        assert list(frame.columns) == list(playlist_sheet.SHEET_COLUMNS)
    finally:
        ctx.close()


def test_duration_comes_from_the_playlist_payload_not_dim_content(db) -> None:
    """THE OTHER GUARD, and the design decision worth protecting.

    `dim_content.duration_ms` is NULL for any track reaching it through the export branch, so a
    track the household has played loses the duration the playlist payload supplied — measured on
    `College Throwbacks`: 87 of 87 in the payload, 18 in the marts. The sheet therefore reads
    `int_playlist_content`. If someone simplifies that join away, duration silently drops to a
    fifth of its coverage and every row still renders.
    """
    from spotify_lakehouse.notebook import setup

    ctx = setup(profile="marc", table_css=False)
    try:
        name = ctx.scalar(
            "select playlist.playlist_name "
            "from {mart}.dim_playlist as playlist "
            "inner join {mart}.fct_playlist_membership as member "
            "  on member.playlist_key = playlist.playlist_key "
            "where playlist.is_current "
            "group by playlist.playlist_name "
            "having count(*) > 20 order by count(*) desc limit 1"
        )
        if name is None:
            pytest.skip("no playlist with loaded tracks in this warehouse")
        frame = playlist_sheet.sheet(ctx, name)
        assert not frame.empty
        missing = int(frame["duration_ms"].isna().sum())
        assert missing == 0, (
            f"{missing} of {len(frame)} tracks in {name!r} have no duration; the sheet is reading "
            "dim_content rather than the playlist payload"
        )
    finally:
        ctx.close()
