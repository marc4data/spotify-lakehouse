# spot-main-R-002 — Build report

**Session:** `main` · **Date:** 2026-09-13 · **Commit:** `3a499cf` (scaffold) + this report
**Status for Cowork:** all acceptance items are met. Marc ran the credential-dependent steps on 2026-09-13
(PDT): OAuth login, a live probe from `main`, and a live probe from `spotify-wta` with no extra setup (§4,
§6). Observed API shapes are in §7 (formats and enums in §7.4, full key-path listing in Appendix A); new
contract findings F7–F10 are in §8. Ready for Cowork review.

---

## 1. Acceptance scorecard

| Acceptance item | Result | Evidence |
|---|---|---|
| `make bootstrap` from clean state with `~/.config/spot/` absent prints the exact missing-credential message | ✅ | §3.1, §3.2 |
| `make bootstrap && spot auth marc && spot probe --profile marc` writes JSON + a row | ✅ run `probe-main-208f7c73` → 2 files in `data/raw/`, `raw.api_response` ids 5–6 | §6 |
| Worktree `spotify-wta` bootstraps and uses the same credentials with no additional setup | ✅ run `probe-wta-8cdee8b3` from `spotify-wta` → 2 more files, ids 7–8. Same `.env`, same token store, same container, same `data/raw` | §4 |
| Staged fake 32-hex string rejected by the hook | ✅ rejected by **two** hooks: `spot-no-spotify-secrets` and `gitleaks` | §2.1 |
| Notebook with populated output stripped on commit | ✅ `nbstripout` | §2.2 |
| `uv run pytest`, `uv run ruff check .`, `uv run dbt parse` pass | ✅ 64 passed · ruff clean · parse 0 errors (see §5 note on how dbt is invoked) | §2.3 |

---

## 2. Proven guards (CLAUDE.md §7)

### 2.1 32-hex string → commit rejected

Staged `scratch_guard/leak_test.env.txt` containing `SPOTIFY_CLIENT_SECRET=<openssl rand -hex 16>`.
The value is redacted below. The hook output itself never prints the matched value.

```
Detect hardcoded secrets........................................................Failed
- hook id: gitleaks
Finding:     SPOTIFY_CLIENT_SECRET=REDACTED
RuleID:      generic-api-key
File:        scratch_guard/leak_test.env.txt
WRN leaks found: 1

spot — reject 32-hex strings and personal email addresses.......................Failed
- hook id: spot-no-spotify-secrets
- exit code: 1

scratch_guard/leak_test.env.txt:1: 32-hex string (Spotify client ID / secret shape)

Commit rejected by spot-no-spotify-secrets. Credentials and account identifiers belong in ~/.config/spot/, never in the repo (CLAUDE.md §5).
git commit exit=1
HEAD still 3a499cf
```

### 2.2 Notebook outputs → stripped on commit

```
before commit  -> outputs: 1 | execution_count: 7
nbstripout......................................................................Failed
- hook id: nbstripout
- files were modified by this hook
git commit exit=1
after hook     -> outputs: 0 | execution_count: None
index after re-add: 1 empty outputs array(s), 0 output text line(s)
```

The proof notebook was unstaged and deleted afterwards. Nothing was committed.

### 2.3 Staged breaks → named tests red → reverted

| Break | Test that went red | Reverted |
|---|---|---|
| Emptied `DISCARDED_FIELDS` in [raw_store.py](../../spotify_lakehouse/raw_store.py), so `/me` email would be persisted | `tests/test_raw_store.py::test_scrub_removes_email_from_me_without_mutating_input` — 1 failed, 4 passed | ✅ file restored, `git status` clean |
| `alter table raw.api_response disable trigger api_response_no_update_delete` | `tests/test_migrate.py::test_raw_api_response_is_append_only` — `Failed: DID NOT RAISE RaiseException` | ✅ trigger re-enabled (`tgenabled = O` for both triggers); `raw.api_response` has 0 rows, since the test rolls back |
| Removed `account_id` from `redact.SENSITIVE_FIELDS` (added after the live probe, F8) | `tests/test_redact.py::test_redact_profile_masks_identifiers_keeps_analytics_fields` — 1 failed, 3 passed | ✅ file restored; full suite 64 passed |

After the reverts: `64 passed`, `ruff check` clean, `ruff format --check` clean, `dbt parse` 0 errors.

---

## 3. Bootstrap contract (worktree-protocol §5)

### 3.1 First run in `main`, `~/.config/spot/` absent

Bootstrap created `~/.config/spot/` (mode 700) and `.env` (mode 600, with a random Postgres password), then
kept going. It started Postgres, ran migrations, linked `data/raw`, and installed hooks. It exits non-zero
with:

```
[credentials-missing]
    /Users/marcalexander/.config/spot/.env is missing required key(s): SPOTIFY_CLIENT_ID SPOTIFY_CLIENT_SECRET
    Fix: 1. developer.spotify.com/dashboard -> your app -> Settings: copy Client ID and Client Secret
         2. edit /Users/marcalexander/.config/spot/.env and replace each replace-me value (it is chmod 600, outside the repo)
         3. confirm the dashboard Redirect URI is exactly http://127.0.0.1:3000
```

### 3.2 Each named failure, from a fresh `git clone` in a scratch directory

| Condition | Name printed | Fix printed |
|---|---|---|
| No `.session` | `[session-missing]` | `echo main > .session` / `echo wta > .session` |
| `.session` = `prod` | `[session-unrecognized]` | overwrite with `main` or `wta/wtb/...` |
| `.session` = `main` inside a linked worktree | `[session-unrecognized]` | extra guard: a worktree claiming `main` would build into `analytics` |
| `pyproject.toml` edited without re-locking | `[lockfile-mismatch]` | `uv lock`, commit together |
| `SPOT_CONFIG_DIR` empty | `[credentials-missing]` | as above (dir 700 / file 600 verified) |
| Docker unreachable (`DOCKER_HOST` pointed at a dead socket) | `[docker-not-running]` | open Docker Desktop, re-run |
| Container not healthy in 90 s | `[container-unhealthy]` | `docker compose logs`, port-5433 check (code path, not staged) |
| `profiles.yml` without `spot:` | `[dbt-profile-missing]` | `make dbt-profile` |

Failures that later steps don't depend on are collected and all reported together. Session and
lockfile failures stop the run immediately.

---

## 4. Worktree demonstration

```
$ git worktree add ../spotify-wta -b feat/probe && echo wta > ../spotify-wta/.session && cd ../spotify-wta && make bootstrap
    ok: session = wta
    ok: virtualenv synced from uv.lock
    FAILED [credentials-missing]          <- same shared ~/.config/spot/.env as main
    ok: Postgres keys present
    ok: spot-postgres healthy             <- same container, not a second one
    ok: 'spot' profile present
    ok: symlink created                   <- data/raw -> ~/spot-data/raw
    migrations applied: none pending      <- raw is shared; main already applied 0001
    session schemas present: stg_wta, mart_wta
    ok: already installed (shared across worktrees)

$ uv run python -c "...config..."
session wta | stg_wta mart_wta | repo_root spotify-wta | db 127.0.0.1 5433 spot | config_dir /Users/marcalexander/.config/spot

$ uv run spot probe --profile marc
spot probe: /Users/marcalexander/.config/spot/.env is missing required key(s): SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET. ...

$ docker ps  ->  spot-postgres  Up (healthy)  127.0.0.1:5433->5432/tcp      (exactly one)
$ schemas    ->  mart_main mart_wta public raw spot_meta stg_main stg_wta
$ git worktree list
/Users/marcalexander/projects/ai_orchestrator_claude/spotify      3a499cf [main]
/Users/marcalexander/projects/ai_orchestrator_claude/spotify-wta  3a499cf [feat/probe]
```

The worktree reached parity with `main` using `.session` and `make bootstrap` alone. Before credentials
existed, its one failure was the same one `main` had.

### Live probes after credentials were placed (Marc, 2026-09-13 PDT)

`spot auth marc` was run once, in `main`. The worktree then probed with the shared token store and no other setup.

| `raw.api_response.id` | Session | `source_file` | `ingested_at` (UTC) |
|---|---|---|---|
| 5 | main | `api/me/marc/20260914T055226Z_probe-main-208f7c73.json` | 2026-09-14 05:52:26 |
| 6 | main | `api/me_player_recently-played/marc/20260914T055228Z_probe-main-208f7c73.json` | 2026-09-14 05:52:28 |
| 7 | **wta** | `api/me/marc/20260914T060909Z_probe-wta-8cdee8b3.json` | 2026-09-14 06:09:09 |
| 8 | **wta** | `api/me_player_recently-played/marc/20260914T060910Z_probe-wta-8cdee8b3.json` | 2026-09-14 06:09:10 |

All four files are under the shared `~/spot-data/raw` (reached through each checkout's `data/raw` symlink)
with mode `-rw-------`. Ids 1–4 were used by the rolled-back inserts in `test_raw_api_response_is_append_only`.
Postgres sequences don't reuse numbers, so no rows are missing.

---

## 5. Files created

| Area | Files |
|---|---|
| Project | `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `README.md`, `LICENSE` (MIT), `Makefile` |
| Secrets scaffolding | `.env.example`, `profiles.yml.example`, `.pre-commit-config.yaml`, `scripts/hooks/check_secrets.py` |
| Bootstrap / ops | `scripts/bootstrap.sh`, `scripts/dbt.sh`, `scripts/install_dbt_profile.sh`, `docker-compose.yml` |
| Package | `spotify_lakehouse/{config,auth,api,db,migrate,raw_store,redact,shape,cli}.py` |
| Database | `migrations/0001_raw_api_response.sql` (table + append-only triggers on update/delete/truncate) |
| dbt | `dbt/dbt_project.yml`, `dbt/macros/generate_schema_name.sql`, empty `models/{staging,intermediate,marts}/` |
| CI | `.github/workflows/ci.yml` — ruff, ruff format, pytest, custom secret scan over tracked files, dbt parse with the template profile, gitleaks-action |
| Tests | `tests/test_{config,auth,api,raw_store,redact,check_secrets,migrate}.py` |
| Placeholders | `notebooks/`, `seeds/`, `dbt/tests/` (`.gitkeep`) |

**Changed outside the repo:** `~/.config/spot/.env` (created, 600) · `~/spot-data/raw/` (created, 700) ·
`~/.dbt/profiles.yml` (`spot` profile appended; backup at `profiles.yml.bak.20260913183838`) · Docker container
`spot-postgres` and volume `spot_pgdata` · worktree `../spotify-wta` on branch `feat/probe`.

**Engineering decisions worth knowing** (Claude Code's call, listed for visibility):

- **dbt invocation.** The profile reads `SPOT_SESSION` and the Postgres credentials from the environment, and
  nothing sets those for a bare `uv run dbt`. The supported form is `make dbt-parse` / `scripts/dbt.sh <cmd>`,
  which reads `.session` and `~/.config/spot/.env`. There is deliberately no `SPOT_SESSION` default.
- **gitleaks** uses the upstream Go-source hook, which pre-commit provisions itself. No system install, and it
  works inside worktrees, where a Docker bind mount cannot see the shared `.git`.
- **Pre-commit hooks are shared by all worktrees.** Bootstrap keeps an existing working install rather than
  re-pointing it at the current worktree's venv, so removing a worktree doesn't break `main`'s hooks.
- **`spot probe` takes the `spot_refresh` advisory lock**, the same lock `spot refresh` will use.
- **`spot auth` forces Spotify's account chooser** (`show_dialog=true`). It also refuses to bind one Spotify
  account to two profile slugs, which catches "Marie logged in while the browser was still signed in as Marc".
- **The email hook** blocks personal-mail domains by default. Family domains can be added through
  `SPOT_BLOCKED_EMAIL_DOMAINS` in `~/.config/spot/.env`, so the list itself never enters the public repo.
- Session tokens are validated as `main` or `wt` + one lowercase letter.

---

## 6. Credential-dependent steps — completed by Marc

| Step | Result |
|---|---|
| Client ID and Secret placed in `~/.config/spot/.env` | ✅ (values never read into this report or the repo) |
| `make bootstrap` in `main` | ✅ re-run by Claude Code after the credentials were placed (see §9) |
| `uv run spot auth marc` | ✅ refresh token stored; the probes below authenticated with it |
| `uv run spot probe --profile marc --shape` in `main` | ✅ `probe-main-208f7c73`, ids 5–6 |
| Same command in `spotify-wta` | ✅ `probe-wta-8cdee8b3`, ids 7–8 |

(`spot` is installed into the project venv, so the commands are `uv run spot ...`, not bare `spot ...`.)

**GitHub:** `marc4data/spotify-lakehouse` does not exist yet. Nothing was pushed, so CI has not run on GitHub.
Creating the public repo is Marc's call.

---

## 7. `/me` and `recently-played` — observed vs documented

Source: the four saved response files (two runs, profile `marc`), read for **key paths and JSON types only**.
No values appear here except `product` and counts, which don't identify anyone. Both runs had identical shapes.

### 7.1 `GET /me`

| Field | Documented | Observed | Note |
|---|---|---|---|
| `id`, `uri`, `href`, `type`, `display_name` | ✅ | ✅ `str` | Identifiers; redacted in every display |
| `external_urls.spotify` | ✅ | ✅ `str` | |
| `country` | ✅ | ✅ `str` | |
| `product` | ✅ | ✅ `"premium"` | Owner account; R-016 is about *added* users and still needs one of them |
| `explicit_content{filter_enabled, filter_locked}` | ✅ | ✅ `bool`, `bool` | |
| `followers{href, total}` | ✅ | ✅ `href` is **null**, `total` is `int` | |
| `images` | ✅ | ✅ **empty list** in both runs | |
| `email` | ✅ | **Absent from saved files** | The scrub runs before save, so the files can't show whether Spotify sent it. The `user-read-email` scope isn't requested, so Spotify shouldn't return it (see F7) |
| `account_id` | ❌ **not documented** | ✅ `str` | Undocumented account identifier. **Fixed this round:** added to `redact.SENSITIVE_FIELDS` with a test (see F8) |

### 7.2 `GET /me/player/recently-played?limit=50`

| Field | Documented | Observed |
|---|---|---|
| `href`, `limit` | ✅ | ✅ `limit = 50` |
| `items[]` | ✅ | ✅ **50 items** in each run |
| `cursors{after, before}` | ✅ | ✅ both `str` |
| `next` | ✅ (nullable) | ✅ **non-null `str`** in both runs (see F10) |
| `total` | ✅ | ❌ **absent** |
| `items[].played_at` | ✅ | ✅ `str` (ISO timestamp) |
| `items[].context{type, href, uri, external_urls}` | ✅ nullable | ✅ `dict`; **null on 2 of 50** items in each run |
| `items[].track` | full track object | ✅ `dict`. `track.type` = `track` on all 100 items; no episodes seen (consistent with CLAUDE.md §4, though it doesn't prove episodes are excluded) |

**Track fields observed:** `id`, `uri`, `href`, `name`, `type`, `duration_ms` (int), `explicit`, `disc_number`,
`track_number`, `is_local`, `is_playable`, `external_urls.spotify`, **`external_ids.isrc`** (see F9),
`artists[]{id, uri, href, name, type, external_urls}`, and `album{id, uri, href, name, type, album_type,
release_date, release_date_precision, total_tracks, is_playable, images[]{url, height, width},
artists[], external_urls}`.

**Track fields not returned** that the historical full track object carried: `popularity`,
`available_markets` (on both track and album), `preview_url`. None is in any contract, so nothing breaks.

**Artist objects are simplified: no `genres`.** Genres need `GET /artists/{id}`, as `dim_artist` (SCD2) already
assumes. Expected, not a finding.

### 7.3 Implications for the model

| Contract column | Fed by | Status |
|---|---|---|
| `fct_play_event.ended_at_utc` (API) | `items[].played_at` | ✅ present |
| `dim_content.content_uri` / `content_type` / `duration_ms` | `track.uri`, `track.type`, `track.duration_ms` | ✅ present |
| `dim_content.parent_name` / `primary_creator_name` | `track.album.name` / `track.album.artists[0].name` | ✅ present |
| `dim_profile.spotify_user_id` | `/me.id` | ✅ present |
| `fct_play_event.ms_played` (API rows) | — | NULL by contract; confirmed there is no duration-played field |

### 7.4 Value-level observations (formats, enums, counts — no identifying values)

Re-read from the same four files. Both runs matched on every line below.

| Observation | Observed | Why it matters |
|---|---|---|
| `items[].played_at` format | All 50 match `YYYY-MM-DDTHH:MM:SS.sssZ` (millisecond precision, UTC `Z`) | Casts cleanly to `timestamptz`; §2 minute truncation applies to a ms-precision value |
| `items[]` order | Newest first (strictly descending `played_at`) | The poller's high-water mark is `items[0].played_at` |
| Repeat plays in one window | 50 items, **43 distinct `track.uri`** | Grain is the play, not the track. Never dedupe on URI alone |
| Overlap between runs | **50 of 50** `played_at` values identical across `main` (05:52Z) and `wta` (06:09Z) | Nothing was played in between, so every poll re-returns plays already seen. Loads must be idempotent on (`profile`, `played_at`, `uri`) |
| `items[].context.type` | `playlist` × 48, `context = null` × 2 | Only `playlist` seen. `album`, `artist`, and `collection` weren't exercised. Null context has to be allowed in staging |
| `track.album.album_type` | `album` 34 · `compilation` 8 · `single` 8 | All three documented enum values present |
| `track.album.release_date_precision` | `day` 42 · `year` 8 (`month` not seen) | `release_date` is a string that can't be cast to `date` directly. Parse by precision |
| Artists per track | 1 artist: 37 · 2: 10 · 3: 1 · 4: 2 | `primary_creator_name = artists[0]` drops credited collaborators on 13 of 50 |
| `track.album.images[]` | Exactly 3 per album, `height`/`width` both `int` | |
| `track.is_local` | `false` on all 50 | Local files not exercised |
| `track.available_markets`, `popularity`, `preview_url` | Key **absent** (not null, not empty) | Staging must use `->>` / `coalesce`, not assume the key exists |
| `cursors.after` / `cursors.before` | Both all-digit strings (epoch-ms shape) | |
| `next` | Non-null; its query string carries `before=<cursor>` | Adds detail to F10: `next` is a `before`-cursor URL, i.e. it *points* backwards in time. Whether following it returns older plays is still untested |
| `/me.images` | Empty list in both runs | |
| `/me.explicit_content` | Both flags `false` | |

---

## 8. Contract findings — not amended; Cowork to decide

| # | Where | Finding | Proposed amendment |
|---|---|---|---|
| F1 | Prompt §8 vs data-contracts §3 | The prompt says to write "the raw JSON" of `/me` to `data/raw/` and `raw.api_response`. §3 says "**email is never stored.** The extractor reads it from `/me` … and discards it." The two conflict. **Built to the contract:** `email` is removed before anything is persisted, and the probe reports only `email: present -> discarded`. | Confirm, and add a line to §1 that `raw` payloads are as received **minus contract-discarded fields**, so "raw" and "immutable" aren't read as "unscrubbed". |
| F2 | data-contracts §1 | §1 says **"One table per source feed"**, but the prompt names a single `raw.api_response` for both endpoints. The contract's five-column shape has no feed column, so the feed can be recovered only from the `source_file` path (`api/me/marc/…` vs `api/me_player_recently-played/marc/…`). Built as the prompt specified. Staging will have to parse a path to split feeds, which is fragile. | Either one raw table per endpoint (`raw.api_me`, `raw.api_recently_played`, …), or add `feed text not null` to the §1 raw shape. |
| F3 | data-contracts §1 vs §3 | `raw.*.profile_key` is `text` (holds the slug, `marc`), while `dim_profile.profile_key` is an int surrogate. One name, two meanings, and the staging join will be confusing. | Rename the raw column `profile_slug`. |
| F4 | Prompt acceptance #1 | "`make bootstrap` **succeeds** … with `~/.config/spot/` absent, **printing the missing-credential message**" contradicts itself: a missing credential can't be both a success and a reported failure. Built as: every step that doesn't need Spotify completes, then the script exits non-zero naming `credentials-missing`. | Reword to "completes all non-credential steps and fails naming the missing keys." |
| F5 | worktree-protocol §2 | "target schema from `SPOT_SESSION` env" doesn't say who sets the variable. Solved with `scripts/dbt.sh`; bare `uv run dbt` requires it exported. | Name `make dbt-*` / `scripts/dbt.sh` as the supported entry point. |
| F6 | `docs/spotify-data-access-guide.md` | "Last verified … **September 14, 2026**" is one day in the future relative to this round (2026-09-13). | **Withdrawn.** The round started 2026-09-13 18:17 PDT, which was already 2026-09-14 in UTC. The date is right in UTC. |
| F7 | data-contracts §3 (`dim_profile`) | "The extractor reads [email] from `/me` to match accounts and discards it." The scope set in the access guide (and in `auth.SCOPES`) does **not** include `user-read-email`, so the extractor never receives an email to match on. Accounts are matched on `/me.id` instead: `spot auth` refuses to bind one Spotify `id` to two profile slugs. | Reword §3: "Accounts are matched on `spotify_user_id`. `user-read-email` is never requested, so email never reaches the extractor." That makes "email is never stored" structural, not procedural. Keep the scrub as a backstop. |
| F8 | data-contracts §3, CLAUDE.md §5 | `/me` returns **`account_id`**, which isn't in Spotify's documented user object. It's an account identifier (CLAUDE.md §5: never in the git tree). It lives in `raw.api_response` (local only) and is now redacted in displays. No contract mentions it. | Decide whether staging drops `account_id`, as it drops `ip_addr`/`user_agent` (§2), or carries it on `dim_profile`. Recommend dropping it: nothing in the model needs it. |
| F9 | data-contracts §3 / §5 | Recently-played track objects carry **`external_ids.isrc`**, a recording-level identifier that stays the same when a track is re-issued under a new URI. That's the problem behind §3's "unresolvable content" caution and §5's single-vs-album miss. The export doesn't carry ISRC, so it's available only for API-resolved content. | Consider `isrc` on `dim_track_detail`, and ISRC as the first tier of the loose match mode in §5 (fall back to `content_match_key`). Not required for phase 1. |
| F10 | CLAUDE.md §4 | "`recently-played` … **cannot page backwards into the past.**" Both runs returned a **non-null `next`** URL and a `cursors.before` value. **Not tested:** following `next` would be an extra API call outside this round's scope. If it returns plays older than the first 50, the "no history" claim is too strong. If it returns an empty list, the claim holds. | One-call check (following `next` once) before R-017 designs the poller. Either result belongs in CLAUDE.md §4 as observed, not assumed. |

---

## 9. Definition-of-done check (CLAUDE.md §7)

| Item | State |
|---|---|
| `make bootstrap` (after credentials) | ✅ `Bootstrap complete for session "main"`; `.env` and `tokens.json` both `-rw-------`; token store holds profile `marc` only |
| `uv run pytest` | ✅ 64 passed (DB integration tests ran against live `spot-postgres`) |
| `uv run dbt build` | n/a — no models this round by instruction; `dbt parse` ✅ |
| `ruff check` / `ruff format --check` | ✅ |
| Pre-commit on staged files, gitleaks included | ✅ all hooks passed on `3a499cf` |
| A guard proven to fail | ✅ §2 (five guards, including the `account_id` redaction added after the live probe) |
| Build report | this file |

---

## Appendix A — Observed key paths (types only)

Output of `spotify_lakehouse.shape.describe_shape` over the saved files. `[]` marks a list element and
`a|b` means both types were seen at that path. The two runs produced identical output. `/me` paths are
shown **after** the scrub (`email` removed before storage).

### A.1 `GET /me`

```
$                                   dict
$.account_id                        str     (undocumented; redacted in displays — F8)
$.country                           str
$.display_name                      str
$.explicit_content                  dict
$.explicit_content.filter_enabled   bool
$.explicit_content.filter_locked    bool
$.external_urls                     dict
$.external_urls.spotify             str
$.followers                         dict
$.followers.href                    null
$.followers.total                   int
$.href                              str
$.id                                str
$.images                            list    (empty)
$.product                           str
$.type                              str
$.uri                               str
```

### A.2 `GET /me/player/recently-played?limit=50`

```
$                                                     dict
$.cursors                                             dict
$.cursors.after                                       str
$.cursors.before                                      str
$.href                                                str
$.items                                               list
$.items[]                                             dict
$.items[].context                                     dict|null
$.items[].context.external_urls                       dict
$.items[].context.external_urls.spotify               str
$.items[].context.href                                str
$.items[].context.type                                str
$.items[].context.uri                                 str
$.items[].played_at                                   str
$.items[].track                                       dict
$.items[].track.album                                 dict
$.items[].track.album.album_type                      str
$.items[].track.album.artists                         list
$.items[].track.album.artists[]                       dict
$.items[].track.album.artists[].external_urls         dict
$.items[].track.album.artists[].external_urls.spotify str
$.items[].track.album.artists[].href                  str
$.items[].track.album.artists[].id                    str
$.items[].track.album.artists[].name                  str
$.items[].track.album.artists[].type                  str
$.items[].track.album.artists[].uri                   str
$.items[].track.album.external_urls                   dict
$.items[].track.album.external_urls.spotify           str
$.items[].track.album.href                            str
$.items[].track.album.id                              str
$.items[].track.album.images                          list
$.items[].track.album.images[]                        dict
$.items[].track.album.images[].height                 int
$.items[].track.album.images[].url                    str
$.items[].track.album.images[].width                  int
$.items[].track.album.is_playable                     bool
$.items[].track.album.name                            str
$.items[].track.album.release_date                    str
$.items[].track.album.release_date_precision          str
$.items[].track.album.total_tracks                    int
$.items[].track.album.type                            str
$.items[].track.album.uri                             str
$.items[].track.artists                               list
$.items[].track.artists[]                             dict
$.items[].track.artists[].external_urls               dict
$.items[].track.artists[].external_urls.spotify       str
$.items[].track.artists[].href                        str
$.items[].track.artists[].id                          str
$.items[].track.artists[].name                        str
$.items[].track.artists[].type                        str
$.items[].track.artists[].uri                         str
$.items[].track.disc_number                           int
$.items[].track.duration_ms                           int
$.items[].track.explicit                              bool
$.items[].track.external_ids                          dict
$.items[].track.external_ids.isrc                     str
$.items[].track.external_urls                         dict
$.items[].track.external_urls.spotify                 str
$.items[].track.href                                  str
$.items[].track.id                                    str
$.items[].track.is_local                              bool
$.items[].track.is_playable                           bool
$.items[].track.name                                  str
$.items[].track.track_number                          int
$.items[].track.type                                  str
$.items[].track.uri                                   str
$.limit                                               int
$.next                                                str
```

Not present at any path: `total`, `track.popularity`, `track.preview_url`, `track.available_markets`,
`track.album.available_markets`, `artists[].genres`, `email`.
