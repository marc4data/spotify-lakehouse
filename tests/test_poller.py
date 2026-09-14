from __future__ import annotations

from datetime import UTC, datetime

from spotify_lakehouse.poller import Gap, Window, detect_gap, window_of


def _at(hms: str) -> datetime:
    return datetime.fromisoformat(f"2026-09-14T{hms}+00:00")


def _page(*played_at: str) -> dict:
    return {"items": [{"played_at": f"2026-09-14T{value}Z"} for value in played_at]}


def test_window_of_reads_oldest_newest_and_count() -> None:
    window = window_of(_page("10:00:00.500", "08:30:00.000", "09:15:00.250"))
    assert window == Window(3, _at("08:30:00.000"), _at("10:00:00.500"))
    assert window.oldest is not None and window.oldest.tzinfo is not None


def test_window_of_empty_page() -> None:
    assert window_of({"items": []}) == Window(0, None, None)
    assert window_of({}) == Window(0, None, None)


# --- The guard this round exists for (spot-main-R-017, required test 1). -------------------------


def test_gap_recorded_when_window_starts_after_the_high_water_mark() -> None:
    previous = _at("07:00:00.000")
    window = window_of(_page("09:00:00.000", "08:00:00.000"))
    assert detect_gap(previous, window) == Gap(previous, _at("08:00:00.000"), 2)


def test_no_gap_when_window_overlaps_the_high_water_mark() -> None:
    window = window_of(_page("09:00:00.000", "08:00:00.000"))
    assert detect_gap(_at("08:30:00.000"), window) is None


def test_no_gap_when_oldest_play_is_the_high_water_mark_itself() -> None:
    window = window_of(_page("09:00:00.000", "08:00:00.000"))
    assert detect_gap(_at("08:00:00.000"), window) is None


def test_no_gap_without_an_earlier_capture_or_with_an_empty_window() -> None:
    assert detect_gap(None, window_of(_page("09:00:00.000"))) is None
    assert detect_gap(datetime(2026, 9, 14, tzinfo=UTC), window_of({"items": []})) is None
