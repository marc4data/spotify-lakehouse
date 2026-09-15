"""The `spot-hex32` rule in .gitleaks.toml, exercised the way gitleaks applies it (spot-main-R-020).

gitleaks finds every non-overlapping match of a rule's regex in a fragment, then drops a match whose
line matches the rule's line allowlist. Python's `re` behaves the same for this pattern (no
lookarounds, RE2-compatible syntax), so these tests pin the rule's behaviour in CI without a
gitleaks binary. The same layouts were measured against real gitleaks v8.30.0 in R-020's scratch
repos.

Synthetic values are built at runtime so no credential-shaped literal exists in this file.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[1] / ".gitleaks.toml"
A = "deadbeef" * 4
B = "0123456789abcdef" * 2
PRAGMA = "spot: allow-secret-shape"


def _rule() -> tuple[re.Pattern[str], list[re.Pattern[str]]]:
    rules = {rule["id"]: rule for rule in tomllib.loads(CONFIG.read_text())["rules"]}
    rule = rules["spot-hex32"]
    line_allow = [
        re.compile(pattern)
        for allowlist in rule.get("allowlists", [])
        if allowlist.get("regexTarget") == "line"
        for pattern in allowlist.get("regexes", [])
    ]
    return re.compile(rule["regex"]), line_allow


def findings(text: str) -> list[int]:
    """1-based line numbers gitleaks would report for `text` under spot-hex32."""
    regex, line_allow = _rule()
    lines = text.split("\n")
    reported = []
    for match in regex.finditer(text):
        line_number = text.count("\n", 0, match.start(1)) + 1
        if any(allow.search(lines[line_number - 1]) for allow in line_allow):
            continue
        reported.append(line_number)
    return reported


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"hello\n{A}\n", [2]),
        (f"{A}\n", [1]),
        (f"{A}\n{B}\n", [1, 2]),
        (f"SPOTIFY_CLIENT_ID={A}\n{B}\n", [1, 2]),
        (f"SPOTIFY_CLIENT_SECRET={B}\n", [1]),
        (f"{A}{A}\n", []),  # 64 hex is not the credential shape
        ("nothing secret here\n", []),
    ],
    # Explicit ids: otherwise pytest prints the synthetic values in public CI logs on a failure.
    ids=[
        "bare-after-plain-line",
        "bare-first-line",
        "two-bare-lines",
        "bare-after-client-id",
        "client-secret",
        "sixty-four-hex",
        "clean",
    ],
)
def test_spot_hex32_reports_every_credential_line(text: str, expected: list[int]) -> None:
    assert findings(text) == expected


def test_a_pragma_line_does_not_hide_a_real_value_on_the_next_line() -> None:
    """R-020's guard. The rule used to consume the newline after a match, so a value on the line
    after an allowlisted fixture was never matched and the whole scan came back green."""
    text = f"fixture, {PRAGMA}, value={A}\n{B}\n"
    assert findings(text) == [2]


def test_the_pragma_still_allows_its_own_line() -> None:
    assert findings(f"SPOTIFY_CLIENT_ID={A}  # {PRAGMA}\n") == []
