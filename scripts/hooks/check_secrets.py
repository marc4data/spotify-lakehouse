#!/usr/bin/env python3
"""pre-commit hook `spot-no-spotify-secrets` (CLAUDE.md §5).

Rejects staged text files containing:
  * a standalone 32-hex-character string — the shape of a Spotify Client ID and Client Secret;
  * an email address at a personal-mail domain, or at any domain listed in
    SPOT_BLOCKED_EMAIL_DOMAINS in ~/.config/spot/.env (kept outside the repo on purpose).

Matched values are never printed. A line containing `spot: allow-secret-shape` is skipped.
Standard library only.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HEX32 = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
DEFAULT_BLOCKED_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
    }
)
ALLOW_PRAGMA = "spot: allow-secret-shape"


def blocked_domains() -> frozenset[str]:
    config = Path(os.environ.get("SPOT_CONFIG_DIR") or Path.home() / ".config" / "spot")
    env_file = config / ".env"
    extra: set[str] = set()
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith("SPOT_BLOCKED_EMAIL_DOMAINS="):
                raw = line.split("=", 1)[1]
                extra |= {d.strip().lower() for d in raw.split(",") if d.strip()}
    return DEFAULT_BLOCKED_DOMAINS | extra


def scan_text(text: str, domains: frozenset[str]) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOW_PRAGMA in line:
            continue
        if HEX32.search(line):
            findings.append((lineno, "32-hex string (Spotify client ID / secret shape)"))
        for match in EMAIL.finditer(line):
            domain = match.group(1).lower()
            if domain in domains:
                findings.append((lineno, f"email address at blocked domain '{domain}'"))
    return findings


def main(argv: list[str]) -> int:
    domains = blocked_domains()
    rejected = False
    for name in argv:
        try:
            text = Path(name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for lineno, reason in scan_text(text, domains):
            print(f"{name}:{lineno}: {reason}")
            rejected = True
    if rejected:
        print(
            "\nCommit rejected by spot-no-spotify-secrets. Credentials and account identifiers "
            "belong in ~/.config/spot/, never in the repo (CLAUDE.md §5).\n"
            "Genuine false positive in a test fixture? Put 'spot: allow-secret-shape' on that line."
        )
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
