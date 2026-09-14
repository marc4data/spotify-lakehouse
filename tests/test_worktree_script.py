"""scripts/worktree.sh refuses bad names before touching git (spot-main-R-033). No side effects."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "worktree.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=REPO, capture_output=True, text=True, check=False
    )


def _worktrees() -> str:
    return subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=REPO, capture_output=True, text=True
    ).stdout


@pytest.mark.parametrize("action", ["add", "remove"])
@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "NAME is required"),
        ("wtab", "not a session token"),
        ("WTC", "not a session token"),
        ("../evil", "not a session token"),
        ("feat", "not a session token"),
        ("main", "primary checkout"),
    ],
)
def test_bad_names_are_refused_before_any_git_action(action: str, name: str, message: str) -> None:
    before = _worktrees()
    result = _run(action, name)
    assert result.returncode == 1
    assert message in result.stderr
    assert _worktrees() == before
    assert not (REPO.parent / f"{REPO.name}-{name}").exists() or name == ""


def test_unknown_action_prints_usage() -> None:
    result = _run("frobnicate", "wtc")
    assert result.returncode == 1
    assert "usage:" in result.stderr


def test_removing_a_worktree_that_does_not_exist_is_refused() -> None:
    result = _run("remove", "wtz")
    assert result.returncode == 1
    assert "no worktree is registered" in result.stderr
