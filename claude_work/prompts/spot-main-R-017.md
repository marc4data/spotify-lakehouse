# spot-main-R-017 — The poller: catch plays before the window overflows

**Session: `main`. Read `CLAUDE.md` §4–§6 and `docs/data-contracts.md` §1–§2 before starting.**

## Why this is the round that compounds

R-018 settled it: **`recently-played` does not page backwards.** A play that falls out of the 50-item
window before it is captured is gone until the GDPR export arrives — and the exports have not been
requested yet. **Every hour this does not run is history that has to come from a file that does not
exist.** Nothing else queued has that property.

Rate, from R-018: the window moved **14 plays in ~19 hours** for `marc`. Thirty minutes is generously
safe at that rate and untested on a heavy listening day; the design must degrade honestly rather than
silently, which is what §4 below is about.

## Cowork's decisions, so you do not re-ask

| Question | Decision |
|---|---|
| Cadence | **Every 30 minutes**, Marc's choice from the first interview. Keep it. |
| Backfill | **None.** There is no mechanism. Do not add `--follow-next` to the poller — R-018 C1 deliberately made it opt-in so the poller never makes a call it did not ask for. |
| Profiles | Every profile in `spot_meta.profile_registry` with `has_api_access`. Today that is `marc` alone; the loop is written for N. |
| Install | Once, from `main`. A `make launchd-install` / `make launchd-uninstall` pair, plist written to `~/Library/LaunchAgents/`. It is outside the repo, so the repo ships the template and the Makefile target. |
| Lock | The existing `spot_refresh` advisory lock, unchanged. A poll that cannot take the lock **exits 0 quietly** — another poll or a probe is already talking to Spotify, and a launchd agent that errors every half hour trains you to ignore it. |

## Build

**1. `spot refresh`** — for each API-enabled profile: one `GET /me/player/recently-played?limit=50`,
persisted as `feed=recently_played` exactly as the probe does. No `/me` call per poll; the profile
registry already has what it needs.

**2. Overflow detection, and it is the point of the round.** Each poll knows the newest `played_at` it
saw last time. If page 1's **oldest** item is newer than that high-water mark, the window overflowed
between polls and plays were lost. **Record it** — a row in `spot_meta`, with the gap's start and end
and how many items the poll returned — and print it. A silent overflow is the one failure this whole
round exists to prevent, and the only evidence it ever happened is the gap between two timestamps.

**3. The launchd agent** — `StartInterval` 1800, stdout and stderr to a rotating log outside the repo
(`~/.config/spot/logs/`), `RunAtLoad` true. It calls the repo's `uv run spot refresh` by absolute path.

**4. `spot refresh --status`** — last successful poll per profile, high-water mark, count of recorded
overflow gaps. Marc should be able to answer "is it running and has it missed anything" in one command.

## Required tests

1. **Overflow detection goes red on a synthetic gap.** A fixture where the previous high-water mark is
   older than page 1's oldest item must record a gap; one where they overlap must not. This is the guard
   to prove failing.
2. **Lock contention exits 0.** A second `spot refresh` while the lock is held exits 0 and records no poll.
3. **Idempotence.** Two consecutive polls over the same unchanged window add raw rows but produce no new
   `fct_play_event` rows — the dedupe already proves this shape; assert it end to end.

## Also in this round

- **C4 from R-018:** a `MockTransport` loop test for `_follow_next`, now that the poller shares the client.
- **C2 from R-004:** the pytest DB fixture must stop applying migrations to the shared database. It asserts
  migrations are current and **skips** otherwise, leaving `make migrate` the only writer. This is R-027;
  close it here.
- **Drop the leftovers:** `drop schema stg_wta cascade; drop schema mart_wta cascade;` — empty since R-002's
  bootstrap, nothing reads them, the worktree is gone.

## Definition of done

`CLAUDE.md` §7 in full. **The report must state how the agent was verified as actually running** — a load,
a real firing, and the log line it produced. An installed plist nobody has seen fire is not a running
poller, by the same rule that a guard nobody has watched fail is not a control.

## Constraints

- **Do not amend a contract.** Report the conflict instead.
- Nothing under `data/` gets committed. The plist and logs live outside the repo.
- Rate limits: 30-second rolling window, dev-mode ceiling unpublished. One call per profile per poll is
  well inside it; honour `Retry-After` anyway.

## Report, then hand yourself back

`claude_work/reports/spot-main-R-017-report.md`, ending with the return handoff cell and the clock line.
