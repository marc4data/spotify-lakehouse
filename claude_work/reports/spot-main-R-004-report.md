# spot-main-R-004 — Build report: STOPPED on a contract conflict

**Session:** `main` · **Date:** 2026-09-14 · **Round status: stopped, per the prompt's constraint.**

> **Do not amend a contract. If §2–§5 conflict with the data, stop and report.**

They conflict. **Spotify returns no artist genres to this app from any endpoint that carries artist
objects.** `docs/data-contracts.md` §3 defines `dim_artist` as SCD Type 2 *on genres*, and §3–§4 build
`dim_genre`, `br_artist_genre` and the whole allocation chain on that field. Those models cannot be built as
specified. Nothing was built, migrated or committed except this report.

The four requested measurements are below, taken from `raw` and from the API directly. Two more findings
also need a decision before a rebuilt R-004 can run: the raw table was never migrated to the amended §1
shape, and the suggested red test cannot go red on this data.

---

## 1. The finding: genres are gone

**Measured 2026-09-14, profile `marc`.** Every artist id referenced anywhere in `raw` (track artists plus
album artists) was requested. Key presence only; no values recorded.

| Source | Objects | `genres` key present | ≥1 genre | Other keys |
|---|---|---|---|---|
| `GET /artists/{id}`, all artists in raw | **45** | **0** | **0** | `external_urls, href, id, images, name, type, uri` (identical ×45) |
| `GET /artists?ids=…` (batch, ≤50) | — | **HTTP 403** | — | batch endpoint refused in dev mode |
| `GET /me/top/artists?limit=5&time_range=medium_term` | 5 | 0 | 0 | same 7 keys |
| `GET /me/following?type=artist&limit=5` | 5 | 0 | 0 | same 7 keys |

`popularity` and `followers` are also absent from every artist object.

**What this contradicts:**

| Document | Says | Observed |
|---|---|---|
| `CLAUDE.md` §4 | "Artist `genres` still returns but Spotify now marks the field deprecated." | Not returned: the key is absent on 55/55 artist objects across three endpoints |
| `data-contracts.md` §3 `dim_artist` | "SCD Type 2 on `genres`" · `genres_observed text[], as returned` | Nothing to observe; no attribute to track |
| `data-contracts.md` §3 `dim_genre` / `dim_genre_bucket` | one row per distinct raw genre string | 0 strings |
| `data-contracts.md` §4 `br_artist_genre` + allocation chain | `ms_played` → primary artist → genres (1/N) → bucket | Chain ends at "primary artist"; 100% of music would be `unclassified_ms` |
| `data-contracts.md` §6 | MusicBrainz fallback is "Phase 2. Contract written **when Spotify actually removes `genres`**, not before." | **That condition is now met** for this app |
| R-004 prompt | "`dim_artist` needs `GET /artists/{id}`. Add that extraction" | The extraction works, but returns no field the contract uses except name/id/uri |

**Scope of the claim:** these are the only artist-bearing endpoints available to this app. The result is 55
objects with an identical key set, from three endpoints. That's a measurement of what this app receives
today. It doesn't say whether other app types, or extended quota mode, still receive genres.

### Proposed amendments — Cowork to choose

| Option | What changes | Cost |
|---|---|---|
| **A. Build the star without genres now** (recommended as the unblocker) | Re-scope R-004 to: `dim_profile`, `dim_content` + `dim_track_detail`, `dim_album`, `dim_artist` (**SCD1** on name, or SCD2 kept with no tracked attribute until a genre source exists), `dim_date`, `dim_time_of_day`, `br_content_artist`, `fct_play_event`, grain/orphan tests, and a reconciliation that ends at "artist-resolved". `dim_genre`, `br_artist_genre` and the bucket rollup move to a new round. | R-009's genre radar is blocked until a source is chosen. R-015 (bucket sign-off) goes moot until then |
| **B. Pull the MusicBrainz fallback into phase 1** | New round: map Spotify artist → MusicBrainz artist (by ISRC via recordings, or by name). Genres come from MusicBrainz tags/genres, carried in `dim_artist` SCD2 with a `genre_source` column. | A second external API (free, ~1 req/s, requires a User-Agent). Name matching is ambiguous; ISRC→recording→artist is more reliable and the ISRCs are already in raw (100/100 items) |
| **C. Another tag source** (e.g. Last.fm `artist.getTopTags`) | Same shape as B with a different source | Requires an API key: a new credential under §5 |

Whichever is chosen, `CLAUDE.md` §4's genres paragraph and §6's "phase 2" row are now wrong, and the 13-bucket
taxonomy (R-015) presumes Spotify's micro-genre strings. MusicBrainz or Last.fm tags are a different vocabulary.

---

## 2. The four requested measurements

| Measurement | Result | Notes |
|---|---|---|
| **Minute-collapse rate** | **0 of 50** distinct plays share `(content_uri, minute)` with another distinct play | The 100 raw items are **50 plays captured twice**: both probe runs returned the identical window (50/50 overlap, same `uri` + `played_at` to the millisecond). All duplicates are exact re-captures that any dedupe removes. There were no genuine same-minute collisions. The contract's actual question, "export rows that share a natural key with another export row", can only be answered once R-003's export arrives |
| **Content resolution rate** (`is_resolved = false`) | **0 of 43** distinct track URIs would be unresolved | Every API item carries a full track object (name, `duration_ms`, album, artists, ISRC). API-sourced content is resolved by construction; the rate only means something for export rows |
| **Artists with zero genres** | **45 of 45 (100%)** | See §1. Not "zero genres": no `genres` key at all |
| **Multi-artist share** | **13 of 50 play items (26.0%)** confirms the contract. **10 of 43 distinct tracks (23.3%)** | Distribution over items: 1 artist ×37, 2 ×10, 3 ×1, 4 ×2, matching §4 exactly. The contract's 26% is **per play**, not per track. Both are true; the notebook should say which it shows |

Other facts from `raw` (all consistent with the prompt's notes):

- `played_at` shape is `dddd-dd-ddTdd:dd:dd.dddZ`.
- `release_date_precision` values: `day`, `year`.
- `context` is null on 4 of 100 items (2 per run).
- ISRC is missing on 0 of 100 items.
- 38 distinct albums; 45 distinct artists (43 track artists, 35 album artists).

---

## 3. Second conflict: `raw.api_response` does not match amended §1

Contract §1, as amended 2026-09-13:
`id bigserial, feed text not null, payload jsonb, source_file text, profile_slug text, ingested_at timestamptz`

Live table, from `information_schema`:

| Live column | Live type |
|---|---|
| `id` | bigint |
| `payload` | jsonb |
| `source_file` | text |
| `profile_key` | text |
| `ingested_at` | timestamptz |

The live table has **no `feed` column**, and **`profile_key` instead of `profile_slug`**. Only migration `0001`
is applied. The amendment wasn't paired with a migration, and R-004's staging layer ("one view per `feed`
value") depends on it.

Renaming the column is metadata only. **Adding `feed` NOT NULL to the 4 existing rows requires a one-time
backfill**, and §1 also says rows are "**never modified after insert**, enforced by triggers". Those two
sentences conflict for rows that existed before the column did.

| Option | Mechanism | Trade-off |
|---|---|---|
| **A. One-time schema backfill** (recommended) | Migration `0002`: rename column; add `feed`; set it from the `source_file` path for pre-existing rows with the update trigger disabled inside the migration transaction; `NOT NULL`; re-enable. Checksum `(id, payload, source_file, ingested_at)` before and after to prove only the new column was written | A documented, bounded exception to "never modified", for a column that didn't exist at insert |
| **B. Discard the 4 probe rows** | Recreate the table in contract shape and re-probe | Loses nothing of analytical value (it's one 50-play window, captured twice), but it deletes raw data, which §1 also forbids |
| **C. Generated column** | `feed` generated from `source_file` | The extractor could never set `feed` explicitly, which defeats F2's rationale |

The Python side also has to follow either way: `raw_store.insert_response` writes `feed` and `profile_slug`,
and the integration test in `tests/test_migrate.py` names `profile_key`.

---

## 4. The suggested red test cannot go red on this data

The prompt proposes removing the minute truncation from the dedupe key and watching the grain test fail. **On
the current data it would pass.** There are 0 pairs of distinct plays in the same minute (§2), so exact-timestamp
dedupe and minute dedupe produce identical rows. The only duplicates are exact re-captures, which both keys
remove.

A guard proven only by a break the data happens not to exercise isn't proven. Proposed for the rebuilt round:

- **A dbt unit test** (dbt ≥1.8, installed 1.12.4) on the dedupe model, with a fixed fixture: two distinct plays of one URI 20 seconds apart in one minute, and one play captured twice. Removing the truncation turns it red deterministically, whatever `raw` contains.
- **Plus** the break the live data *can* exercise: removing the dedupe entirely fails the grain test with **50 duplicate keys**.

---

## 5. Smaller findings for the rebuilt round

| # | Finding | Proposal |
|---|---|---|
| S1 | `dim_profile.home_timezone` and `household_role` have **no API source**: `/me` gives country, not a timezone. They must be declared somewhere, and `date_key` depends on `home_timezone`. | A seed, e.g. `seeds/profile_registry.csv` (`profile_slug, household_role, home_timezone`). **It would be public.** Publishing each family member's role and home timezone is Marc's call, as is the alternative of keeping it in `~/.config/spot/` and loading it at build time |
| S2 | The contract's "`other` > 15% of allocated time fails the build" test vs the prompt's "tolerate an empty map without failing". Once export `ms_played` exists and the map is empty, 100% is `other` and the build fails. | Moot while genres are absent. When a genre source exists, decide whether an empty map skips the test or fails it |
| S3 | Batch `GET /artists?ids=` is **403** in dev mode. One call per artist at ~1 req/s is 45 s today, but a decade of export history is thousands of artists, and the same will apply to tracks and albums for export resolution. | R-003 needs a resumable, lock-holding resolver. Worth noting in the R-003 prompt |
| S4 | The prompt's "~100 play events" is 100 **items** = 50 **plays** (two identical windows). | Wording only, but a fact-table row count test written against "~100" would be wrong by 2× |
| S5 | **`spotify-wta` still exists** at `e4e9109` (branch `feat/probe`), with schemas `stg_wta` / `mart_wta`. R-019 is closed as "slated for removal", but the worktree wasn't removed. Migration `0002` (§3) renames a column its extractor code writes. | Marc: `git worktree remove ../spotify-wta && git branch -D feat/probe`, and drop `stg_wta`/`mart_wta`, before `0002` lands |

---

## 6. What this round did and did not touch

| | |
|---|---|
| Files changed | `claude_work/reports/spot-main-R-004-report.md` (this file) only |
| Contracts touched | None |
| Migrations | None. `raw` still at `0001` |
| dbt models | None. `dbt/models/*` still `.gitkeep` only |
| API calls | **49**: 1 shape check `GET /artists/{id}`, 1 batch (403), 45 single `GET /artists/{id}`, `GET /me/top/artists`, `GET /me/following`. All under the `spot_refresh` advisory lock with 1 s pacing. **None persisted**, because `raw.api_response` has no `feed` column to file them under (§3). They can be re-fetched in about a minute when the round is rebuilt |
| Raw data | Read only |

## 7. Definition of done (CLAUDE.md §7)

**Not met, by design: the round stopped.** No test or constraint was added, so there's no break to stage. The
red-test plan for the rebuilt round is in §4. `pytest`, `ruff` and pre-commit were not re-run because no code
changed. CI on `main` last passed at `e4e9109` (run `34815340167`); this session didn't watch the later
docs-only pushes.

---

**Start 2026-09-14 11:55 AM / End 12:00 PM : 04:50**
