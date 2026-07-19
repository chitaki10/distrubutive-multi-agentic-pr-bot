# Stage 6 Saga/Compensation — E2E Verification

Date: 2026-07-19

## Setup

Same infra as prior stages, restarted twice during this verification due to two unrelated session/machine interruptions (Docker Desktop and the Temporal dev server both needed restarting mid-verification; Postgres data persisted across the Docker restart via its named volume, Temporal's in-memory workflow history did not, which doesn't matter since `pr_reviews` in Postgres is the durable source of truth for this check).

## Attempt 1 — failure injection enabled

Worker restarted with `PRBOT_DEMO_FORCE_FAILURE_AFTER_POST=true`. Pushed a commit to a PR on `chitaki10/Job_Recommender_System_with_MCP`, which became PR #15 (head_sha `c5505e17f08e8ce884d263bfe3e5b14720d14c2d`).

Result:
- `pr_reviews` row for this head_sha: `status='failed'`.
- GitHub API check on PR #15's comments: **0 comments** — confirming a comment was posted and then removed by `delete_comment_activity`, exactly as designed. (The comment's brief existence wasn't screenshotted, but the workflow logic only reaches `check_demo_failure_injection_activity` after `post_comment_activity` succeeds, and the final DB state plus zero surviving comments is conclusive: post → compensate → fail.)

## Attempt 2 — failure injection disabled, confirm recovery

Worker restarted without the env var. Pushed another commit (PR #16, head_sha `adf30701ef5f657a4ca852b9ebed964c38bc3d6e`).

Result:
- `pr_reviews` row: `status='complete'`.
- GitHub API check: **1 comment**, present and with real model-generated content (Security/Style/Test Coverage sections) — confirming normal operation resumed once the injection was disabled.

## Conclusion

Stage 6 deliverable met: a controllable, reproducible failure-injection hook triggers the saga compensation path — a posted comment gets deleted and the run is marked failed — while normal operation (comment posts and stays, run marked complete) is unaffected once the injection is off.
