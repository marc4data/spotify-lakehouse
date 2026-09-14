# spot-main-R-032 — Move the PM surface out of the code repo

**Session: `main`. Run this only after R-008 has landed and been reviewed.**

## Why

Marc set the boundary on 2026-09-14: Cowork writes prompts and the register, never source. Today that is
discipline. This round makes it **mechanism** — Cowork will have no path to the code repo at all, because
the folder will not be connected to it.

It also removes two failures by construction:

- **R-022 / R-031.** `claude_work/` lives inside the repo, so a worktree sees only committed state and an
  uncommitted prompt is invisible. Outside the repo it is just a folder every session reads by absolute
  path. The "written but not committed" state disappears.
- **R-030.** Ruff hooks reformat prose in `claude_work/` and bounce the commit. After the move, ruff never
  sees a prompt. No exclusion needed.

## Target layout

```
~/projects/ai_orchestrator_claude/spotify/        code repo — Cowork will NOT be connected to this
~/projects/ai_orchestrator_claude/spotify-wt*/    worktrees — same
~/projects/ai_orchestrator_claude/spotify-pm/     PM folder — its own git repo, Cowork connected here
    prompts/
    reports/
    register.md
```

**`CLAUDE.md` and `docs/` stay in the code repo.** Claude Code loads `CLAUDE.md` from the project root
automatically; moving it would break that for every session. The contracts belong with the code they bind
and with the public repo anyone reads. Cowork amends both by specifying the change in a prompt — that is
the cost of this design and it is accepted.

## Do

1. **Create `../spotify-pm/` as its own git repo.** `git init`, a `README.md` saying what it is and that
   Cowork owns `prompts/` and `register.md` while Code owns `reports/`, and a `.gitignore` for `.DS_Store`.
2. **Move, preserving readability of history.** Copy `claude_work/prompts/`, `claude_work/reports/` and
   `claude_work/spot_request_register.md` → `../spotify-pm/` (register renamed `register.md`). Then
   `git rm -r claude_work/` in the code repo. The prompt history stays readable in `spotify`'s log; do not
   attempt a history rewrite for it.
3. **Update `CLAUDE.md` §2's round contract** to the new absolute paths: register
   `~/projects/ai_orchestrator_claude/spotify-pm/register.md`, prompts and reports beside it. Say plainly
   that the PM folder is **not** a git worktree of this repo and that an uncommitted prompt there is
   readable — the state table's "written but not committed" case no longer applies to this project.
4. **Delete three files a Cowork session created and cannot remove:** `.git/_cowork_probe`,
   `.git/_probe_b`, `claude_work/_delete_probe`. Creates succeed under the mount; unlinks return
   *Operation not permitted*, so they are Code's to clear.
5. **Check every reference.** Grep the repo for `claude_work` — `CLAUDE.md`, `docs/*.md`,
   `.claude/commands/project-round-close.md`, the Makefile, CI. Update each to the new path or to a
   `SPOT_PM_DIR` env var with a documented default. **A stale path here is the whole round wasted.**
6. **Prove a worktree can read it.** Create a throwaway worktree, confirm it reads the PM folder by
   absolute path with nothing committed there, then remove the worktree. This is the R-022 fix; show it
   working rather than asserting it.

## Definition of done

`CLAUDE.md` §7 in full, plus:

- `grep -rn claude_work` in the code repo returns nothing but historical mentions in reports.
- `make dbt-build`, `pytest`, `ruff`, pre-commit all still pass.
- The worktree read in step 6, shown.
- The three probe files are gone.

## Report

`~/projects/ai_orchestrator_claude/spotify-pm/reports/spot-main-R-032-report.md` — the new location.
End with the return handoff cell and the clock line.

**Then tell Marc, in one line, that he can disconnect `spotify` and connect `spotify-pm` in the Claude
desktop app** — that is the step only he can take, and until he takes it the boundary is still discipline.
