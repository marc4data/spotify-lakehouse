---
description: Run a spotify-lakehouse round by its id (spot-main-R-004, or short R-004).
argument-hint: R-###
---

Run round **$ARGUMENTS** in this repository.

You are the **Claude Code** half of the round workflow. Cowork reviews and closes; you execute. This
file exists because the account-level `project-round-close` skill resolves in Cowork but not in Claude
Code — it carries only the execute half, so there is no second copy of the close format to drift.

## 1. Read the round contract

`CLAUDE.md` §2 declares this project's abbr, sessions, register path, id shape and repo root, plus its
traps. Read it. If §2 is missing, stop and say which facts are absent — do not invent them.

## 2. Resolve the id

A short `R-###` resolves by globbing the session segment; a full id names its session outright.
**The session segment names the session that EXECUTES, not the one that wrote the prompt.**

Four traps, every one already paid for on this project:

1. **A prompt is not available until it is COMMITTED.** Check with `git ls-files`, not `ls`. A worktree
   sees only committed state, so an untracked prompt in `main`'s working tree is invisible everywhere
   else and the handoff fails silently, in a way that looks like your fault (R-022).
2. **The session in the id must match `.session`.** If they differ, stop and say which session owns the
   round. Never run another session's round (R-019).
3. **More than one live match: stop and list them.** Never pick one.
4. **Round ids are not unique across this machine.** Other repos here also use "round", `claude_work/`,
   prompts and a register. Stay inside this repo; never answer about an `A###`/`B###` id (R-021).

## 3. The states, and they must read differently

| state | say |
|---|---|
| prompt committed, no report | execute it |
| prompt written but untracked | "Its prompt is written but not committed, so no worktree can open it. Commit it, then hand off." **Not** "not found" |
| report already exists | say so, and ask whether to re-run rather than silently redoing it |
| neither | "No such id in `claude_work/`." Then list the live ids so a typo is obvious |

## 4. Execute

Read the whole prompt and follow it.

**Do not amend a contract.** `docs/data-contracts.md` is binding. A contract that cannot be built as
written is a **finding to report**, not an obstacle to route around — that has happened once on this
project and the finding was worth more than the workaround would have been.

Meet `CLAUDE.md` §7 in full, including **staging a break and naming the test that went red.** "All tests
pass" is not evidence a guard works; a guard nobody has watched fail is not a control.

## 5. Report

Write `claude_work/reports/<full-id>-report.md`. Name the files changed, the red-test transcript, the
measurements the prompt asked for, and anything the Cowork prompt got wrong.

🚨 **End your reply with the return handoff cell, in a fenced block Marc can copy without editing** — the
same command, with the same **full** id, which Cowork will read as "review this round's report":

    /project-round-close <abbr>-<session>-R-###

**A round that does not hand itself back is not finished.** Do not make Marc compose the handoff.

Then the measured clock line, last:

    **Start YYYY-MM-DD H:MM AM/PM / End H:MM AM/PM : MM:SS**

Local time, no timezone suffix, no leading zero on the hour, elapsed to the second. Measured at the top
of the work, never estimated. `TZ=America/Los_Angeles date +"%Y-%m-%d %-I:%M:%S %p"`.
