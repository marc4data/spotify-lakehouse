# spot-main-R-004 — The star schema, API path (rebuilt, genres removed)

**Session: `main`. Read `docs/data-contracts.md` in full before starting. It is binding.**

**This prompt supersedes the first R-004.** That round stopped correctly on a contract conflict and the
report (`claude_work/reports/spot-main-R-004-report.md`) is the best artefact this project has produced.
Every decision below answers a question it raised. Read it before this prompt.

## What Cowork decided, so you do not have to re-ask

| Report § | Cowork's call |
|---|---|
| §1 genres absent | **Option A.** Build the star without genres now. `dim_genre`, `br_artist_genre` and the bucket rollup move to `spot-main-R-024`. Contracts §3–§4 are amended and marked ⏸️ DEFERRED. `dim_artist` becomes **SCD Type 1** with a `genre_source` column, NULL for now. |
| §1 which source later | **MusicBrainz**, in R-024, not this round. Reason: no API key, so no new credential in a public repo (§5); and ISRC → recording → artist is a real join, present on 100/100 items, rather than name matching. Last.fm needs a key; that is the deciding factor, not quality. |
| §3 raw table mismatch | **Option A, one-time backfill.** B deletes raw data, which §1 forbids; C defeats F2's rationale. §1 now carries an explicit, bounded exception for a column that did not exist at insert. Do it exactly as §1 now words it. |
| §4 red test | **Both.** The dbt unit test with the fixed fixture *and* the live break that fails the grain test with 50 duplicate keys. Your reasoning was right: a guard proven only by a break the data does not exercise is not proven. |
| §5 S1 profile registry | **Not in the repo.** `~/.config/spot/profiles.csv` (`profile_slug,household_role,home_timezone`), loaded at build time, `.env.example`-style template committed. Household roles and home timezones for Marc's wife and children are personal data about other people and the repo is public. |
| §5 S2 `other` > 15% test | Moot while genres are absent. Ship it disabled with a comment naming R-024. |
| §5 S3 batch 403 | Noted for R-003. Not this round. |
| §5 S4 "~100 play events" | Cowork's error: 100 items = **50 plays**. Any row-count assertion uses 50. |
| §5 S5 `spotify-wta` | Being removed before `0002` lands. Assume a single checkout. |

## Build, in this order

**1. Migration `0002`** — bring `raw.api_response` to the amended §1 shape: rename `profile_key` →
`profile_slug`; add `feed text`; backfill `feed` from the `source_file` path for the 4 existing rows per
the §1 exception, with the before/after checksum on `(id, payload, source_file, ingested_at)` printed in
the report; then `NOT NULL`. Update `raw_store.insert_response` and `tests/test_migrate.py` to match.

**2. Re-file the 49 artist responses** the stopped round fetched but could not persist. They are worth
about a minute of API time and they are the only artist data in existence.

**3. The star**, per `docs/data-contracts.md` §2–§5:

| Layer | Models |
|---|---|
| staging | one view per `feed` value |
| intermediate | dedupe per §2; `int_allocation_reconciliation` per §4 |
| marts | `dim_profile`, `dim_content` + `dim_track_detail`, `dim_album`, `dim_artist` (SCD1), `dim_date`, `dim_time_of_day`, `br_content_artist`, `fct_play_event` |

Out of scope, by instruction: `dim_genre`, `dim_genre_bucket`, `br_artist_genre`, `dim_show`,
`dim_episode_detail`, `dim_playlist`, the export loader, playlists and library.

## Required tests

1. **Grain** — `fct_play_event` unique on (`profile_key`, `content_key`, `date_trunc('minute', ended_at_utc)`).
2. **No orphans** — every FK resolves, including to the `-1`/`-2` unknown members.
3. **Reconciliation** — `int_allocation_reconciliation` ships as a model and reports total ms → music ms →
   artist-resolved ms, with the residual named at each step. It ends at artist-resolved this round;
   `unclassified_ms` is 100% of music time and that is the correct visible answer.

## Definition of done

`CLAUDE.md` §7 in full, and the red test is **both** breaks from §4 of the report, each with the named
test and its failure output.

## Constraints

- **Do not amend a contract.** Stopping was right last time and it is right again. The contracts have been
  amended for everything your report raised — if something still conflicts, that is a new finding.
- `dbt` runs through `make dbt-*` / `scripts/dbt.sh`. Target `stg_main` / `mart_main`. **Never `analytics`.**
- Nothing under `data/` gets committed.

## Report

`claude_work/reports/spot-main-R-004-report.md` — replace it; the stopped round's content is preserved in
git at `c8f0a65`. Include the migration checksums, the four measurements re-taken against built models,
and both red-test transcripts.
