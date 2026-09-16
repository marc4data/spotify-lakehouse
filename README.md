# spotify-lakehouse

A local Postgres warehouse of Spotify listening data for one family, modeled to Kimball dimensional
standards and surfaced through Jupyter notebooks and Tableau Public.

Governing documents: [CLAUDE.md](CLAUDE.md), [docs/data-contracts.md](docs/data-contracts.md),
[docs/worktree-protocol.md](docs/worktree-protocol.md).

> **This repository is public and the data is personal.** No credential, token, account identifier,
> raw export, or notebook output is ever committed. See CLAUDE.md §5.

## Quick start

```bash
echo main > .session          # untracked; `wta`, `wtb`... in a worktree
make bootstrap                # uv sync, ~/.config/spot/, Postgres, migrations, hooks
# first run: fill in the Spotify Client ID/Secret it names, then re-run
make dbt-profile              # once per machine: adds the `spot` profile to ~/.dbt/profiles.yml
uv run spot auth marc         # browser login; refresh token -> ~/.config/spot/tokens.json
uv run spot probe --profile marc
```

## New worktree

```bash
make worktree NAME=wta          # ../spotify-wta on branch feat/wta: .session, bootstrap, and a summary
code -n ../spotify-wta          # open it in its own VS Code window
make worktree-remove NAME=wta   # from main: removes it, deletes the branch if merged, drops its schemas
```

Session names are `wta` … `wtz`. The worktree shares credentials, the database and `data/raw` with every
other checkout, and reads the PM folder (`~/projects/ai_orchestrator_claude/spotify-pm`) by absolute path;
nothing else is configured by hand. `make worktree-remove` refuses a worktree that holds uncommitted or
untracked work.

## Layout

| Path | What |
|---|---|
| `spotify_lakehouse/` | Python package: `config`, `auth`, `api`, `db`, `migrate`, `raw_store`, `redact`, `cli` |
| `migrations/` | Numbered SQL for the shared `raw` schema. Never edit an applied file; add a new one. |
| `dbt/` | dbt-postgres project. Schemas `stg_<session>`, `mart_<session>`; `analytics` is main-only. |
| `seeds/` | Marc-editable seed files (e.g. `genre_bucket_map.csv`) |
| `notebooks/` | Committed with outputs stripped (nbstripout) |
| `scripts/` | `bootstrap.sh`, `dbt.sh`, pre-commit hooks |
| `data/raw` | Symlink to `~/spot-data/raw`. Gitignored. |

## Commands

| Command | Does |
|---|---|
| `uv run spot auth <profile>` | OAuth Authorization Code login at `http://127.0.0.1:3000` |
| `uv run spot probe --profile <p> [--shape]` | `GET /me` + `GET /me/player/recently-played`; writes `data/raw/` and `raw.api_response` |
| `uv run spot resolve-tracks --profile <p> [--run] [--limit N]` / `make resolve-tracks PROFILE=<p>` (dry run) | `GET /tracks/{id}` for export track URIs so their plays reach an artist and an ISRC. **Dry run unless `--run`**: batch `GET /tracks?ids=` is 403 to this app, so it is one call per track (~13 h for ~47k). Resumable. |
| `uv run spot load-export --profile <p>` / `make load-export PROFILE=<p>` | Unzip `~/spot-data/exports/<p>/*.zip` beside itself (never overwriting) and load Extended Streaming History into `raw.export_record`; `ip_addr`/`user_agent` discarded before insert; re-run inserts 0 |
| `uv run spot resolve-musicbrainz` / `make musicbrainz` | ISRC → MusicBrainz recording → primary artist genres and tags into `raw.external_response`; resumable (skips every id already stored); needs `MUSICBRAINZ_CONTACT` in `~/.config/spot/.env` |
| `uv run spot migrate` / `make migrate` | Apply raw migrations; create this session's schemas |
| `make dbt-parse` | `dbt parse` with `SPOT_SESSION` from `.session` |
| `make test`, `make lint` | pytest; ruff check + format check |
| `make notebook` | Start Jupyter Lab (`uv run jupyter lab`) to open and run notebooks interactively |
| `make report-01` | Execute `notebooks/01_data_inventory.ipynb` (API, export, MusicBrainz, warehouse), render it to `reports/01_data_inventory.html`, strip its outputs again |
| `make report-02 PROFILE=<slug>` | Execute `notebooks/02_listening_patterns.ipynb` for one registered profile (read from the warehouse, no API calls), render it to `reports/02_listening_patterns_<slug>.html`, strip its outputs again. An unregistered slug fails first, naming the registered ones |
| `make report-03 PROFILE=<slug>` | Execute `notebooks/03_history_profile.ipynb` for one registered profile (the export's own shape, distributions and oddities; warehouse only, no API calls), render it to `reports/03_history_profile_<slug>.html`, strip its outputs again |
| `make worktree NAME=wtc` / `make worktree-remove NAME=wtc` | Create and provision / remove a parallel worktree (see "New worktree") |

## Notebooks and reports

Notebooks open with `ctx = setup(profile="marc")` from `spotify_lakehouse.notebook`. It holds the connection,
so no notebook contains a connection string or a credential. Rendered HTML goes to `reports/`, which is
gitignored; notebooks are committed with outputs stripped (nbstripout).

Rendering uses **nb2report** from a **local checkout**, expected beside this repo at `../nb2report`
(override with `make report-01 NB2REPORT=/path/to/nb2report`). It is attached per run with
`uv run --with-editable`, **not** recorded in `pyproject.toml` or `uv.lock`: a path dependency in the lock
makes `uv sync --locked` fail for anyone without that directory, CI included. nb2report's remote is not
assumed to be public, so it is not referenced by git URL either.

## dbt conventions

- **One `.yml` per model file.** `models/marts/fct_play_event.yml` sits beside
  `fct_play_event.sql`. Never a directory-wide `schema.yml` — parallel worktrees would conflict on it.
- Layers: `staging` → `intermediate` → `marts`. Lowercase SQL, trailing commas, CTEs over subqueries.
- `_key` surrogate keys, `_id` source natural keys, `_uri` Spotify URIs. Timestamps `timestamptz` UTC;
  durations integer milliseconds.
- Run dbt through `scripts/dbt.sh` (or `make dbt-parse`), which sets `SPOT_SESSION` from `.session`.

## License

MIT
