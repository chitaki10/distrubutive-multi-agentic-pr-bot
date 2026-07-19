# Stage 5 Circuit Breaker — E2E Verification

Date: 2026-07-19

## Setup

Same infra as prior stages. Worker restarted for Stage 5 code (fresh, closed breakers).

## Attempt 1 — Ollama down

Stopped both Ollama processes (`ollama.exe serve` and `ollama app.exe`). Confirmed unreachable (`curl` to `localhost:11434/api/version` failed). Pushed a commit to PR #13 on `chitaki10/Job_Recommender_System_with_MCP`.

Result: `pr_reviews` row for head_sha `afae12e...` reached `status='complete'` (not `failed`) in ~14 seconds (matches 3 Temporal retry attempts with backoff before each agent's breaker tripped on the 3rd attempt, per `fail_max=3` == `RetryPolicy(maximum_attempts=3)`). Posted comment:

```
### Security
_Security check skipped._

### Style
_Style check skipped._

### Test Coverage
_Test Coverage check skipped._
```

All three agents' independent breakers tripped (expected — all three hit the same unreachable Ollama target). Workflow did not hang and did not fail; it completed with a partial/skipped review, exactly as designed.

## Attempt 2 — Ollama restarted, recovery confirmed

Restarted Ollama (`ollama app.exe`), confirmed `api/version` reachable again. Pushed another commit (PR #14). Result: completed in ~9.6 seconds (matches a normal single-attempt run, no retries/backoff), with real model-generated content in all three sections — the breakers had reset (more than `reset_timeout=60s` had elapsed) and successful calls closed them again.

## Conclusion

Stage 5 deliverable met: a broken model target degrades to a partial review (marking the affected sections "skipped") instead of hanging or failing the whole workflow, and normal operation resumes automatically once the target recovers.
