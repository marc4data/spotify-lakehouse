"""Extended Streaming History loader (spot-main-R-003): product, safe unzip, PII scrub, re-runs."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from spotify_lakehouse import export

# Documentation-range address (RFC 5737 TEST-NET-1) and an obviously fake agent: never real data.
FAKE_IP = "192.0.2.10"
FAKE_AGENT = "pytest-agent/1.0"


def _record(index: int, **extra: object) -> dict[str, object]:
    return {
        "ts": f"2024-01-01T00:{index:02d}:00Z",
        "ms_played": 31000,
        "spotify_track_uri": f"spotify:track:pytest{index}",
        "spotify_episode_uri": None,
        "ip_addr": FAKE_IP,
        "user_agent_decrypted": FAKE_AGENT,
        **extra,
    }


def test_scrub_removes_every_discarded_field_without_mutating_input() -> None:
    record = _record(1, user_agent=FAKE_AGENT, ip_addr_decrypted=FAKE_IP)
    clean = export.scrub_record(record)
    assert not set(export.DISCARDED_FIELDS) & set(clean)
    assert clean["spotify_track_uri"] == "spotify:track:pytest1"
    assert record["ip_addr"] == FAKE_IP


def test_the_one_year_account_data_package_is_refused(tmp_path: Path) -> None:
    (tmp_path / "StreamingHistory_music_0.json").write_text("[]")
    (tmp_path / "Userdata.json").write_text("{}")
    with pytest.raises(export.ExportError, match="1-year account-data package"):
        export.discover_files(tmp_path)


def test_extended_files_are_found_and_other_files_ignored(tmp_path: Path) -> None:
    folder = tmp_path / "Spotify Extended Streaming History"
    folder.mkdir()
    for name in [
        "Streaming_History_Audio_2018_1.json",
        "Streaming_History_Audio_2014.json",
        "Streaming_History_Video_2023.json",
        "ReadMeFirst_ExtendedStreamingHistory.pdf",
        "notes.json",
    ]:
        (folder / name).write_text("[]")
    assert [p.name for p in export.discover_files(tmp_path)] == [
        "Streaming_History_Audio_2014.json",
        "Streaming_History_Audio_2018_1.json",
        "Streaming_History_Video_2023.json",
    ]


def test_extract_refuses_a_member_that_escapes_the_profile_folder(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "evil.zip", "w") as zf:
        zf.writestr("../escaped.json", "[]")
    with pytest.raises(export.ExportError, match="unsafe member path"):
        export.extract_archives(tmp_path)
    assert not (tmp_path.parent / "escaped.json").exists()


def test_extract_never_overwrites_and_is_idempotent(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "export.zip", "w") as zf:
        zf.writestr("Spotify Extended Streaming History/Streaming_History_Audio_2014.json", "[1]")
    assert export.extract_archives(tmp_path) == [
        "Spotify Extended Streaming History/Streaming_History_Audio_2014.json"
    ]
    target = tmp_path / "Spotify Extended Streaming History" / "Streaming_History_Audio_2014.json"
    target.write_text("[2]")  # a later extraction must not clobber what is on disk
    assert export.extract_archives(tmp_path) == []
    assert target.read_text() == "[2]"


# --- Integration: shared Postgres. Everything written here is rolled back. ---


class _Rollback(Exception):
    pass


def _export_file(tmp_path: Path, name: str, count: int) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps([_record(i) for i in range(count)]))
    return path


def test_scrub_keeps_ip_and_user_agent_out_of_raw_export_record(db, tmp_path: Path) -> None:
    """The named PII guard. The table's check constraint is dropped inside this transaction, so
    the only thing between a fake IP and raw is the loader's scrub, which is what is under test."""
    path = _export_file(tmp_path, "Streaming_History_Audio_1999.json", 3)
    try:
        with db.transaction():
            db.execute(
                "alter table raw.export_record drop constraint export_record_no_ip_or_user_agent"
            )
            result = export.load_file(db, "pytest", path)
            assert result.inserted == 3
            row = db.execute(
                "select count(*) from raw.export_record where source_file = %s and payload ?| "
                "array['ip_addr', 'ip_addr_decrypted', 'user_agent', 'user_agent_decrypted']",
                (path.name,),
            ).fetchone()
            assert row == (0,), f"{row[0]} record(s) in raw.export_record carry ip_addr/user_agent"
            raise _Rollback
    except _Rollback:
        pass


def test_the_database_rejects_an_ip_even_if_the_scrub_is_bypassed(db) -> None:
    import psycopg

    try:
        with db.transaction():
            with pytest.raises(psycopg.errors.CheckViolation, match="no_ip_or_user_agent"):
                db.execute(
                    "insert into raw.export_record "
                    "(profile_slug, source_file, record_index, payload) "
                    "values ('pytest', 'pytest.json', 0, %s::jsonb)",
                    (json.dumps({"ts": "2024-01-01T00:00:00Z", "ip_addr": FAKE_IP}),),
                )
            raise _Rollback
    except _Rollback:
        pass


def test_loading_the_same_file_twice_inserts_zero_the_second_time(db, tmp_path: Path) -> None:
    path = _export_file(tmp_path, "Streaming_History_Audio_1998.json", 5)
    try:
        with db.transaction():
            assert export.load_file(db, "pytest", path).inserted == 5
            again = export.load_file(db, "pytest", path)
            assert (again.records, again.inserted) == (5, 0)
            raise _Rollback
    except _Rollback:
        pass


def test_raw_export_record_is_append_only(db, tmp_path: Path) -> None:
    import psycopg

    path = _export_file(tmp_path, "Streaming_History_Audio_1997.json", 1)
    try:
        with db.transaction():
            export.load_file(db, "pytest", path)
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                db.execute(
                    "update raw.export_record set record_index = 9 where source_file = %s",
                    (path.name,),
                )
            raise _Rollback
    except _Rollback:
        pass


# --- Rebuild input gates (spot-main-R-062) -------------------------------------------------------


def _export_dir(root: Path, slug: str, plays: list[tuple[str, str]], zip_body: bytes) -> Path:
    """A profile directory holding a zip (for hashing) and its extracted file (for play keys)."""
    directory = root / slug
    directory.mkdir(parents=True)
    (directory / f"{slug}.zip").write_bytes(zip_body)
    (directory / "Streaming_History_Audio_2024.json").write_text(
        json.dumps([{"ts": ts, "spotify_track_uri": uri, "ms_played": 1000} for ts, uri in plays])
    )
    return directory


def test_rebuild_refuses_two_exports_that_are_the_same_file(tmp_path: Path) -> None:
    """THE GUARD. One download served under two names is what R-044 could not recover from."""
    same = b"PK-identical-archive"
    _export_dir(tmp_path, "one", [("2024-01-01T00:00:00Z", "spotify:track:a")], same)
    _export_dir(tmp_path, "two", [("2024-02-01T00:00:00Z", "spotify:track:b")], same)

    with pytest.raises(export.ExportError) as caught:
        export.check_rebuild_inputs(tmp_path, ["one", "two"])
    assert "the same file" in str(caught.value)
    assert "re-request" in str(caught.value)


def test_rebuild_refuses_profiles_whose_plays_materially_overlap(tmp_path: Path) -> None:
    """The gate is a RELATION, not a hash (Cowork, 2026-09-24).

    R-062's prompt banned one md5. That cannot catch two files which differ by a byte yet carry the
    same listening, and it wrongly condemns a file that is merely the sole surviving copy. Two
    Spotify accounts do not independently produce the same play at the same millisecond.
    """
    shared = [(f"2024-01-01T00:{i:02d}:00Z", f"spotify:track:s{i}") for i in range(20)]
    _export_dir(tmp_path, "one", shared, b"PK-archive-one")
    _export_dir(tmp_path, "two", shared, b"PK-archive-two-different-bytes")

    with pytest.raises(export.ExportError) as caught:
        export.check_rebuild_inputs(tmp_path, ["one", "two"])
    message = str(caught.value)
    assert "share" in message and "100.00%" in message


def test_rebuild_accepts_two_genuinely_different_people(tmp_path: Path) -> None:
    """The real pair, measured 2026-09-24: 1 shared play in 60,080 — 0.0017%."""
    left = [(f"2024-01-01T00:{i:02d}:00Z", f"spotify:track:l{i}") for i in range(50)]
    right = [(f"2025-06-01T00:{i:02d}:00Z", f"spotify:track:r{i}") for i in range(50)]
    right[0] = left[0]  # one coincidence must not condemn the pair
    _export_dir(tmp_path, "one", left, b"PK-archive-one")
    _export_dir(tmp_path, "two", right, b"PK-archive-two")

    checked = export.check_rebuild_inputs(tmp_path, ["one", "two"])
    assert checked.overlaps[("one", "two")] == pytest.approx(0.02)
    assert checked.play_counts == {"one": 50, "two": 50}
