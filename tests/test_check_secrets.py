from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.conftest import fake_hex

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "check_secrets.py"
spec = importlib.util.spec_from_file_location("check_secrets", HOOK)
assert spec and spec.loader
check_secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_secrets)

DOMAINS = frozenset({"gmail.com", "family.example"})


def test_flags_32_hex() -> None:
    findings = check_secrets.scan_text(f"SPOTIFY_CLIENT_ID={fake_hex()}", DOMAINS)
    assert findings and "32-hex" in findings[0][1]


@pytest.mark.parametrize("length", [31, 40, 64])
def test_ignores_other_hex_lengths(length: int) -> None:
    assert check_secrets.scan_text(f"sha={fake_hex(length)}", DOMAINS) == []


def test_flags_blocked_email_domain_and_never_echoes_value() -> None:
    line = "contact: somebody" + "@" + "gmail.com"
    findings = check_secrets.scan_text(line, DOMAINS)
    assert findings
    assert "somebody" not in repr(findings)


def test_allows_unblocked_domains() -> None:
    assert check_secrets.scan_text("noreply" + "@" + "anthropic.com", DOMAINS) == []


def test_pragma_skips_line() -> None:
    assert check_secrets.scan_text(f"{fake_hex()}  # spot: allow-secret-shape", DOMAINS) == []


def test_extra_domains_come_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("SPOT_BLOCKED_EMAIL_DOMAINS=family.example, other.example\n")
    monkeypatch.setenv("SPOT_CONFIG_DIR", str(tmp_path))
    domains = check_secrets.blocked_domains()
    assert {"family.example", "other.example", "gmail.com"} <= domains


def test_main_exit_code(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text(fake_hex())
    good = tmp_path / "good.txt"
    good.write_text("nothing here")
    assert check_secrets.main([str(good)]) == 0
    assert check_secrets.main([str(good), str(bad)]) == 1
