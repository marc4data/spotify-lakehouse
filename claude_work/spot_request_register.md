# Request Register — spot

Cowork owns this file. Sessions never mint their own numbers.
Format: `spot-<session>-R-###`. Bare numbers below; the session is the section heading.

Status: ✅ landed · 🔨 in flight · 📋 queued · ⏸️ parked · 💭 deferred · ❓ unverified · ⚠️ dropped

---

## spot-main

| ID | Status | Request | Rounds | Notes |
|---|---|---|---|---|
| R-001 | ✅ | **Project CLAUDE.md and governing documentation** | 0 | "Build a claude.md for this project folder to establish the guidelines for this effort." Delivered 2026-09-13: `CLAUDE.md`, `docs/data-contracts.md`, `docs/worktree-protocol.md`, `docs/notebook-specs.md`. |
| R-002 | 📋 | **Repo scaffold, uv env, Docker Postgres, dbt project, OAuth, first verified API call** | 0 | Marc chose "scaffold + auth + first real API call." Prompt: `prompts/spot-main-R-002.md`. |
| R-003 | ⏸️ | **Ingest Extended Streaming History export files** | 0 | Parked on Spotify delivery (~weeks). Loader is built against the documented schema and a synthetic fixture; real files swap in on arrival. |
| R-004 | 📋 | **Kimball star schema in dbt-postgres** | 0 | Contract in `docs/data-contracts.md` §2–§4. Grain, keys and SCD policy are fixed; everything else is Code's. |
| R-005 | 📋 | **Multi-profile utility — swap between Marc, Marie, Brody, Emma** | 0 | "the account profile utility should be able to swap between the profiles." `dim_profile.profile_slug` is the address. Export-only contributors get a profile too. |
| R-006 | 📋 | **Playlist inventory, overlap and miss analysis** | 0 | "explore/inventory my playlists, compare playlists to overlaps and misses." Semantics fixed in contracts §5 — miss is directional, two match modes. |
| R-007 | 📋 | **Tableau Public extract publication** | 0 | Tableau Public cannot source a database. `published/` emits flat extracts from marts; `make publish` regenerates. |
| R-008 | 📋 | **EDA notebook — a sample of every table reachable from the API** | 0 | Spec: `docs/notebook-specs.md` §1. Heading hierarchy is the contract. Rendered via nb2report. |
| R-009 | 📋 | **Listening-patterns notebook — volume, music vs podcast, top podcasts, genre radar, longitudinal drill** | 0 | Spec: `docs/notebook-specs.md` §2. Depends on R-003 for podcast and duration data. |
| R-010 | ✅ | **Parallel VS Code sessions must work — worktrees with full connectivity and permissions** | 0 | "make sure the work pattern supports spinning subsequent VS Code sessions (worktrees)." Protocol in `docs/worktree-protocol.md`; enforcement lands with R-002's `make bootstrap`. |
| R-011 | ✅ | **NEVER let Spotify API connectivity data leak to public GitHub** | 0 | Marc, verbatim: "NEVER let any Spotify API connectivity data leak to GitHub." Three-layer enforcement specified in `CLAUDE.md` §5: secrets outside the tree, gitleaks + custom hook, nbstripout. Moves from ✅-as-specified to ✅-as-enforced when R-002 installs the hooks. |
| R-012 | ✅ | **Hard separation — Cowork manages, Claude Code builds** | 0 | "Hard separation between Church and State." `CLAUDE.md` §1. Marc chose the "contracts specified, internals free" model. |
| R-013 | ✅ | **Guard against accidentally using Chat instead of Cowork for this project** | 0 | Marc: "Chat doesn't have enough memory or shared context and I consider using it a mistake." Two mechanisms: `CLAUDE.md` §3 (the register-table tell), and a project-memory note so a Chat session reads the rule and redirects him. |
| R-014 | ⏸️ | **Request Extended Streaming History for all four accounts** | 0 | Marc's action, not a build. Long lead time — this gates R-003 and half of R-009. |
| R-015 | ⏸️ | **Genre bucket taxonomy — sign-off needed** | 0 | 13 buckets proposed in contracts §3. Rap and hip-hop merged because Spotify's taxonomy does not separate them; flagged rather than done silently. Seed file `seeds/genre_bucket_map.csv` is Marc-editable. |
| R-016 | 📋 | **Verify empirically whether added dev-mode users need their own Premium** | 0 | Spotify's docs specify the app owner only. Unverified. Tested during family onboarding; code must handle the failure path either way. |
| R-017 | 📋 | **launchd agent — poll recently-played every 30 min for all profiles** | 0 | Marc chose 30 min. Installed once from `main`, shares the refresh advisory lock. |
