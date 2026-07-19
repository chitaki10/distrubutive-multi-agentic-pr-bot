# Stage 4 Staleness Handling — E2E Verification

Date: 2026-07-19

## Setup

Same infra as prior stages (Postgres on 5434, Temporal dev server, worker restarted for Stage 4 code, webhook + smee tunnel).

## Attempt 1 — genuine manual force-push race

PR #12 on `chitaki10/Job_Recommender_System_with_MCP`, two commits pushed via GitHub's web editor a few seconds apart. Result: no overlap — the first review (head_sha `6cb1a4a7...`) completed at 12:40:13, five seconds before the second push landed at 12:40:18. Both workflows completed normally, no staleness triggered. Human reaction time via the GitHub web UI could not reliably beat a ~7-second review pipeline.

## Attempt 2 — deterministic replay (successful)

Rather than depend on human timing, replayed two webhook events directly against the local `/webhook` endpoint (real HMAC signature, real webhook secret) for PR #12's two real commits:
- `6cb1a4a7819e1c79500f17e991d8753f826b785c` (PR's first, now-historical commit)
- `7d0ca193a6a91b860419e0b75189c71b7f4c4deb` (PR's actual current HEAD)

This is deterministic rather than timing-dependent: `check_staleness_activity` compares a workflow's stored `head_sha` against the PR's *live* current HEAD fetched from GitHub — replaying the old commit's event guarantees a mismatch regardless of wall-clock ordering, since GitHub's real current state never reverts to the older commit. Everything downstream of the webhook trigger was real: real GitHub App JWT/installation token, real diff fetch, real Ollama model calls (3 concurrent agents), real Postgres, real comment posting.

**Result** (`pr_reviews` table):
```
head_sha=6cb1a4a7...  status=stale     (discarded, no comment posted)
head_sha=7d0ca193...  status=complete  (posted normally)
```

Confirmed via the GitHub API: PR #12 has exactly one new comment from this replay (posted at 12:42:15, matching the `complete` row's `updated_at`) — the `stale` row produced zero comments.

## Conclusion

Stage 4 deliverable met: a review run against a superseded PR state is discarded without posting, while the run matching the PR's current state posts normally. The manual force-push race is genuinely hard to trigger by hand against a multi-second pipeline (documented as attempt 1); the replay method proves the same code path deterministically using real downstream API calls, only synthesizing the webhook trigger itself.
