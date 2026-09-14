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
| `raw` | extractor (Python) | One table per source feed. Untyped: `id bigserial`, `payload jsonb`, `source_file text`, `profile_key text`, `ingested_at timestamptz`. **Never modified after insert.** Shared across all sessions. |
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

**`ip_addr` and `user_agent` from the export are dropped at staging and never land in the warehouse.**
They add nothing to listening analysis and are the most sensitive fields in the file.

### Deduplication contract

The export and the API overlap. Once a refreshed export arrives, every play it covers is already in
the warehouse from the poller.

**Natural key: `(profile_key, content_uri, date_trunc('minute', ended_at_utc))`.**

Minute truncation, not second: the export records stream *end* and the API's `played_at` is
independently recorded, and they do not agree to the second.

**Precedence: `export` beats `api`, always.** When both sources produce a row for one natural key,
keep the export row and discard the API row entirely — do not merge fields. The export row is
strictly richer.

> [!WARNING]
> Minute-truncation will collapse two genuine plays of the same track in the same minute into one.
> This is a real and accepted loss. A track under 60 seconds played twice back-to-back counts once.
> **Quantify it before accepting it:** the round that builds this reports how many export rows share
> a natural key with another export row. If that number is material, the contract changes.

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
| `has_api_access` | Whether they occupy one of the 5 dev-mode slots. |
| `export_coverage_start` / `_end` | Observed min/max `ts` in their export. **Derived, not declared** — every longitudinal comparison must clip to the intersection of coverage windows or it will show a family member "stopping listening" when their export simply ends. |
| `api_coverage_start` | First poller row. |

**`email` is never stored.** The extractor reads it from `/me` to match accounts and discards it.

### `dim_content` — supertype, SCD Type 1

Kimball supertype/subtype. Music and podcasts must live in one dimension or the music-vs-podcast
split requires a union at query time in every single query.

| Column | Notes |
|---|---|
| `content_key` | Surrogate. |
| `content_uri` | Natural key. `spotify:track:…` or `spotify:episode:…`. |
| `content_type` | `track` \| `episode`. |
| `content_name` | Track or episode name. |
| `parent_name` | Album name for tracks; show name for episodes. |
| `primary_creator_name` | Album artist for tracks; show publisher for episodes. |
| `duration_ms` | NULL where unresolved. |
| `is_resolved` | Whether the URI was successfully looked up in the API. See below. |
| `first_seen_at`, `last_seen_at` | |

Subtype tables `dim_track_detail` and `dim_episode_detail` hang off `content_key` and carry
type-specific attributes (album_key, explicit, disc/track number; show_key, episode description,
release_date).

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

### `dim_artist` — SCD Type 2 on `genres`

| Column | Notes |
|---|---|
| `artist_key`, `artist_id`, `artist_uri`, `artist_name` | |
| `genres_observed` | text[], as returned. |
| `valid_from`, `valid_to`, `is_current` | |

Type 2 because **Spotify has marked the artist `genres` field deprecated.** Snapshotting it with a
date means the genre analysis survives the field's removal. Every genre value this warehouse ever
sees is preserved with the date it was observed.

### `dim_album`, `dim_show` — SCD Type 1
Straightforward. `dim_show` carries publisher, total_episodes, media_type.

### `dim_playlist` — SCD Type 2
Name, description, owner, is_public, is_collaborative, track_count all change. Type 2 on all of them.

### `dim_date`, `dim_time_of_day`
`dim_date`: standard, generated 2008-10-07 (Spotify launch) through +2 years, with fiscal-free
calendar attributes plus `is_weekend`, `day_of_week_name`, `iso_week`, `month_start`, `quarter`.

`dim_time_of_day`: 1440 rows, one per minute of day, with `hour`, `minute`, `daypart`
(`overnight` 00–05, `morning` 05–09, `midday` 09–12, `afternoon` 12–17, `evening` 17–22,
`night` 22–24).

### `dim_genre` and `dim_genre_bucket`

Spotify emits several thousand micro-genres (`melodic drill`, `escape room`, `pov: indie`). Nobody
reads a radar chart with 4,000 spokes.

**`dim_genre`** — one row per distinct raw genre string ever observed.
**`dim_genre_bucket`** — the analysis taxonomy. **Marc owns this list; it is a business definition.**

Proposed buckets (see §6 — this needs Marc's sign-off before the mapping is built):

`hip-hop / rap` · `pop` · `rock` · `alt / indie rock` · `hard rock / metal` · `country / americana` ·
`r&b / soul` · `electronic / dance` · `latin` · `jazz / blues` · `classical` · `spoken / comedy` ·
`other`

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

### `br_artist_genre`
Grain: one row per `(artist_key, genre_key)` with `weight_factor = 1 / count(genres for that artist)`.

**Allocation chain for the radar chart:**
`ms_played` → primary artist (weight 1.0) → each of that artist's genres (weight 1/N) → bucket (sum).

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

Carries `added_at`, `added_by_profile_key` (collaborative playlists), `position`.

### `fct_library_snapshot` — periodic snapshot
**Grain: one row per (profile, content, snapshot_date)** for saved tracks/albums.

### `fct_top_item` — periodic snapshot
**Grain: one row per (profile, entity_type, time_range, rank, snapshot_date).**
`entity_type` ∈ {artist, track}; `time_range` ∈ {short_term, medium_term, long_term}.
Spotify's own rollups, captured because they are the only pre-export signal available and because
comparing Spotify's ranking to ours is itself interesting.

---

## 5. Playlist comparison semantics

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

Comparison is at `content_key`, which means the **same recording**. A track that appears on both a
single and an album has two URIs and will read as a miss. **Contract:** a `content_match_key` column
on `dim_content` — normalized `lower(trim(content_name)) || '|' || lower(trim(primary_creator_name))`
— supports a second, looser comparison mode. Both modes are exposed; the strict one is the default,
and the notebook shows both numbers side by side because the gap between them is itself the story.

---

## 6. Open contract items

| | Item | Needs |
|---|---|---|
| `spot-main-R-015` | Genre bucket list (§3) | **Marc's sign-off.** Everything else can be built around it; the seed file is the last thing to fill. |
| `spot-main-R-016` | Do added dev-mode users need Premium? | Empirical test during onboarding. |
| — | MusicBrainz genre fallback | Phase 2. Contract written when Spotify actually removes `genres`, not before. |
| — | Feature-artist credit splitting | Phase 2. A change to `weight_factor`, not to the model. |
