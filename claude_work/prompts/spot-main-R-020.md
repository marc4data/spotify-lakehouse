# spot-main-R-020 — CI scans history, not a range

**Session: `main`. Small round.**

## Why

Two facts, and the second is the one that matters:

1. The `gitleaks` job failed on the first push. The reported cause — the action computing a commit
   range against a first commit that has no parent — is plausible and widely reported, but it is an
   inference from the log, not something the action's documentation confirms. **This round does not
   need to settle it**, because the fix is the same either way: a job that scanned ~0 bytes gave zero
   coverage regardless of why.

2. **Neither CI job has ever scanned the repository's history.** `gitleaks-action` scans a pushed
   commit range, and the `checks` job's `git ls-files | check_secrets.py` scans the current tree. A
   secret committed and then removed in a later commit would pass both. `CLAUDE.md` §5 implies a
   stronger guarantee than CI actually provides. That is the real gap, and it is why this is worth a
   round rather than a shrug.

## Do

Replace the `gitleaks` job with a step that installs the `gitleaks` binary at a pinned version and
scans **full history** on every run:

- Pin the version explicitly. Do not track `latest`.
- `actions/checkout` with `fetch-depth: 0`.
- Run gitleaks in whatever mode scans all commits, not a diff or a range. Verify from `gitleaks --help`
  on the pinned version which flag does that on that version — do not assume a flag name from memory or
  from a blog post.
- Fail the job on any finding.
- Do not print matched values into the log. A public repo's Action logs are public.

Keep the `checks` job's tracked-file scan. It is a different guarantee (current tree, custom rules) and
both are wanted.

## Acceptance

- **Prove the guard fires.** On a scratch branch, commit a fake secret, then commit its removal, then
  push the branch. The history scan must fail the job even though the tree is clean. Report the job
  name, the run URL, and the finding count. **Delete the scratch branch and its remote afterwards.**
  This is the whole point of the round: a CI secret scan nobody has watched fail is not a control.
- The `checks` job still passes.
- On `main`, both jobs green.

## Report

`claude_work/reports/spot-main-R-020-report.md`. Include the red-run URL from the acceptance test and
what the pinned gitleaks version's help output actually says about the full-history flag you used.

## Note for Cowork, not for this round

If the acceptance test above is impossible without leaving a fake secret in a public repo's history
even briefly, **stop and say so** rather than doing it. A scratch branch that is force-deleted still
leaves the objects reachable on GitHub for a while. Propose an alternative — a local `gitleaks` run over
a synthetic repo, with the CI job proven by a different route — and let Cowork decide.
