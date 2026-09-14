# spot-main-R-008 — The EDA notebook, and the first thing Marc can look at

**Session: `main`. Read `docs/notebook-specs.md` §0 and §1 before starting. They are the contract.**

Six rounds of scaffolding, migrations, a poller and a paging test have produced nothing Marc can open.
`notebooks/` has held one `.gitkeep` since the first round. **This round's deliverable is a rendered HTML
report he can read**, not a passing test suite.

## What this is

Marc's original ask: *"a Jupyter notebook that imports other files to connect to the backend db we use,
and pulls in all the necessary packages. Seed it with an initial exploratory data analysis to demonstrate
the breadth of data available in the api. It should have a sample of every table we can access from the
api."* Then render it with **nb2report**.

`docs/notebook-specs.md` §1 carries the full heading hierarchy. **It is the contract — build to it.** But
it was written before the warehouse existed, and two things have changed:

| Then | Now |
|---|---|
| Everything would be a live API call | `mart_main` holds 64 plays, 43 tracks, 45 artists, 38 albums. **Read the warehouse where the data has landed; call the API live only for surfaces not yet extracted.** Every section says which it did. |
| §6 "What We Cannot Get" was three bullets | It is now the most valuable page in the document. See below. |

## §0 is not optional

`spotify_lakehouse.notebook.setup()` must exist as a real module and the notebook must open with it:

```python
from spotify_lakehouse.notebook import setup

ctx = setup(profile="marc")
```

It resolves `.session`, reads `~/.config/spot/`, opens the Postgres connection, imports and configures
pandas / plotly / matplotlib, and returns the context. **A notebook that runs standalone without the
package is a notebook with credentials in it.** No connection string appears in any cell.

## §6 — what we cannot get, and why it earns its place

This is the page that stops a future round spending an afternoon looking for something that is gone.
Each item with the round that measured it:

- **Artist `genres`: absent, not deprecated.** 55/55 objects across three endpoints (R-004 §1).
- **`/audio-features`, `/audio-analysis`, `/recommendations`, `/related-artists`**: cut off for apps
  registered after 2024-11-27.
- **`recently-played` returns no podcast episodes at all.** Music-vs-podcast time is export-only.
- **No backward paging.** Following `next` returned 0 items and `next = null` while 14 older plays were
  demonstrably available a day earlier (R-018).
- **No listening duration from the API.** `ms_played` is NULL on all 64 rows, and every aggregate in the
  notebook that touches duration says so rather than printing a zero that looks like a measurement.
- **Batch `GET /artists?ids=` is 403 in dev mode** (R-004 S3).
- **`popularity`, `followers`, `available_markets`, `preview_url`**: absent keys, not nulls.

## Two findings worth a section of their own

- **96% of plays fall on a different calendar date in local time than in UTC** (R-004 §5, 48 of 50).
  Show it. It is the most load-bearing modelling decision in the project and it looks like pedantry until
  you see the number.
- **The 50-item window is the whole API history.** Show `fct_play_event` row count against
  `dim_profile.api_coverage_start`, and say plainly that everything before it needs the export.

## nb2report

Install from `~/projects/ai_orchestrator_claude/nb2report` as a **dev dependency via local path**.
Its GitHub remote is `marc4data/nb2report-analytics`; **do not assume that repo is public** — the
visibility question is still open, and a git-URL dependency in a public `uv.lock` that resolves to a
private repo breaks for every other reader. Local path, documented in the README.

Authoring rules from §0, because they decide how the notebook is written:

- **Code cells are hidden in the report.** Every code cell needs a preceding markdown cell.
- **`print()` is excluded.** Use `display()`.
- Headings drive the TOC and the collapsible tree — the hierarchy in §1 is the structure.
- `display(Markdown('### …'))` from a loop is how one helper emits a section per endpoint. Thirty
  hand-written cells drift.
- Every data-quality finding gets a `> [!WARNING]` or `> [!CAUTION]` callout — they drive the
  "Issues Only" filter, which is the fastest review path.
- Render with `--author "Marc Alexander" --toc-depth 3` to `reports/`, which is gitignored.

## PII — the trap this notebook walks straight into

`/me` returns `account_id` and, with the wrong scope, `email`. **Every display of profile, user, owner or
follower data passes through `spotify_lakehouse.redact.redact_profile()`.** Notebooks commit stripped via
nbstripout; the rendered HTML stays in gitignored `reports/`. Both are required; neither alone is enough.

## Definition of done

`CLAUDE.md` §7 in full, plus:

- `uv run nb2report notebooks/01_api_inventory.ipynb --author "Marc Alexander" --toc-depth 3 -o reports/01_api_inventory.html` runs clean.
- **A guard proven to fail:** stage a break in `redact_profile` and show the test that goes red. A
  redaction nobody has watched fail is not a redaction.
- The report states its own coverage: how many sections read the warehouse, how many called the API live,
  and how many endpoints in §1's hierarchy could not be sampled at all and why.

## Constraints

- Do not amend a contract. `docs/notebook-specs.md` §1's hierarchy is binding; if a section cannot be
  built as specified, that is a finding to report.
- Rate limits: 30-second rolling window, dev-mode ceiling unpublished. Sleep between calls, honour
  `Retry-After`. A notebook that 429s halfway renders a broken report.
- Nothing under `data/` or `reports/` gets committed.

## Report, then hand yourself back

`claude_work/reports/spot-main-R-008-report.md`, ending with the return handoff cell and the clock line.
**Also tell Marc the path to the rendered HTML** — the deliverable is the thing he opens, not the report
about it.
