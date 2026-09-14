# spot-main-R-020 — Build report: CI scans history, not a range

**Session:** `main` · **Date:** 2026-09-13 (PDT) · **Commits:** `4b4c312` (history scan + rules) and this
report's commit (script scope fix + report)

**Status for Cowork:** the history-scan job is built, pinned, and green on `main`. **The public scratch-branch
acceptance test was not run.** It can't be done without putting a fake secret in the public repo's history, which
is the stop condition in the prompt's note (§2). The guard was instead proven failing with the exact CI script
on Linux x64, outside the public repo (§4). Cowork needs to pick the route for an on-GitHub red run.

Separately, and more important than the range bug: **gitleaks' default rules alone barely detect a Spotify
credential.** A full-history scan with default rules would have been green over a committed Client ID. Fixed
with a project `.gitleaks.toml` (§3). This also corrects an R-002 claim (§7, G1).

---

## 1. Scorecard

| Prompt requirement | Result | Evidence |
|---|---|---|
| Replace the `gitleaks` job with an installed binary at a pinned version | ✅ v8.30.0, sha256-verified download, in `scripts/ci/gitleaks_history.sh` | §5 |
| Pin explicitly, not `latest` | ✅ version and linux_x64 sha256 hardcoded | §5 |
| `actions/checkout` with `fetch-depth: 0` | ✅ plus the script refuses a shallow clone (exit 2) | §5 |
| Scan all commits; verify the flag from `--help` on the pinned version | ✅ with a caveat: the help text doesn't document the default scope, so the scope was verified from the binary's own debug output | §6 |
| Fail the job on any finding | ✅ `--exit-code 1`; exit 1 shown in §4 | §4 |
| No matched values in the log | ✅ `--redact`, no `--verbose`. The public job log shows only checksum, version, commit count, finding count | §4.3 |
| Keep the `checks` job's tracked-file scan | ✅ unchanged; green on GitHub | §4.3 |
| **Prove the guard fires on a pushed scratch branch** | ⛔ **Not run: stop condition.** Proven off-GitHub with the identical script instead | §2, §4 |
| Both jobs green on `main` | ✅ run `34815163444` on `4b4c312` | §4.3 |

---

## 2. Why the public scratch-branch test was stopped

The test requires pushing a commit containing a fake secret to `marc4data/spotify-lakehouse`, which is public.
Deleting the branch afterwards doesn't remove it: GitHub keeps the orphaned commit reachable by SHA until garbage
collection, and it can be fetched, cached, or indexed in the meantime. That is exactly the case the prompt's note
describes, so per the note it was not done.

**Alternatives for Cowork to choose from:**

| Option | What it proves | What it costs |
|---|---|---|
| **A. Accept the off-GitHub proof in §4** (recommended as sufficient) | The byte-identical script, same pinned binary, same config, on Linux x86_64, fails on a secret that exists only in history; the same script is green on GitHub over `main` | Nothing further. It doesn't prove GitHub Actions' runner wiring on a *red* result, only on green |
| **B. Private scratch repo** | A real red Actions run of the same workflow, with a URL | Marc creates a **private** repo on his account, Claude Code pushes the workflow plus a synthetic history into it, records the red URL, and Marc deletes the repo. Uses a few Actions minutes |
| **C. Canary rule on a public scratch branch** | A red run in *this* repo | Add a `spot-ci-canary` rule matching an obviously non-secret marker (e.g. `SPOT-CI-CANARY-<date>`), push a branch that adds then removes it. The public history then contains a marker word, not a credential-shaped string. Leaves a permanent test-only rule in `.gitleaks.toml` |

---

## 3. The larger finding: default rules miss the credential this project exists to protect

The first synthetic test did **not** go red. A fake `SPOTIFY_CLIENT_SECRET=<32 hex>` was committed, then removed.
The pinned binary scanned full history and reported `no leaks found`. The value's Shannon entropy was **3.480**,
and gitleaks' `generic-api-key` rule has a **3.5** entropy floor. Random hex tops out at 4.0 bits/char, so a
meaningful share of real Spotify values fall under it.

**Measured detection rate, gitleaks v8.30.0, 40 random 32-hex values (values never printed):**

| Shape | Default rules | Default + `.gitleaks.toml` |
|---|---|---|
| `SPOTIFY_CLIENT_SECRET=<hex32>` | 7/10 (misses: entropy 3.31–3.34) | **10/10** |
| `SPOTIFY_CLIENT_ID=<hex32>` | **0/10** (no "secret"-type keyword) | **10/10** |
| `client_secret: "<hex32>"` | 8/10 | **10/10** |
| bare `<hex32>` | **0/10** | **10/10** |
| line carrying `spot: allow-secret-shape` | — | not flagged (escape hatch works) |

**Fix:** `.gitleaks.toml` at the repo root, `[extend] useDefault = true`, plus rule `spot-hex32`: a standalone
32-hex string. That's the same shape `scripts/hooks/check_secrets.py` rejects, with the same pragma allowlist and
`uv.lock` excluded. gitleaks reads a root `.gitleaks.toml` automatically, so the **pre-commit gitleaks hook now
applies the same rules as CI.** The CI script passes it explicitly with `--config`, and fails (exit 2) if the file
is missing rather than silently falling back to defaults.

**False-positive check:**

- Committed history (4 commits): clean.
- Working tree: 7 hits, **all in gitignored files that can't be committed**:
  - `CACHEDIR.TAG` ×3: the standard cache-directory signature line is 32 hex.
  - `dbt/target/manifest.json`.
  - 3 minified JupyterLab bundles in `.venv` (default generic rule).
- No tracked or committable file matches, including Cowork's uncommitted doc edits. Pre-commit passed on
  `4b4c312` with the new config active.

---

## 4. Guard proven to fire (off-GitHub)

### 4.1 Why a range scan and a tree scan both miss it

Synthetic repo: `c1` clean root → `c2` adds a fake secret → `c3` deletes it. The tree at HEAD is `README.md` only.

| Scan | Models | Result |
|---|---|---|
| `check_secrets.py` over `git ls-files` | the `checks` job | exit 0: tree is clean |
| `gitleaks git --log-opts=HEAD~1..HEAD` | a pushed-range scan of `c3` | exit 0: `0 commits scanned` |
| `gitleaks git` full history, **default rules** | a naive history scan | exit 0: missed (entropy 3.480, §3) |
| `scripts/ci/gitleaks_history.sh` (history + `spot-hex32`) | **the new CI job** | **exit 1, `leaks found: 1`** |

### 4.2 Exact CI script, Linux x86_64, pinned binary

`docker run --platform linux/amd64 buildpack-deps:24.04-scm`, each repo mounted read-only, running
`scripts/ci/gitleaks_history.sh <repo>`. This is the same file, binary, checksum, and config CI uses.

```
===== scripts/ci/gitleaks_history.sh /synthetic_secret        (SPOTIFY_CLIENT_SECRET added, then removed)
tree at HEAD: README.md
gitleaks_8.30.0_linux_x64.tar.gz: OK
8.30.0
commits reachable from any ref: 3
INF 2 commits scanned.
WRN leaks found: 1
exit=1

===== scripts/ci/gitleaks_history.sh /synthetic_id            (SPOTIFY_CLIENT_ID added, then removed)
tree at HEAD: README.md
gitleaks_8.30.0_linux_x64.tar.gz: OK
8.30.0
commits reachable from any ref: 3
INF 2 commits scanned.
WRN leaks found: 1
exit=1

===== scripts/ci/gitleaks_history.sh /repo                     (spotify-lakehouse)
gitleaks_8.30.0_linux_x64.tar.gz: OK
8.30.0
commits reachable from any ref: 4
INF 4 commits scanned.
INF no leaks found
exit=0
```

"2 commits scanned" out of 3 is expected: gitleaks counts commits with additions, and `c3` is a pure deletion.

### 4.3 On GitHub, `main`

| | |
|---|---|
| Red run URL | **None: stopped, see §2** |
| Green run | https://github.com/marc4data/spotify-lakehouse/actions/runs/34815163444 (`4b4c312`) |
| `checks` | success |
| `gitleaks (full history)` | success: checksum `OK`, `8.30.0`, `commits reachable from any ref: 4`, `4 commits scanned`, `scanned ~368334 bytes`, `no leaks found` |
| Previous run, for contrast | https://github.com/marc4data/spotify-lakehouse/actions/runs/34814253756: `gitleaks-action` scanned `~0 bytes`, failed with "unknown revision" on the root commit's parent |

That green run used `--log-opts="--all"`. The scope fix in §6 lands in this report's commit, which triggers
another run on `main`.

---

## 5. Files changed

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | `gitleaks` job (display name `gitleaks (full history)`): checkout `fetch-depth: 0`, then `scripts/ci/gitleaks_history.sh`. `gitleaks/gitleaks-action@v2` removed. `checks` job untouched |
| `scripts/ci/gitleaks_history.sh` | New. Downloads `gitleaks_8.30.0_linux_x64.tar.gz`, checks sha256 `79a3ab57…a66e` with `sha256sum --check --strict`, refuses shallow clones and non-Linux-x64, scans with `--config .gitleaks.toml --log-opts="--full-history --all --diff-filter=tuxdb" --redact --no-banner --no-color --exit-code 1` |
| `.gitleaks.toml` | New. Default rules plus `spot-hex32`, pragma and `uv.lock` allowlists |

**Version choice:** v8.30.0, not the latest v8.30.1, because `.pre-commit-config.yaml` pins gitleaks at
`v8.30.0`. The hook and CI stay on one version. Bump both together in a later round.

---

## 6. What the pinned binary's help says about full history

**`gitleaks --help` (v8.30.0):** the subcommands are `dir`, `git`, `stdin`. The old `detect` still runs but is
no longer listed.

**`gitleaks git --help` (v8.30.0), verbatim:**

```
scan git repositories for secrets

Usage:
  gitleaks git [flags] [repo]

Flags:
  -h, --help              help for git
      --log-opts string   git log options
      --platform string   the target platform used to generate links (github, gitlab)
      --pre-commit        scan using git diff
      --staged            scan staged commits (good for pre-commit)
```

**What the help does and doesn't say:**

- There is **no flag named for "full history" or "all commits."**
- Scope is controlled only by `--log-opts`, described just as `git log options`.
- The help **doesn't state what `git` scans when `--log-opts` is omitted.**

So the scope was verified from the binary's own `--log-level=debug` output, not from memory:

| Invocation | Command gitleaks actually executed |
|---|---|
| no `--log-opts` | `git log -p -U0 --full-history --all --diff-filter=tuxdb` |
| `--log-opts="--all"` | `git log -p -U0 --all` |
| `--log-opts="--full-history --all --diff-filter=tuxdb"` (**shipped**) | `git log -p -U0 --full-history --all --diff-filter=tuxdb` |

**`--log-opts` replaces the default git arguments; it doesn't add to them.** Commit `4b4c312` shipped
`--log-opts="--all"`, which silently dropped `--full-history` and the diff filter. Detection wasn't affected (same
finding, same commit count on the synthetic repos), but the flag didn't do what was intended. This report's commit
sets it to the full default string. That keeps gitleaks' own behavior and pins the all-refs scope in case a later
version narrows its default.

**Coverage limit worth knowing:** `--all` covers every ref *in the CI clone*. `actions/checkout` with
`fetch-depth: 0` fetches all branches and tags. Refs GitHub holds but doesn't fetch (e.g. `refs/pull/*`, orphaned
commits) are outside any clone-based scan.

---

## 7. Findings for Cowork

| # | Where | Finding | Proposal |
|---|---|---|---|
| G1 | R-002 report §2.1; register R-011 | R-002 recorded the fake secret as "rejected by **two** hooks, `spot-no-spotify-secrets` and `gitleaks`." True for that value, but gitleaks' catch was luck: entropy 3.608, just over the 3.5 floor, and a `SECRET` keyword present. A `SPOTIFY_CLIENT_ID` would have passed gitleaks 10 times out of 10 (§3). Until `4b4c312`, the custom hook was the **only** reliable local guard for a Client ID. | Note on R-011 that the two-layer claim became true at `4b4c312`. |
| G2 | CLAUDE.md §5, enforcement item 2 | Says "`gitleaks` as a pre-commit hook, plus a custom hook." It doesn't say gitleaks runs with project rules, or that CI scans full history. | Add: "gitleaks runs with the repo's `.gitleaks.toml` (default rules + `spot-hex32`) in pre-commit and in CI, where it scans full history." |
| G3 | This round's acceptance | On-GitHub red run not produced (§2). | Choose A, B or C. |
| G4 | CI | `actions/checkout@v4` and `astral-sh/setup-uv@v6` trigger GitHub's Node 20 deprecation notice. Warning only, no failure. | Small future round: bump action majors. |
| G5 | Tooling | v8.30.1 exists; hook and CI both on v8.30.0. | Bump together, with a re-measure of §3, in a later round. |

---

## 8. Definition of done (CLAUDE.md §7)

| Item | State |
|---|---|
| `uv run pytest` | ✅ CI `checks` job green on `4b4c312`. No Python source changed this round |
| `uv run dbt build` | n/a: no models |
| `ruff check` / `ruff format --check` | ✅ CI `checks` job |
| Pre-commit on staged files, gitleaks included | ✅ passed on `4b4c312` with the new `.gitleaks.toml` active |
| A guard proven to fail | ✅ off-GitHub, exact CI script, exit 1 (§4.2). ⛔ on-GitHub red run pending Cowork's choice (§2) |
| Build report | this file |
