# spot-main-R-004 — The star schema, API path

**Session: `main`. Read `docs/data-contracts.md` in full before starting. It is binding.**

This is the first warehouse build. Everything before it was plumbing.

## Scope — and what is deliberately out

**In:** `staging` → `intermediate` → `marts` in dbt-postgres, built from what is already in `raw.api_response`
(4 probe responses, ~100 play events, profile `marc`).

**Out, by instruction:**
- The export loader. R-003 is parked until the GDPR files arrive. **Design the fact table so the export
  path drops in without a migration** — `source_system`, the nullable `ms_played`, and the dedupe key
  all exist now and are exercised by the API path alone.
- The genre bucket seed. R-015 is waiting on Marc's sign-off. Build `dim_genre` and `br_artist_genre`
  from the raw genre strings; ship `seeds/genre_bucket_map.csv` with a header row and no data, and make
  the bucket rollup tolerate an empty map without failing.
- Playlists, library, top items. Those are R-006 and later; `/me/playlists` has not been extracted yet.

## Build

Per `docs/data-contracts.md`, in its terms:

| Layer | Models |
|---|---|
| staging | one view per `feed` value in `raw.api_response`. Flatten, cast, rename. No joins, no business logic. |
| intermediate | dedupe per §2, artist/genre allocation per §4 |
| marts | `dim_profile`, `dim_content` (+ `dim_track_detail`), `dim_artist` (SCD2), `dim_album`, `dim_genre`, `dim_date`, `dim_time_of_day`, `br_content_artist`, `br_artist_genre`, `fct_play_event` |

Notes the probe already established, so don't rediscover them:
- `played_at` is ms-precision UTC (`…THH:MM:SS.sssZ`); items come back newest-first.
- 50 items held 43 distinct track URIs. **Never dedupe on URI alone.**
- `track.available_markets`, `popularity`, `preview_url` are **absent keys**, not nulls. Use `->>`/`coalesce`.
- `album.release_date_precision` was `day` and `year` in one sample. `release_date` stays text; parse by precision.
- `context` was null on 2 of 50. Staging must allow it.
- Recently-played artist objects are **simplified — no `genres`.** `dim_artist` needs `GET /artists/{id}`.
  Add that extraction as part of this round; it is the only new API surface here.

## Required tests

Beyond dbt's generic tests, these three are the round:

1. **Grain.** `fct_play_event` is unique on (`profile_key`, `content_key`, `date_trunc('minute', ended_at_utc)`).
2. **No orphans.** Every FK resolves, including to the `-1`/`-2` unknown members.
3. **Allocation reconciliation.** Total `ms_played` → music → artist-resolved → genre-allocated, with the
   residual named at each step (§4). Ship it as a model, `int_allocation_reconciliation`, not just a test —
   the notebook in R-009 has to print it.

## Measure and report these

Numbers, not adjectives. Each one is a contract check:

- **Minute-collapse rate** (§2's accepted loss): how many rows share a natural key with another row?
  If it is material on 100 API events, say so — the contract said to quantify it before accepting it.
- **Content resolution rate** — `is_resolved = false` count.
- **Artists with zero genres** — these contribute to no bucket and are not `other`.
- **Multi-artist share** — the contract records 26% from the probe sample; confirm or correct it.

## Definition of done

`CLAUDE.md` §7 in full. In particular: **stage a break and name the test that goes red.** The obvious
one is removing the minute truncation from the dedupe key and showing the grain test fail with a count.
A build report that says "all tests pass" without that is not finished.

## Constraints

- **Do not amend a contract.** If §2–§5 conflict with the data, stop and report. That has happened once
  already this project and the finding was worth more than the workaround.
- `dbt` runs through `make dbt-*` / `scripts/dbt.sh` (F5). Target is `stg_main` / `mart_main`. **Do not
  build into `analytics`.**
- Nothing under `data/` gets committed.

## Report

`claude_work/reports/spot-main-R-004-report.md`, with the four measurements above and the red-test transcript.
