# Data Contracts — spotify-lakehouse

**Owner: Cowork. Binding on Claude Code.**

Everything in this file is a contract. Table names, grain statements, key definitions, and business
rules are fixed here and changed here. Claude Code implements them; it does not amend them. If a
contract cannot be built as written, **stop and report it** — that is a finding, not an obstacle.

Everything *not* in this file is Claude Code's to decide: file layout, module boundaries, SQL style
inside a model, materialization strategy, indexes, test implementation, retry logic, CLI shape.

---

## 1. Layers

| Schema | Owner | Contents |
|---|---|---|
| `raw` | extractor (Python) | **Three tables, distinguished by where the data came from (R-024, R-003).** **`raw.export_record`** (R-003) holds one row per **play record** from a Spotify Extended Streaming History export — per record, not per file, because a file holds ~15,000 records and one multi-megabyte `jsonb` is unqueryable: `id bigserial`, `profile_slug text not null`, `source_file text not null` (the export filename), `record_index int not null` (position within that file, so every record is addressable), `payload jsonb not null`, `ingested_at timestamptz`, **unique on `(profile_slug, source_file, record_index)`** so a re-run cannot double-load. `ip_addr` and `user_agent` are discarded before insert (§2) and rejected by a check constraint. Files live outside the repo under `~/spot-data/exports/<profile_slug>/`, the zip kept as the immutable original. Same append-only triggers as below. **`raw.external_response`** holds one row per response from a **non-Spotify** source (MusicBrainz, R-024): `id bigserial`, `source text` (checked against an allowlist, `musicbrainz` for now), `feed text` (`isrc_lookup`, `artist`; same format check), `request_key text` (the ISRC or MBID requested, so a 404 is tied to the id it answers and is never re-fetched), `payload jsonb`, `source_file text` (under `data/raw/external/<source>/<feed>/`), `ingested_at timestamptz`. **It has no `profile_slug`, deliberately:** an external source's answer about an artist is the same whoever played it, and a fake value in a not-null column is how a dimension starts lying. Same append-only triggers as below. **`raw.api_response`** holds one row per **Spotify Web API** response, **carrying a `feed` column** — not one table per endpoint (F2: a table per endpoint means a migration per new endpoint and an N-way union in staging). Untyped: `id bigserial`, `feed text not null`, `payload jsonb`, `source_file text`, `profile_slug text`, `ingested_at timestamptz`. **Never modified after insert**, enforced by triggers — with exactly one exception: a **schema
migration that adds a column which did not exist at insert time** may backfill that column, inside the
migration's own transaction, with the update trigger disabled for its duration, and must checksum every
pre-existing column before and after to prove nothing else was written. Adding `feed` to rows written
before the amendment is that case (R-004 §3). No other write to `raw` is permitted, ever. `profile_slug`, not `profile_key` — the raw layer holds the slug, and `profile_key` means an int surrogate everywhere else (F3). `payload` is the response **as received minus contract-discarded fields** (F1) — "raw" means untyped and unmodelled, never unscrubbed. Shared across all sessions. |
| `stg_<session>` | dbt | One view per raw feed. Flatten JSON, cast types, rename to project conventions. No business logic, no joins, no filtering except structurally-invalid rows. |
| `int_<session>` (inside `stg_<session>`) | dbt | Deduplication, source reconciliation, genre allocation. The messy middle. |
| `mart_<session>` | dbt | The star. Facts and dimensions only. |
| `analytics` | dbt, **main session only** | Promotion target. What notebooks and extracts read by default. |

---

## 2. `fct_play_event` — the spine

**Grain: one row per completed play event, per profile.** A play event is one occurrence of a
listener playing one piece of content, terminating for any reason.

This is the only fact table fed by two sources, and reconciling them is the hardest thing in the
project. Read this whole section before writing any of it.

### Columns

| Column | Type | Source | Notes |
|---|---|---|---|
| `play_event_key` | bigint | generated | Surrogate. |
| `profile_key` | int | FK → `dim_profile` | |
| `content_key` | int | FK → `dim_content` | Supertype key — resolves tracks and episodes alike. |
| `date_key` | int | FK → `dim_date` | Derived from `ended_at_utc` converted to the profile's home timezone. **Not from UTC.** A 11pm-Pacific play is a Tuesday play, not a Wednesday one. |
| `time_of_day_key` | int | FK → `dim_time_of_day` | Same conversion. |
| `ended_at_utc` | timestamptz | both | Export `ts`; API `played_at`. |
| `ms_played` | int | export only | **NULL for API-sourced rows.** See "the ms_played hole" below. |
| `is_ms_played_imputed` | boolean | derived | True where `ms_played` was estimated rather than observed. |
| `reason_start` | text | export only | `trackdone`, `clickrow`, `fwdbtn`, `backbtn`, `playbtn`, `appload`, … |
| `reason_end` | text | export only | |
| `was_skipped` | boolean | export only | |
| `was_shuffled` | boolean | export only | |
| `was_offline` | boolean | export only | |
| `platform` | text | export only | |
| `conn_country` | text | export only | |
| `source_system` | text | derived | `export` or `api`. |
| `source_file` | text | lineage | Export filename or extractor run id. |

**Discarded at ingest, never written to `raw`:** `ip_addr` and `user_agent` from the export (and the
`ip_addr_decrypted` / `user_agent_decrypted` names older exports use) — they add nothing to listening analysis
and are the most sensitive fields in the file. `raw` is append-only, so a field that reached it could never be
removed: §1's "as received minus contract-discarded fields" governs, and the loader scrubs them before insert,
with a check constraint on `raw.export_record` as the backstop (R-003; this sentence formerly said "dropped at
staging", which §1 made impossible). **Dropped at staging:** **`account_id`** from `/me`, an identifier Spotify
returns but does not document (F8). Nothing in the model needs it.

**Incognito plays are in the export, and they stay in the model.** A "private session" is not
withheld from the GDPR export: **1,558 of `marc`'s records carry `incognito_mode = true`** (measured
R-052, 2026-09-16 — the same count in `raw.export_record` and `stg_export__play_record`). They reach
`fct_play_event` and sit inside every figure in all three notebooks. **Nothing filters them and
nothing here proposes to:** silently dropping 1,558 real plays is a modelling decision nobody asked
for, and a worse surprise than the fact itself.

> [!WARNING]
> **The rows reach `fct_play_event`; the flag does not** (R-007 F1, clarified by spot-main-R-054).
> `was_incognito` exists only on `stg_export__play_record` — it is on **1** model file and **0** of
> the marts, verified 2026-09-16. So a filter cannot be written against the fact table: excluding
> incognito plays means joining back to export staging on the natural key
> `(content_uri, ended_at_utc)`, which is what `publish.py` does. R-007 measured that join removing
> exactly **1,558** rows, and checked it a second way first — **0** incognito rows share a key with
> a non-incognito row, so the join cannot be removing somebody else's play.

> [!WARNING]
> **Consequence for `04_household_comparison.ipynb`** (R-044, parked): when Marie's, Brody's and
> Emma's exports land, **their private sessions will be visible in a notebook built to compare family
> members**. That is a fact about the data, not a defect to fix in the loader, and it is not R-052's
> to solve. R-044's §1 says it out loud before any comparison is drawn.

### Deduplication contract

The export and the API overlap. Once a refreshed export arrives, every play it covers is already in
the warehouse from the poller.

**Natural key, by source pair (amended R-037):**

| Pair | Key | Why |
|---|---|---|
| export vs export | `(profile_key, content_uri, ended_at_utc)`: **second precision** | Two export rows come from one clock (stream end), so there is no disagreement to absorb; truncating them only collapses genuine plays. |
| export vs api | `(profile_key, content_uri, date_trunc('minute', ended_at_utc))` | The export records stream *end* and the API's `played_at` is recorded independently; they do not agree to the second. This pair is what minute truncation was written for. |
| api vs api | `(profile_key, content_uri, date_trunc('minute', ended_at_utc))` | Unchanged: one play captured by several polls. |

**Precedence: `export` beats `api`, always.** When an api row shares a minute key with an export row,
keep the export row and discard the API row entirely — do not merge fields. The export row is
strictly richer.

> [!NOTE]
> **Quantified, judged material, changed.** Under the former single minute key, R-003 measured **4,011
> export rows collapsed (2.258%)**, 6,337 sharing a key (3.567%), 61.5 h of `ms_played`, 1,023 collapsed
> rows played ≥ 30 s. Cowork judged that material (R-037). At second precision **2,794 of those rows come
> back** (33,572,697 ms; 182 of them ≥ 30 s). **1,217 rows still collapse**: export records identical to the
> second are indistinguishable, so collapsing them is correct. Minute truncation still merges two genuine
> API plays of one track in one minute; the API carries no duration, so that loss cannot be measured from
> the API alone.

### The `ms_played` hole

The API gives no listening duration. Every metric in this project that says "time listened" therefore
has a coverage boundary.

**Contract:**
- `ms_played` stays **NULL** for API rows. It is not defaulted to zero and not defaulted to track
  length. A NULL that propagates into a broken chart is a correct signal; a fabricated number is not.
- `is_ms_played_imputed` exists for a possible future imputation strategy and is `false` everywhere
  in phase 1.
- Every mart and notebook that aggregates `ms_played` **must** also expose `play_count` and
  `pct_rows_with_duration` for the same grouping, so the reader can see the coverage.

### "What counts as a listen"

Two measures, both published, never conflated:

| Measure | Definition |
|---|---|
| `play_count` | Every row. Includes two-second skips. |
| `qualified_play_count` | `ms_played >= 30000` **or** `ms_played >= 0.5 * content.duration_ms`. NULL `ms_played` does not qualify. |

30 seconds is the industry streaming threshold and makes the numbers comparable to Wrapped-style
statistics. The half-duration clause catches short tracks and podcast segments.

---

## 3. Dimensions

### `dim_profile` — SCD Type 1

One row per person whose data is in the warehouse. **Not one row per Spotify account** — a person
who contributes only an export file, with no API access, still gets a profile.

| Column | Notes |
|---|---|
| `profile_key` | Surrogate. |
| `profile_slug` | Stable lowercase handle: `marc`, `marie`, `brody`, `emma`. **This is what the CLI and notebooks address a person by.** |
| `display_name` | |
| `spotify_user_id` | NULL for export-only contributors. |
| `household_role` | `self`, `spouse`, `child`, `friend`. |
| `home_timezone` | IANA name. Drives every local-date conversion. |
| `has_api_access` | Whether they occupy one of the 5 dev-mode slots. **Redefined now that R-017 has landed (F1):** a stored refresh token **and** at least one `ok` row in `spot_meta.poll_run`. A token proves consent was granted; a successful poll proves it still works. The extractor selects on the token alone — it must never read a dbt mart to decide what to extract. |
| `export_coverage_start` / `_end` | Observed min/max `ts` in their export. **Derived, not declared** — every longitudinal comparison must clip to the intersection of coverage windows or it will show a family member "stopping listening" when their export simply ends. |
| `api_coverage_start` | **The first `ok` `spot_meta.poll_run.finished_at` for the profile** (F1, R-017). Probe captures that predate the poller are real data but not coverage — they are one-off snapshots, not a guarantee of continuity. |

**`email` never reaches the extractor.** The `user-read-email` scope is never requested, so Spotify does not
return it, and accounts are matched on `spotify_user_id` instead — `spot auth` refuses to bind one Spotify id
to two profile slugs (F7). This makes "email is never stored" structural rather than procedural. The scrub
before storage stays as a backstop.

### `dim_content` — supertype, SCD Type 1

Kimball supertype/subtype. Music and podcasts must live in one dimension or the music-vs-podcast
split requires a union at query time in every single query.

| Column | Notes |
|---|---|
| `content_key` | Surrogate. |
| `content_uri` | Natural key. `spotify:track:…` or `spotify:episode:…`. |
| `content_type` | `track` \| `episode` \| `audiobook_chapter` (R-037: a third subtype of listening, on the not-music side). |
| `content_name` | Track or episode name. |
| `parent_name` | Album name for tracks; show name for episodes. |
| `primary_creator_name` | Album artist for tracks; show publisher for episodes. |
| `duration_ms` | NULL where unresolved. |
| `is_resolved` | Whether the URI was successfully looked up in the API. See below. |
| `first_seen_at`, `last_seen_at` | |

Subtype tables `dim_track_detail` and `dim_episode_detail` hang off `content_key` and carry
type-specific attributes (album_key, explicit, disc/track number; show_key, episode description,
release_date).

**`dim_track_detail.isrc`** carries `external_ids.isrc` where the API returns it (F9). ISRC is a
*recording*-level identifier that survives re-issue under a new Spotify URI, which makes it the real fix for
both the unresolvable-content problem above and the single-vs-album miss in §5. The export does not carry it,
so it is populated only for API-resolved content and is therefore a second-tier key, never the primary one.

**`dim_album.release_date` is stored as text, not `date`.** Observed precision varies (`day` and `year` both
seen in one 50-track sample, `month` documented). Parse by `release_date_precision` at the point of use; a
direct cast will fail.

> [!CAUTION]
> **The export contains URIs for content that no longer exists in the catalog.** Tracks are removed,
> regionally delisted, and re-issued under new URIs constantly over a decade of history. A meaningful
> fraction of historical rows will not resolve against the API.
>
> **Contract: unresolvable content still gets a `dim_content` row**, built from the export's own
> `master_metadata_*` fields, with `is_resolved = false`. It is never dropped and never routed to an
> unknown-member row. Dropping it silently deletes Marc's early listening history, which is exactly
> the history he wants. The build report states the resolution rate.

Standard Kimball unknown members: `content_key = -1` for genuinely absent, `-2` for
not-yet-resolved-but-pending.

**Unknown-member attribute values are part of the domain, not an exception to it (C3, R-004).** Enumerate
them in the accepted-values tests rather than letting a row carry a value the contract forbids:
`dim_content.content_type` ∈ {`track`, `episode`, `audiobook_chapter`, `unknown`}; `dim_profile.profile_slug` unknown member is
`(unknown)`; `dim_time_of_day.daypart` gains `unknown`. A dimension whose unknown row violates its own
enumeration is a contract that cannot pass its own test.

### `dim_artist` — SCD Type 2 on sourced genres

| Column | Notes |
|---|---|
| `artist_key`, `artist_id`, `artist_uri`, `artist_name` | `artist_key` identifies a version; `artist_id` repeats across versions. |
| `genre_source` | Which vocabulary populated this version's genres: `musicbrainz`, or NULL when the artist resolved to no genre or tag strings. |
| `valid_from`, `valid_to`, `is_current` | Version bounds; exactly one current version per `artist_id`. |

**SCD Type 2 on the sourced genre and tag strings plus `genre_source` (R-024).** Spotify returns no
`genres` key to this app on any artist-bearing endpoint (measured 55/55, R-004 §1); genres come from
MusicBrainz, joined by ISRC. A version changes when the **set** of strings changes, not on vote counts;
name is Type 1 across versions. History is rebuilt from `raw.external_response`, not held in a dbt
snapshot, so every session derives the same versions (R-024 F5). An external vocabulary changes over
time, and surviving that is the reason for versioning.

### `dim_album`, `dim_show` — SCD Type 1
Straightforward. `dim_show` carries publisher, total_episodes, media_type.

### `dim_playlist` — SCD Type 2
Name, description, **`is_owned_by_profile`**, is_public, is_collaborative, track_count all change.
Type 2 on all of them.

> [!WARNING]
> **`owner` is not a column and never will be, amended by spot-main-R-058.** This row previously
> contracted Type 2 *on owner*. A playlist's owner is a person who is not necessarily Marc, this
> repository is public, and `raw_store.scrub` discards `items[].owner.*` before anything is written
> (R-054) — so owner identity is not derivable from `raw` and the model cannot carry it.
>
> What replaces it answers the same question without the identifier: **`scrub` compares `owner.id`
> to the profile's own Spotify id on the way past and keeps a boolean**, `is_owned_by_profile`. The
> id is gone by the time the payload is written; only the bit survives. It is a tracked Type 2
> attribute, so a playlist changing hands starts a new version.

### `dim_date`, `dim_time_of_day`
`dim_date`: standard, generated 2008-10-07 (Spotify launch) through +2 years, with fiscal-free
calendar attributes plus `is_weekend`, `day_of_week_name`, `iso_week`, `month_start`, `quarter`.

`dim_time_of_day`: 1440 rows, one per minute of day, with `hour`, `minute`, `daypart`
(`overnight` 00–05, `morning` 05–09, `midday` 09–12, `afternoon` 12–17, `evening` 17–22,
`night` 22–24).

### `dim_genre` and `dim_genre_bucket`

> [!NOTE]
> **The genre vocabulary is MusicBrainz's, not Spotify's (R-024).** `dim_genre` holds one row per distinct
> MusicBrainz string per `tag_type` (`genre`, the curated list; `tag`, the folksonomy). The *shape* below
> survived the change of source — a raw-tag dimension, a bucket dimension, a seed-file mapping, a drift
> test. The prose about Spotify micro-genres is the original motivation, kept for context; the bucket list
> and its sign-off are tracked as R-015.

Spotify emits several thousand micro-genres (`melodic drill`, `escape room`, `pov: indie`). Nobody
reads a radar chart with 4,000 spokes.

**`dim_genre`** — one row per distinct raw genre string ever observed.
**`dim_genre_bucket`** — the analysis taxonomy. **Marc owns this list; it is a business definition.**

Buckets, signed off by Marc 2026-09-14 and amended to 14 by spot-main-R-048:

`hip-hop / rap` · `pop` · `rock` · `alt / indie rock` · `hard rock / metal` · `country / americana` ·
`folk / singer-songwriter` · `r&b / soul` · `electronic / dance` · `latin` · `jazz / blues` ·
`classical` · `spoken / comedy` · `other`

> [!NOTE]
> **`folk / singer-songwriter` added 2026-09-15 (R-048), on Marc's decision "Add to Folk".** R-015 measured
> the hole: seven folk strings carrying 113.4 h, 5.94% of allocated listening time, all sitting in `other`
> because the 13-bucket list had nowhere for them. The name pairs the genre with its nearest neighbour, as
> the other labels do; it is a one-line seed edit if Marc prefers another. It sits between
> `country / americana` and `r&b / soul` so related families stay adjacent on the radar.

> [!NOTE]
> Marc's original list separated `rap` from `hip-hop`. Spotify's taxonomy does not — it emits
> `hip hop`, `rap`, `southern hip hop`, `trap` as siblings with no consistent distinction, and any
> split would be Cowork inventing a boundary Spotify does not draw. They are merged, and this note
> exists so the merge is a visible decision rather than a silent one. Marc can overrule it.

Mapping lives in a **committed seed file**, `seeds/genre_bucket_map.csv`, columns
`genre_name,bucket_name`. It is a seed and not code so Marc can edit it in a spreadsheet without a
Claude Code round. Unmapped genres fall to `other`, and a dbt test **fails the build when `other`
exceeds 15% of allocated listening time** — silent drift into `other` is how a radar chart becomes a
lie.

---

## 4. Bridges and allocation

### `br_content_artist`
Tracks have multiple credited artists. Grain: one row per `(content_key, artist_key)` with
`is_primary` and `weight_factor`.

**Phase-1 allocation contract: 100% of a play's `ms_played` is attributed to the primary artist.**
`weight_factor` is 1.0 for primary and 0.0 for features. This is a deliberate simplification and it
is documented in the notebooks wherever artist time is shown. Splitting credit across features is a
phase-2 change to a weight, not to a model.

**It is not a small simplification.** In the one observed 50-track sample, **13 tracks (26%) had more than one
credited artist** (2 artists ×10, 3 ×1, 4 ×2). Any "top artists" figure in phase 1 under-counts featured
artists by roughly that much, and the notebook says so where the number is shown.

### `br_artist_genre`
Grain: one row per `(artist_key, genre_key)` with `weight_factor = 1 / count(genres for that artist)`
**within one `tag_type`** — the curated `genre` list and the `tag` folksonomy are parallel allocations,
never one denominator (R-024).

**Allocation chain for the radar chart:**
`ms_played` → primary artist (weight 1.0) → each of that artist's genres (weight 1/N) → bucket (sum).

**Built through the genre step (R-024).** `int_allocation_reconciliation` reports every step — total →
music → artist-identified → genre-allocated — each naming its residual. The genre step follows one
MusicBrainz vocabulary (`genre` by default); artist-identified music whose primary artist has no string in it
is `unclassified_ms` — the correct, visible answer, not a failure to be papered over. The bucket step waits
on the mapping (R-015).

**Step 3 is `artist_identified`: the play's primary artist id is known (amended by spot-main-R-041, Cowork's
decision on R-040 F5).** R-004 defined it as `artist_resolved`, requiring the primary artist's own
`GET /artists/{id}` response. That made sense while Spotify was to supply genres. It does not: genres come
from MusicBrainz by ISRC, which reads the artist id off the track and never opens the artist response, so
the fetch gated the chain on a step the allocation does not pass through. **The change is definitional** —
the data does not move, the question step 3 asks does — and any before/after comparison must say so.

**The Spotify artist fetch is reported beside the chain, not inside it**, as an enrichment-coverage figure
(`int_artist_enrichment_coverage`): of the artist-identified plays, how many have a Spotify artist object.
That object carries the artist's name, which every top-artists view needs; it gates no allocation step.

Consequences Claude Code must handle and the notebook must state:
- An artist with no genres contributes to **no bucket**, not to `other`. `other` means "mapped to a
  genre we chose not to bucket." A separate `unclassified_ms` measure carries artist-has-no-genre
  time, and the notebook shows it as a footnote figure, not a spoke.
- Podcast episodes have no artist and therefore no genre. They are excluded from the radar entirely
  and reported as a separate total.
- Bucket totals sum to allocated music time, not to total listening time. **Every radar chart must
  print the reconciliation:** total ms → music ms → allocated ms → the three gaps.

### `fct_playlist_membership` — periodic snapshot
**Grain: one row per (playlist, content, snapshot_date).** This is what makes overlap analysis
possible over time rather than only right now. Snapshotted on every full refresh, not every poll.

Carries `added_at`, `position`, `content_uri`, and the track's own name/creator/ISRC as observed in
the playlist.

> [!WARNING]
> **`added_by_profile_key` is NOT built, amended by spot-main-R-054.** This row previously contracted
> it for collaborative playlists. A collaborative playlist names **a different person in `added_by`
> on every item**, and this repository is public, so `items[].added_by` is discarded at ingest and
> never reaches `raw` — discard beats redact, because a field that is never written cannot leak from
> a table nobody thought to check (R-052 F2). Marc has not asked who added what, and R-044 already
> rules that "who introduced whom" is an inference rather than a measurement. Restoring the column
> means deciding to store other people's identities first; it is not a modelling change.

> [!NOTE]
> **`content_key` is not defaulted to the `-2` pending member (R-054).** A playlist can contain
> tracks nobody has played, and collapsing them onto one key would make them indistinguishable —
> which is exactly the row "in A, not in B" is hunting. See §3's `dim_content` note.

### `fct_library_snapshot` — periodic snapshot
**Grain: one row per (profile, content, snapshot_date)** for saved tracks/albums.

### `fct_top_item` — periodic snapshot
**Grain: one row per (profile, entity_type, time_range, rank, snapshot_date).**
`entity_type` ∈ {artist, track}; `time_range` ∈ {short_term, medium_term, long_term}.
Spotify's own rollups, captured because they are the only pre-export signal available and because
comparing Spotify's ranking to ours is itself interesting.

---

## 5. Playlist comparison semantics

> [!NOTE]
> **Built by spot-main-R-054.** All three tiers are implemented in
> `spotify_lakehouse/playlists.py` (`misses`, `tier_table`) and shown side by side in
> `notebooks/05_sandbox.ipynb` §4. `miss_count` is directional and every output carries its
> direction in a label, which `tests/test_playlists.py::test_misses_are_directional` keeps
> true. ⚠️ **Tier 2's reach depends on which side you ask about, and the two answers differ by
> two orders of magnitude.** Across `dim_content` as a whole, ISRC lands only on API-resolved
> tracks — **624 of 48,784 rows (1.3%)**, measured 2026-09-16. But a *playlist* payload carries
> `external_ids.isrc` on the track object itself, so for playlist membership tier 2 covers
> **3,554 of 3,568 tracks (99.6%)**. Quote the coverage share beside the count, always: the
> unqualified sentence "tier 2 only covers API-resolved tracks" is true of the warehouse and
> misleading about the comparison.

Marc's stated goal: "compare playlists to overlaps and misses." Two different questions, both built:

| Metric | Definition |
|---|---|
| `overlap_count` | Distinct `content_key` present in both playlists at the same `snapshot_date`. |
| `jaccard_index` | `overlap / union`. The symmetric measure — use for "how similar are these two playlists." |
| `coverage_pct` | `overlap / size(A)`. **Asymmetric.** Use for "how much of A is already in B." |
| `miss_count` | In A, not in B. Directional — `A→B` and `B→A` are different rows. |

`miss` is directional and the output must always label the direction. "47 misses between Marc's
Workout and Brody's Gym" is meaningless; "47 tracks in Marc's Workout that are not in Brody's Gym"
is the answer.

Comparison is at `content_key`, which means the **same Spotify URI**. A track that appears on both a
single and an album has two URIs and will read as a miss.

**Contract — a three-tier match, most reliable first:**

| Tier | Key | Coverage |
|---|---|---|
| 1 (strict, default) | `content_uri` | Everything |
| 2 | `dim_track_detail.isrc` | API-resolved tracks only. Same recording, different URI — catches the single-vs-album case exactly (F9) |
| 3 (loose) | `content_match_key` — normalized name + primary creator | Everything, including unresolved export rows. Will over-match on covers and live versions |

All three are exposed and the notebook shows them side by side, because the gap between tiers is itself the
story: tier 1 to tier 2 is re-issue churn, tier 2 to tier 3 is genuine ambiguity.

> [!WARNING]
> **"Most reliable first" orders reliability, not containment — the tiers are NOT nested.** Amended by
> spot-main-R-059, which measured it: against `Smith` → `Connor's Playlist (MFA)`, tier 1 missed 40,
> tier 2 missed 30, and tier 3 — the *loose* tier — missed **31**. A looser match cannot miss more
> than a stricter one if the ladder nests, so it does not. Measured set relations: `tier2 ⊆ tier1` ✅,
> `tier3 ⊆ tier1` ✅, **`tier3 ⊄ tier2`** ❌.
>
> **Two mechanisms, both observed in the payload:**
> 1. **The normalized key breaks where an ISRC matches.** `JAŸ-Z` is stored with a diaeresis on one
>    side and as `jay-z` on the other, so `content_match_key` differs while `external_ids.isrc` is
>    identical — tier 3 misses `Otis` and `Ni**as In Paris`, tier 2 matches both.
> 2. **One recording carries two ISRCs.** Drake's `Forever` is `USUM70920707` on *Relapse: Refill*
>    and `USUM70985104` on *Forever*, so tier 2 misses it while tier 3's name key matches.
>
> **Consequence for anyone reading a tier count:** a track absent at tier 2 may still be present, and
> a track absent at tier 3 may still be present. "Absent" means absent **at every tier** — which is
> what `playlists.split_misses` computes, and why its two lists are the answer rather than any single
> tier's number.

---

## 6. Open contract items

### Settled 2026-09-13, from the R-002 live probe

F1–F5 and F7–F9 are **accepted as proposed and folded into the sections above.** In short: raw payloads
are as-received-minus-discarded-fields (F1); one `raw.api_response` with a `feed` column rather than a table
per endpoint (F2); the raw profile column is `profile_slug` (F3); bootstrap "completes non-credential steps
and fails naming the missing keys" (F4); `make dbt-*` is the supported dbt entry point (F5); email is
structurally unavailable because the scope is never requested (F7); `account_id` is dropped at staging (F8);
ISRC lands on `dim_track_detail` and becomes tier 2 of the playlist match (F9). F6 was withdrawn by the
reporting session, correctly — the date was right in UTC.

### Open

| | Item | Needs |
|---|---|---|
| `spot-main-R-015` | Genre bucket list (§3) | **Marc's sign-off.** Everything else can be built around it; the seed file is the last thing to fill. |
| `spot-main-R-016` | Do added dev-mode users need Premium? | Empirical test at family onboarding. The owner account is confirmed `product: premium`; the added-user case is untested. |
| ~~`spot-main-R-018`~~ | ~~Does `recently-played` page backwards via `next`?~~ | **SETTLED 2026-09-14: no.** Following `next` once returned 0 items and `next = null`, with 14 older plays demonstrably available from the same endpoint a day earlier. R-017's poller is a catcher, not a backfiller. See `CLAUDE.md` §4. |
| ~~—~~ | ~~MusicBrainz genre fallback~~ | **BUILT by `spot-main-R-024`** as the genre source, not a fallback: Spotify supplies no `genres` to this app (R-004). |
| — | Feature-artist credit splitting | Phase 2. A change to `weight_factor`, not to the model. Currently mis-attributing ~26% of tracks (§4). |
