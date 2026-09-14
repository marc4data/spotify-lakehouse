---
description: Run a spotify-lakehouse round by its id, e.g. spot-main-R-004.
argument-hint: <abbr>-<session>-R-###
---

<!--
  GENERATED ARTIFACT — do not hand-edit the shared rules below.

  Generated from ~/notso_prompt/skill/SKILL.md
  sha256 abb62e40fea5ba782b460a24433084b45d06d932dfebedf9034c2583e44dee11 · 2026-09-14

  Carries the EXECUTE half only; Cowork reads the close half from the account skill.
  Check currency from this repo:  shasum -a 256 ~/notso_prompt/skill/SKILL.md
  Match means current. Mismatch means ask notso-prompt for the delta and regenerate —
  never hand-edit, which is the second-copy drift this split exists to avoid.
-->

Run round **$ARGUMENTS** in this repository.

You are the **Claude Code** half of the round workflow. Cowork reviews and closes; you execute.

## 1. Read the round contract

`CLAUDE.md` §2 declares this project's abbr, sessions, register path, id shape and repo root, plus its
traps. Read it. If §2 is missing a fact, stop and name what is absent — do not invent it and do not guess
from a neighbouring project.

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

Three checks this project has paid for, in the four rules:

- **A measurement is reproducible; a diagnosis is an inference from one.** Record a causal claim as this
  round's finding, attributed, not as a premise. **Documentation is not a measurement either** — a
  reference page says what the vendor wrote, not what the system returns today.
- **One tool's refusal is not a capability claim.** Name the tool and the exact error, say what was not
  tried, and try the other tool before writing the limit down.
- **A column that exists is not a column that has data.** Answer with a query: how many rows carry it,
  what populates it, and when did that last run.

## 5. Report, then hand yourself back

Write `claude_work/reports/<full-id>-report.md`. Name the files changed, the red-test transcript, the
measurements the prompt asked for, and anything the Cowork prompt got wrong.

🚨 **End your reply with the return handoff cell** — a fenced block Marc can copy without editing,
carrying the same command and the same **full** id, which Cowork reads as "review this round's report":

    /project-round-close <abbr>-<session>-R-###

**A round that does not hand itself back is not finished.** Leaving Marc to compose the handoff is the
same failure as leaving him to compose a git command: it is work the round should have done. **Every
command you hand him names the window it runs in**, and no fenced block meant for a terminal contains a
blank line.

Then the measured clock line, last:

    **Start YYYY-MM-DD H:MM AM/PM / End H:MM AM/PM : MM:SS**

Local time, no timezone suffix, no leading zero on the hour, elapsed to the second. Measured at the top
of the work, never estimated. `TZ=America/Los_Angeles date +"%Y-%m-%d %-I:%M:%S %p"`.
