from __future__ import annotations

import pytest

from spotify_lakehouse.cli import build_parser


def _probe(*extra: str):
    return build_parser().parse_args(["probe", "--profile", "marc", *extra])


def test_follow_next_absent_means_no_extra_calls() -> None:
    assert _probe().follow_next == 0


def test_bare_follow_next_means_one() -> None:
    assert _probe("--follow-next").follow_next == 1


def test_follow_next_accepts_up_to_the_cap() -> None:
    assert _probe("--follow-next", "3").follow_next == 3
    assert _probe("--follow-next", "5").follow_next == 5


@pytest.mark.parametrize("value", ["0", "6", "-1", "three"])
def test_follow_next_out_of_range_is_a_usage_error(value: str) -> None:
    with pytest.raises(SystemExit) as exc:
        _probe("--follow-next", value)
    assert exc.value.code == 2
