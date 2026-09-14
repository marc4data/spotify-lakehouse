# spot-main-R-018 — Does `recently-played` page backwards?

**Session: `main`. Small round. One API call is the whole experiment.**

## Why

`CLAUDE.md` §4 asserted that `GET /me/player/recently-played` "cannot page backwards into the past."
The R-002 probe contradicted the confident version of that claim: both runs returned a **non-null
`next`** whose query string carries `before=<cursor>`, and Spotify's reference documents no temporal
limit on the `before` cursor.

This gates `spot-main-R-017` (the poller) and, if the answer is "yes," it materially reduces how much
of this project depends on the GDPR export. It is worth one call before anything is designed on top of
the assumption.

## Do

1. Add `spot probe --profile marc --follow-next N` (default 1, cap it low — 5). It follows the `next`
   URL from the previous page, honoring `Retry-After`, and persists each page to `raw.api_response`
   like any other response.
2. Run it with `N=3` for profile `marc`.
3. Report, per page: item count, min and max `played_at`, count of `played_at` values **not already
   seen on an earlier page**, and whether `next` was still non-null at the end.

## The question this answers

**Does page 2 contain plays older than page 1's oldest `played_at`, or does it repeat / come back
empty?** Say which, with the timestamps.

State it as a measurement, not a diagnosis. "Page 2 returned 50 items, all older than page 1's oldest,
`next` still non-null" is a measurement. "The endpoint supports unlimited backward paging" is an
inference from three pages and does not follow — depth is a separate question and Spotify may cut it
off at any point. If backward paging works, note how deep this test actually went and stop there.

## Then

Report to `claude_work/reports/spot-main-R-018-report.md` and **stop.** Do not amend `CLAUDE.md` §4 —
Cowork owns that paragraph and will rewrite it from your measurement.

## Constraints

- Rate limits: 30-second rolling window, dev-mode ceiling is lower and unpublished. Sleep between pages.
- The probe already takes the `spot_refresh` advisory lock. Keep that.
- Nothing under `data/` gets committed.
