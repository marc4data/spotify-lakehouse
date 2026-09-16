# Notebook Specifications

Four notebooks, each rendered through **nb2report** into standalone HTML. (§4 is reserved for R-044's household comparison, which is parked and unbuilt, so the numbering skips it.)

```bash
uv run nb2report notebooks/01_data_inventory.ipynb \
  --author "Marc Alexander" \
  -o reports/01_data_inventory.html
```

`reports/` is gitignored. Notebooks are committed with outputs stripped.

---

## 0. Shared requirements — every notebook

**No connection logic in a notebook.** Each opens with a single import cell:

```python
from spotify_lakehouse.notebook import setup

ctx = setup(profile="marc")  # returns con, session, schema, and the plotting theme
```

`spotify_lakehouse.notebook.setup()` is a real module in the package. It resolves `.session`, reads
credentials from `~/.config/spot/`, opens the Postgres connection, imports and configures pandas /
plotly / matplotlib with the project theme, and returns a context object. **A notebook that can run
standalone without the package is a notebook that has credentials in it.**

**nb2report authoring rules — these drive how the notebooks must be written:**

| Rule | Why |
|---|---|
| Code cells are **hidden** in the report. | Every code cell must be preceded by a markdown cell that says what it shows. A section with no markdown renders as an unexplained table. |
| `print()` is **excluded** by default. | Use `display()` for anything the reader must see. Do not rely on `--include-stdout`. |
| Headings drive the TOC and the collapsible tree. | Heading discipline is the document structure. See below. |
| `display(Markdown('### Foo'))` from a code cell opens a section. | This is how the EDA notebook generates one section per endpoint from a loop instead of 30 hand-written cells. |
| `> [!WARNING]` / `> [!CAUTION]` blockquotes become callouts and drive the "Issues Only" filter. | Every data-quality finding gets one. That filter is the fastest review path. |
| `<!-- caption: … -->` before a figure captions it. | Every figure gets a caption. |

**PII:** every display of profile, user, or follower data passes through
`spotify_lakehouse.redact.redact_profile()`. The `/me` endpoint returns an email address.

🚨 **A helper that iterates columns generically carries its own sensitive-column list.** The rule
above governs displays that were **written** — a chosen table, a chosen set of columns. A generic
profiler, schema lister or sampler never meets it: it profiles column 17 exactly as it profiled
column 3, and if column 17 is `platform` it renders a device model. Any such helper names the columns
whose *values* must never render — today `platform`, because it names a device — and redacts them in
**every** derived field: example, min, max, modal value and sample rows alike.

**The list is `spotify_lakehouse.redact.SENSITIVE_VALUE_COLUMNS`**, and it binds two helpers today:
`history.field_profile`, and `data_inventory.column_schema` / `payload_key_schema` / `redact_sample`
(the two schema helpers share `_schema_row`, so one list reaches both). 🚨 **When a new sensitive
column appears, extend the list — not the call sites**, which is the whole point of having it.

Added by R-052, from R-050 F1: a 34-character Windows build string across 24,816 records reached a
rendered report, and R-009's method C caught it at 1 of 76 — understating it, because `example` and
`min` were raw as well and `max` was a raw value truncated at 24 characters.

---

## 1. `01_data_inventory.ipynb` — every source this project can reach

**Purpose:** a complete, honest inventory of every table reachable from the Spotify Web API, the
Extended Streaming History export and MusicBrainz, and of the warehouse built from them, with a real
sample of each, so the breadth (and the holes) are visible in one document. Renamed from
`01_api_inventory.ipynb` by R-042: when it was written, "reachable" meant only the API.

**Heading hierarchy — this is the contract:**

```
# Spotify Lakehouse — Data Inventory                  (H1: report title)
  [intro prose: what this is, when it was run, which profile, the 5-user and Nov-2024 caveats]

## 1. Identity & Account                              (H2: domain)
### 1.1 /me — Current User Profile                    (H3: one per endpoint = one "table")
#### Endpoint & scope                                 (H4: what it is, what it costs)
#### Schema                                           (H4: field, type, null-rate, example)
#### Sample                                           (H4: 5 redacted rows)
#### Notes & limits                                   (H4: callouts for anything surprising)

## 2. Library
### 2.1 /me/tracks — Saved Tracks
### 2.2 /me/albums — Saved Albums
### 2.3 /me/following — Followed Artists

## 3. Playlists
### 3.1 /me/playlists — Playlist Inventory
### 3.2 /playlists/{id}/items — Playlist Contents

## 4. Listening
### 4.1 /me/player/recently-played — Recent Plays
### 4.2 /me/top/artists — Top Artists (×3 time ranges)
### 4.3 /me/top/tracks — Top Tracks (×3 time ranges)
### 4.4 Local date versus UTC date                    (added R-008, confirmed R-033: how many plays move
                                                       date under UTC grouping, with a captioned figure)
### 4.5 The 50-item window is the whole API history   (added R-008, confirmed R-033: play count and
                                                       span vs api_coverage_start; what needs the export)

## 5. Catalog Enrichment
### 5.1 /artists/{id} — Artist (`genres` absent)      (R-042: absent, not deprecated — 448 of 448 objects)
### 5.2 /albums/{id} — Album
### 5.3 /tracks/{id} — Track
### 5.4 /shows/{id} & /episodes/{id} — Podcasts
### 5.5 /search — Catalog Search

## 6. What the API Cannot Give                         (H2: the negative space, and what fills it)
### 6.1 Removed endpoints (Nov 2024)
### 6.2 What the API cannot give, and what fills it   (rewritten R-042: each gap names its filler or
                                                       states that nothing fills it)
### 6.3 Field-level deprecations

## 7. Coverage Summary                                 (H2)
   [one table: API endpoint × reachable? × rows sampled × fields × feeds which warehouse table]

## 8. The Extended Streaming History Export            (added R-042. Each H3 below carries the same four
                                                       H4s, "Table & source" in place of "Endpoint & scope")
### 8.1 raw.export_record — one row per export record  [key census, per-file counts and dates, content types]
### 8.2 stg_export__play_record — the export, typed

## 9. MusicBrainz                                      (added R-042)
### 9.1 raw.external_response — MusicBrainz `isrc_lookup`
### 9.2 raw.external_response — MusicBrainz `artist`, and the `genre` / `tag` split

## 10. The Warehouse                                   (added R-042)
### 10.1 fct_play_event — one row per play             [by source_system and content_type]
### 10.2 dim_content — track, episode, audiobook chapter  [resolved vs unresolved]
### 10.3 dim_artist — SCD Type 2 on sourced genres
### 10.4 dim_genre and br_artist_genre
### 10.5 The allocation reconciliation — four steps and their residuals

## 11. The Queryable Catalog                           (added R-049, for "what can I query at all".
                                                       Introspected at run time from information_schema
                                                       and pg_catalog — never hand-typed, or it goes
                                                       stale the first time a model lands)
### 11.1 Connecting, and where the schema names come from  [ctx.frame/rows/scalar; session-scoped
                                                       schema names as found; analytics measured]
### 11.2 Start here                                    [the five objects that answer most questions]
### 11.3 Three worked joins                            [plays → content → artist → genre]
### 11.4 The full catalog                              [every object, by layer: kind, rows counted in
                                                       the run, columns, its own header comment, and a
                                                       runnable example query that is executed]
```

**Sections 6 and 7 are not optional and not filler.** Section 6 is the single most useful page in the
document — it is what stops a future round from spending an afternoon looking for audio features.
Section 7 is the map from API surface to warehouse model.

**Implementation notes (Claude Code's call, but these are the constraints):**
- Every endpoint section is generated by one helper that takes an endpoint spec and emits
  `display(Markdown('### …'))` plus the four H4 subsections. Thirty hand-written cells will drift.
- The schema table is derived from the actual response, not from documentation. Where a documented
  field is absent from the live response, that is a `> [!WARNING]` callout — it is exactly the kind of
  drift that breaks an extractor six months later.
- Rate limits: 30-second rolling window, unpublished ceiling, dev-mode is lower. Sleep between calls
  and honor `Retry-After`. A notebook that 429s halfway through renders a broken report.
- `--toc-depth 3` so the sidebar shows domains and endpoints but not the four H4s under each.

---

## 2. `02_listening_patterns.ipynb` — one profile's listening

**Purpose:** the actual analysis. Reads the marts, not the API.

```
# Listening Patterns                                   (H1: report title. R-009: not the display name —
                                                       dim_profile.display_name is Spotify's /me
                                                       display name, which §0's PII rule redacts;
                                                       the slug goes in the intro and the filename)
  [intro: profile slug, coverage window, source mix, the ms_played caveat, stated plainly]

## 1. Coverage & Caveats
   [Non-negotiable opening section. Export window, API window, % rows with duration,
    content resolution rate. Every number downstream inherits these limits and the
    reader sees them before any chart.]

## 2. Volume Over Time
### 2.1 Daily listening minutes                       [line of average minutes per day; daily with a
                                                       7-day rolling mean for a window up to two years,
                                                       weekly with a 13-week rolling mean beyond (the
                                                       grain rule below; built weekly, R-009 F11)]
### 2.2 Monthly totals                                [bar]
### 2.3 By day of week and daypart                    [heatmap: dow × daypart]
### 2.4 Play count vs. qualified plays                [both series; the gap is the skip rate]

## 3. Music vs. Podcasts
### 3.1 Share of listening time                       [stacked area, monthly]
### 3.2 Share of play count                           [same, and the divergence is the point —
                                                       podcasts are long, so time-share and
                                                       count-share tell different stories]
### 3.3 Podcast leaderboard                           [top shows by total hours, episodes, mean
                                                       completion]
### 3.4 Podcast listening by daypart

## 4. Genre Spread
### 4.1 Allocation reconciliation                     [MANDATORY FIRST. total → music →
                                                       artist-identified → genre-allocated ms, with
                                                       the unclassified residual named. A radar
                                                       chart without this is decoration.]
### 4.2 Current spread                                [radar, trailing 12 months, 13 buckets]
### 4.3 Longitudinal                                  [small-multiple radars by year, plus a
                                                       stacked-area share-over-time — the area
                                                       chart is what actually shows a shift; the
                                                       radars show the shape at a point]
### 4.4 Drill-down                                    [plotly, click a bucket → constituent raw
                                                       genres → top artists in that bucket for the
                                                       selected period]

## 5. Artists & Tracks
### 5.1 Top artists by era                            [rank-flow across years]
### 5.2 Tracks with the longest tail                  [first play to last play, still active]
### 5.3 One-hit wonders                               [heavy in one month, absent after]

## 6. Behavior
### 6.1 Skip rate over time                           [by reason_end]
### 6.2 Shuffle vs. deliberate                        [reason_start distribution]
### 6.3 Offline listening                             [proxy for travel]
### 6.4 Platform mix over time
### 6.5 Relocation signals — country and hour-of-day phase
                                                      [added R-009 for R-038: `conn_country` by date
                                                       range, and each month's circular-mean local
                                                       hour against a rolling ±6-month baseline, with
                                                       runs of shifted months as date ranges. Reports
                                                       the shift and its dates, never a place: the
                                                       section's own text says why]
```

**§4.2–§4.4 are built on the bucket seed (R-015).** They read `int_genre_bucket_allocation`, which maps
curated genres through `seeds/genre_bucket_map.csv` (unlisted genres fall to `other`). Each radar prints the
reconciliation's four steps and three gaps, the share of the period's music time it is drawn on, and the
`unclassified` footnote. §4.4 is one self-contained plotly figure: a sunburst of bucket → genre → top
artists, with a menu of periods pre-computed into it, so it works from `file://` with no server.

**Choosing the profile (R-009).** The notebook's first cell is `ctx = setup()`, with no literal:
`setup()` takes its profile from the `SPOT_PROFILE` environment variable (default `marc` when unset).
`make report-02 PROFILE=<slug>` sets it, checks the slug against `spot_meta.profile_registry` first
(`python -m spotify_lakehouse.notebook`, which names the registered slugs on failure), and renders
`reports/02_listening_patterns_<slug>.html`. No papermill and no new dependency. A registered profile
with no export still renders: §1 says no export is loaded, and each export-only panel says what is
missing instead of drawing.

**Chart contracts:**
- **Radar (§4.2, §4.3):** normalize to *share of allocated music time*, never raw milliseconds — raw
  values make a heavy-listening year swamp a light one and the shape becomes unreadable. Fixed
  bucket order and fixed colors across every radar so year-over-year small multiples are comparable.
  Print the underlying ms as a table beneath.
- **Drill-down (§4.4):** plotly with a Dash-free interaction model — the report is a static HTML file
  and must work from disk with no server. Pre-compute the drill levels into the figure.
- **Every time series** clips to the coverage window from §1 and shades any partial period at the
  edges. An export that ends mid-month must not render as a collapse in listening.
- **Weekly grain, not daily,** for anything spanning more than two years. Daily over a decade is
  4,000 points of noise.

**Multi-profile:** built for one profile at a time via `SPOT_PROFILE` (above), so the same notebook
runs for `marie`, `brody`, `emma` once each is registered. A family comparison notebook (`04_family_comparison.ipynb`, R-044) is a
later round and is not specified here — it should not be attempted until §1–§6 are real for one
person.

---

## 3. `03_history_profile.ipynb` — the export's own shape

**Purpose:** profile the Extended Streaming History file itself — distributions, cardinalities, gaps and
oddities — so that what nobody thought to ask about has somewhere to surface. Notebook 01 says what tables
exist; notebook 02 answers questions about listening; this one characterises the source those answers rest
on. Added by R-050.

**Scope:** the export only, read from `stg_export__play_record` and the marts. **Zero API calls** — the
API contributes a few hundred rows with no duration, and mixing them into a profile of the file would
misstate every distribution here. Same `SPOT_PROFILE` mechanism as notebook 02 (R-009):
`make report-03 PROFILE=<slug>` checks the slug against `spot_meta.profile_registry` first and renders
`reports/03_history_profile_<slug>.html`.

```
# History Profile                                      (H1: report title)
  [intro: what this profiles, which profile, that it reads the warehouse and calls no API]

## 1. Coverage & Provenance                            [raw records and files, staged rows, window,
                                                        hours, and the positive/zero/NULL duration split]

## 2. Field Profile                                    [every column of stg_export__play_record: type and
                                                        null rate from the database, cardinality, range
                                                        and modal value from the run's rows]

## 3. Temporal Shape
### 3.1 Records per month                              [records and hours, captioned; months absent]
### 3.2 Silent stretches                               [longest gaps, with the "silence is not absence"
                                                        callout — a gap proves Spotify recorded nothing]
### 3.3 Bursts                                         [heaviest days, and what was playing on the top one]
### 3.4 Per-file coverage                              [each export file's rows and date span, and why
                                                        overlapping spans are not duplicate rows]

## 4. Content Shape
### 4.1 The three URI types                            [track / episode / audiobook_chapter, by records
                                                        and by hours — the two shares differ]
### 4.2 The long tail                                  [items played ≥ N times; the play-count histogram]
### 4.3 Resolution                                     [resolved vs unresolved plays and items, by type]
### 4.4 Repeat behaviour                               [most-replayed tracks and their first→last span]

## 5. Quality & Oddities                                (the section that earns the notebook)
### 5.1 Zero-duration plays, and why they are not NULLs
### 5.2 Empty reason_start and reason_end
### 5.3 offline_timestamp's mixed units                 [seconds and milliseconds in one column]
### 5.4 Duplicate natural keys                          [what the second-precision dedupe collapses]
### 5.5 reason_end = trackerror
### 5.6 What else the profiling found                   [generated from the data, not hand-listed, so a
                                                        new oddity appears without a code change]

## 6. Platform & Context
### 6.1 Platform strings over time                      [device families by year, captioned]
### 6.2 conn_country                                    [including ZZ, which is not a country]
### 6.3 Skipped, shuffle, offline, incognito             [rates by year, captioned]
```

**A play with no duration is not a play of zero seconds.** `ms_played = 0` is the export recording that a
play produced no listening time; NULL is the API recording nothing at all. They are counted separately
everywhere — `history.duration_profile` returns both and never a total that folds one into the other, and
`tests/test_history.py::test_a_play_with_no_duration_is_not_a_play_of_zero_seconds` fails if they merge.
§5.1 is about the real zeros; conflating them is the failure that section exists to expose.

**Every count is measured in the run.** No figure in the prose is typed from a prior round's report — the
`trackerror` count, the collapsed-duplicate count and the millisecond-timestamp count all moved between
R-003 and R-050, and a hard-coded number would have gone quietly stale.

**PII:** platform strings are never displayed raw (they can name a device model, R-009) — they are grouped
into families. Every sample passes `redact_sample`, which applies `redact_profile` plus R-042's
identifier mask. `ip_addr` and `user_agent` are discarded at load and never reach the warehouse.


---

## 5. `05_sandbox.ipynb` — scratch queries, and the playlist comparison

**Purpose:** somewhere to try a query without disturbing 01, 02 or 03. Added by R-054 together with
the playlist ingest its headline query needs — a sandbox shipped alone would have had §4 return zero
rows, because no playlist had ever been stored.

**Scope:** reads the warehouse, makes **zero API calls**. Same `SPOT_PROFILE` mechanism as 02 and 03
(R-009): `make report-05 PROFILE=<slug>` checks the slug against `spot_meta.profile_registry` first
and renders `reports/05_sandbox_<slug>.html`.

```
# Sandbox                                             (H1: report title)

## 1. Connect                                         [setup(), and the profile it resolved]
## 2. The API, in one screen                          [ctx.frame / ctx.rows / ctx.scalar / ctx.conn,
                                                       each with a worked one-liner. It is
                                                       `ctx.conn` — R-049 F5 shipped `ctx.con` in the
                                                       section teaching people how to query]
## 3. What you can query                              [a pointer to 01 §11's catalog, never a second
                                                       copy of it: a copy drifts the first time a
                                                       model lands]
## 4. Playlist comparison
### 4.1 The playlists that were loaded                [name, track count, first seen. No owner — see
                                                       below]
### 4.2 Resolving the two names                       [case-insensitive; zero or several matches
                                                       print the candidates and stop]
### 4.3 The answer                                    ["Tracks in A that are not in B", artist+title]
### 4.4 All three tiers, side by side                 [URI / ISRC / normalized name. Tier 2's
                                                       coverage share is printed, not assumed: it is
                                                       1.3% of dim_content but 99.6% of playlist
                                                       tracks, because the playlist payload carries
                                                       external_ids.isrc itself]
### 4.5 The other direction                           [B→A: the check that A→B is not an artefact of
                                                       one playlist being larger]
### 4.6 How much of this the warehouse already knew   [playlist tracks with no dim_content row]

## 5. Scratch                                         [empty, and meant to stay that way in git]
```

**`miss` is directional and every output says so.** data-contracts §5: "47 misses between A and B" is
meaningless; "47 tracks in A that are not in B" is the answer. `playlists.misses` returns a
`DirectionalMiss` whose `label` carries the direction, and
`tests/test_playlists.py::test_misses_are_directional` fails if the two directions ever return the
same set.

**No owner column, anywhere.** A playlist's owner is a person who is not necessarily Marc, and this
repository is public, so `items[].owner.*` and `items[].added_by` are discarded at ingest (R-054) —
discard beats redact, because a field never written cannot leak from a table nobody thought to check.
The consequence is stated in §4.1 rather than worked around: **"is this playlist mine?" is not
answerable from the warehouse.**

**A sandbox is edited by design, and §7 wants a clean tree.** The resolution: the notebook is tracked
at a known-good state, `nbstripout` strips outputs on commit as it does for every other notebook, and
**§5 is committed empty** so a diff shows only what Marc added. His own edits are his to keep or
discard; nothing here tries to stop him dirtying it.
