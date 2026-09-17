"""Playlist ingest and the three-tier comparison (spot-main-R-054).

The guard this round stages is `test_misses_are_directional`. §5 is explicit that `miss_count` is
directional, and a comparison that returns the same set both ways is broken — so a test that cannot
tell the two directions apart is not a test.
"""

from __future__ import annotations

import pandas as pd
import pytest

from spotify_lakehouse import playlists
from spotify_lakehouse.raw_store import FILE_KEY


def _frame(rows: list[tuple[str, str, str | None, str | None]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["artist", "title", "content_uri", "isrc"]).assign(
        content_match_key=lambda f: (
            f["title"].str.lower().str.strip() + "|" + f["artist"].str.lower().str.strip()
        )
    )


SMITH = _frame(
    [
        ("Aretha Franklin", "Respect", "spotify:track:aaa", "USA111"),
        ("The Band", "The Weight", "spotify:track:bbb", "USB222"),
        ("Sam Cooke", "Cupid", "spotify:track:ccc", None),
    ]
)
CONNOR = _frame(
    [
        ("The Band", "The Weight", "spotify:track:bbb", "USB222"),
        ("Otis Redding", "Try a Little Tenderness", "spotify:track:ddd", "USD444"),
    ]
)


def test_misses_are_directional() -> None:
    """THE GUARD (R-054). A→B and B→A are different questions with different answers."""
    forward = playlists.misses(
        SMITH, CONNOR, left_name="Smith", right_name="Connor", tier="content_uri"
    )
    backward = playlists.misses(
        CONNOR, SMITH, left_name="Connor", right_name="Smith", tier="content_uri"
    )
    assert forward.miss_count == 2  # Respect, Cupid
    assert backward.miss_count == 1  # Try a Little Tenderness
    assert forward.miss_count != backward.miss_count
    assert set(forward.rows["title"]) == {"Respect", "Cupid"}
    assert set(backward.rows["title"]) == {"Try a Little Tenderness"}
    # the two directions must never be the same set of rows
    assert set(forward.rows["title"]) != set(backward.rows["title"])
    # and each states which direction it is
    assert forward.label == "Tracks in Smith that are not in Connor"
    assert backward.label == "Tracks in Connor that are not in Smith"
    assert forward.label != backward.label


def test_every_output_names_its_direction() -> None:
    """§5: "47 misses between A and B" is meaningless. The label carries the direction."""
    table = playlists.tier_table(SMITH, CONNOR, left_name="Smith", right_name="Connor")
    assert len(table) == 3
    assert set(table["direction"]) == {"Tracks in Smith that are not in Connor"}
    assert "Smith" in table.loc[0, "direction"] and "Connor" in table.loc[0, "direction"]
    reverse = playlists.tier_table(CONNOR, SMITH, left_name="Connor", right_name="Smith")
    assert set(reverse["direction"]) == {"Tracks in Connor that are not in Smith"}


def test_tier_two_reports_how_little_it_covers() -> None:
    """ISRC covers API-resolved tracks only; a bare count would read as authoritative."""
    result = playlists.misses(SMITH, CONNOR, left_name="Smith", right_name="Connor", tier="isrc")
    assert result.left_with_key == 2  # Cupid has no ISRC
    assert "2 of 3 tracks in Smith carry a isrc" in result.coverage_note
    # a row with no key at this tier counts as a miss rather than being assumed present
    assert "Cupid" in set(result.rows["title"])


def test_loose_tier_matches_a_different_uri_for_the_same_recording() -> None:
    single = _frame([("The Band", "The Weight", "spotify:track:zzz", "USB222")])
    strict = playlists.misses(
        SMITH, single, left_name="Smith", right_name="Single", tier="content_uri"
    )
    loose = playlists.misses(
        SMITH, single, left_name="Smith", right_name="Single", tier="content_match_key"
    )
    assert strict.miss_count == 3  # a re-issued URI reads as a miss at tier 1
    assert loose.miss_count == 2  # tier 3 sees it is the same recording
    assert "The Weight" not in set(loose.rows["title"])


def test_resolve_one_never_guesses() -> None:
    frame = pd.DataFrame({"playlist_name": ["Smith Family", "Connor Road", "smith beach"]})
    with pytest.raises(playlists.AmbiguousPlaylist, match="matched 2"):
        playlists.resolve_one(frame, "smith")
    with pytest.raises(playlists.AmbiguousPlaylist, match="matched 0"):
        playlists.resolve_one(frame, "nobody")
    assert playlists.resolve_one(frame, "connor") == "Connor Road"


def test_an_unknown_tier_is_an_error_not_a_silent_default() -> None:
    with pytest.raises(ValueError, match="not one of"):
        playlists.misses(SMITH, CONNOR, left_name="a", right_name="b", tier="title")


def test_page_keys_are_accepted_by_write_response() -> None:
    """`write_response` rejects a key that is not letters and digits; pages must never trip it."""
    assert FILE_KEY.fullmatch(playlists.page_key("3cEYpjA9oz9GiPac4AsH4n", 1))
    assert FILE_KEY.fullmatch(playlists.page_key(None, 12))


def test_empty_playlists_compare_without_raising() -> None:
    empty = SMITH.iloc[0:0]
    result = playlists.misses(
        empty, CONNOR, left_name="Empty", right_name="Connor", tier="content_uri"
    )
    assert result.miss_count == 0
    assert result.coverage_note == "no tracks"
    both_ways = playlists.misses(
        CONNOR, empty, left_name="Connor", right_name="Empty", tier="content_uri"
    )
    assert both_ways.miss_count == len(CONNOR)  # everything is a miss against nothing


def test_a_unique_substring_match_is_not_silently_treated_as_a_name_match() -> None:
    """R-054, measured: "Connor" is inside "O'Connor".

    The match is unique, so nothing raises — and the notebook went on to answer a question nobody
    asked. Uniqueness is not correctness; the defence is that the result says it was inexact.
    """
    frame = pd.DataFrame(
        {"playlist_name": ["Smith", "The Emperor's New Clothes - Sinead O'Connor 2", "Jazz"]}
    )
    exact = playlists.resolve(frame, "Smith")
    assert exact.exact and exact.name == "Smith"
    assert exact.warning is None

    loose = playlists.resolve(frame, "Connor")
    assert loose.name == "The Emperor's New Clothes - Sinead O'Connor 2"
    assert loose.exact is False, "a substring hit must never be reported as a name match"
    assert loose.warning is not None and "substring" in loose.warning
    assert loose.candidates == ["The Emperor's New Clothes - Sinead O'Connor 2"]


def test_an_exact_name_wins_over_a_longer_substring_match() -> None:
    frame = pd.DataFrame({"playlist_name": ["Connor", "Sinead O'Connor favourites"]})
    match = playlists.resolve(frame, "connor")
    assert match.exact and match.name == "Connor"
    assert len(match.candidates) == 2, "both are candidates; the exact one wins"


def test_a_typographic_apostrophe_matches_an_ascii_one() -> None:
    """R-054, Marc 2026-09-16. His playlist is `Connor\u2019s Playlist`, typed on a phone.

    Searching for `Connor's Playlist` with an ASCII apostrophe matched zero playlists, and zero
    matches is indistinguishable from "no such playlist" — the wrong answer to give when it exists.
    """
    frame = pd.DataFrame({"playlist_name": ["Connor\u2019s Playlist", "Smith", "Jazz"]})
    match = playlists.resolve(frame, "Connor's Playlist")
    assert match.exact, "an apostrophe variant is still an exact name match"
    assert match.name == "Connor\u2019s Playlist"
    # and the reverse direction: the stored ASCII form found by a typographic query
    ascii_frame = pd.DataFrame({"playlist_name": ["Connor's Playlist"]})
    assert playlists.resolve(ascii_frame, "Connor\u2019s Playlist").exact
    assert playlists.normalise_name("Connor\u2019S  ") == "connor's"


# --- splitting the misses: absent vs merely re-housed (spot-main-R-059) -------------------------


def _split_frame(rows):
    """artist, title, album, content_uri, isrc — the shape load_membership returns."""
    frame = pd.DataFrame(rows, columns=["artist", "title", "album", "content_uri", "isrc"])
    return frame.assign(
        content_match_key=lambda f: (
            f["title"].str.lower().str.strip() + "|" + f["artist"].str.lower().str.strip()
        )
    )


LEFT = _split_frame(
    [
        ("Los Lonely Boys", "Heaven", "Very Best Of", "spotify:track:aaa", "USSM10315916"),
        ("Aretha Franklin", "Respect", "I Never Loved", "spotify:track:bbb", "USA111"),
        ("JAY-Z", "Otis", "Watch The Throne", "spotify:track:ccc", "USUM71111634"),
    ]
)
RIGHT = _split_frame(
    [
        ("Los Lonely Boys", "Heaven", "Los Lonely Boys", "spotify:track:zzz", "USSM10315916"),
        ("JAŸ-Z", "Otis", "Watch The Throne", "spotify:track:yyy", "USUM71111634"),
    ]
)


def test_list_a_and_list_b_partition_the_tier_one_misses() -> None:
    """THE GUARD (R-059). Disjoint, and together exactly the tier-1 miss set.

    Nesting is NOT the invariant to guard: R-059 measured the ladder as non-nested, so a test
    asserting it would fail on correct code and prove nothing. What must always hold is that a
    track is either absent or re-housed, never both and never neither.
    """
    tier_one = playlists.misses(LEFT, RIGHT, left_name="L", right_name="R", tier="content_uri")
    split = playlists.split_misses(LEFT, RIGHT, left_name="L", right_name="R")

    absent = set(split.absent["content_uri"])
    rehoused = set(split.rehoused["content_uri"])
    assert absent & rehoused == set(), "a track cannot be both absent and re-housed"
    assert absent | rehoused == set(tier_one.rows["content_uri"]), "the two must cover the misses"
    assert len(split.absent) + len(split.rehoused) == tier_one.miss_count


def test_the_split_puts_a_different_album_pressing_in_list_b() -> None:
    """Heaven: same ISRC, different URI, different album — re-housed, not absent."""
    split = playlists.split_misses(LEFT, RIGHT, left_name="L", right_name="R")
    assert "Heaven" in set(split.rehoused["title"])
    assert "Heaven" not in set(split.absent["title"])
    row = split.rehoused[split.rehoused["title"] == "Heaven"].iloc[0]
    assert row["album"] == "Very Best Of"
    assert row["album_other_side"] == "Los Lonely Boys", "the other side's album must be joined on"
    assert "isrc" in row["matched_tier"]


def test_a_track_on_neither_side_is_absent_not_rehoused() -> None:
    split = playlists.split_misses(LEFT, RIGHT, left_name="L", right_name="R")
    assert "Respect" in set(split.absent["title"])
    assert "Respect" not in set(split.rehoused["title"])


def test_the_tier_ladder_is_not_nested() -> None:
    """R-059's finding, pinned. An ISRC can match where the normalized name key does not.

    `JAY-Z` on one side and `JAŸ-Z` on the other produce different tier-3 keys while sharing an
    ISRC, so tier 3 — the *loose* tier — misses what tier 2 matches. data-contracts §5's "most
    reliable first" ladder is therefore an ordering of reliability, not of containment.
    """
    by_isrc = playlists.misses(LEFT, RIGHT, left_name="L", right_name="R", tier="isrc")
    by_name = playlists.misses(LEFT, RIGHT, left_name="L", right_name="R", tier="content_match_key")
    isrc_misses = set(by_isrc.rows["title"])
    name_misses = set(by_name.rows["title"])
    assert "Otis" in name_misses, "the diaeresis breaks the name key"
    assert "Otis" not in isrc_misses, "but the ISRC matches"
    assert not name_misses <= isrc_misses, "the loose tier is not a superset of the strict one"


def test_an_empty_left_side_splits_into_two_empty_lists() -> None:
    split = playlists.split_misses(LEFT.iloc[0:0], RIGHT, left_name="L", right_name="R")
    assert len(split.absent) == 0 and len(split.rehoused) == 0
