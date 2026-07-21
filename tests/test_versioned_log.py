import pytest

from prbot.activity_types import RecordStepInput
from prbot.state import db, versioned_log


@pytest.fixture(autouse=True)
async def reset_pool():
    db._pool = None
    yield
    if db._pool is not None:
        await db._pool.close()
        db._pool = None


async def test_record_step_and_get_steps_for_workflow_round_trip():
    await db.init_db()
    workflow_id = "test-owner/test-repo#9002@sha-versioned-log-1"

    await versioned_log.record_step(workflow_id, 1, "fetch_diff", "ok", "diff-content")
    await versioned_log.record_step(workflow_id, 2, "security_review", "contract_rejected:too_short", None)

    steps = await versioned_log.get_steps_for_workflow(workflow_id)

    assert len(steps) == 2
    assert steps[0]["step_seq"] == 1
    assert steps[0]["agent"] == "fetch_diff"
    assert steps[0]["status"] == "ok"
    assert steps[0]["output"] == "diff-content"
    assert steps[1]["step_seq"] == 2
    assert steps[1]["status"] == "contract_rejected:too_short"
    assert steps[1]["output"] is None


async def test_record_step_is_idempotent_on_retry():
    await db.init_db()
    workflow_id = "test-owner/test-repo#9003@sha-versioned-log-2"

    await versioned_log.record_step(workflow_id, 1, "fetch_diff", "ok", "diff-content")
    await versioned_log.record_step(workflow_id, 1, "fetch_diff", "ok", "diff-content")

    steps = await versioned_log.get_steps_for_workflow(workflow_id)
    assert len(steps) == 1


async def test_record_state_version_activity_accepts_valid_output():
    await db.init_db()
    workflow_id = "test-owner/test-repo#9004@sha-versioned-log-3"

    result = await versioned_log.record_state_version_activity(
        RecordStepInput(
            workflow_id=workflow_id,
            step_seq=2,
            agent="security_review",
            raw_output="No issues found in this diff, looks clean overall.",
            skip_reason=None,
            reference_text="diff --git a/x.py b/x.py\n+print(1)\n",
        )
    )

    assert result == "No issues found in this diff, looks clean overall."
    steps = await versioned_log.get_steps_for_workflow(workflow_id)
    assert steps[0]["status"] == "ok"


async def test_record_state_version_activity_rejects_echoed_diff():
    await db.init_db()
    workflow_id = "test-owner/test-repo#9005@sha-versioned-log-4"
    diff = "diff --git a/x.py b/x.py\n+print(1)\n"

    result = await versioned_log.record_state_version_activity(
        RecordStepInput(
            workflow_id=workflow_id,
            step_seq=2,
            agent="security_review",
            raw_output=diff,
            skip_reason=None,
            reference_text=diff,
        )
    )

    assert result is None
    steps = await versioned_log.get_steps_for_workflow(workflow_id)
    assert steps[0]["status"] == "contract_rejected:echoed_input"


async def test_record_state_version_activity_accepts_large_diff_when_length_check_skipped():
    await db.init_db()
    workflow_id = "test-owner/test-repo#9007@sha-versioned-log-6"
    huge_diff = "diff --git a/x.py b/x.py\n+print(1)\n" * 2000

    result = await versioned_log.record_state_version_activity(
        RecordStepInput(
            workflow_id=workflow_id,
            step_seq=1,
            agent="fetch_diff",
            raw_output=huge_diff,
            skip_reason=None,
            reference_text=None,
            skip_length_check=True,
        )
    )

    assert result == huge_diff
    steps = await versioned_log.get_steps_for_workflow(workflow_id)
    assert steps[0]["status"] == "ok"


async def test_record_state_version_activity_records_circuit_breaker_skip():
    await db.init_db()
    workflow_id = "test-owner/test-repo#9006@sha-versioned-log-5"

    result = await versioned_log.record_state_version_activity(
        RecordStepInput(
            workflow_id=workflow_id,
            step_seq=3,
            agent="style_review",
            raw_output=None,
            skip_reason="circuit_breaker_open",
            reference_text=None,
        )
    )

    assert result is None
    steps = await versioned_log.get_steps_for_workflow(workflow_id)
    assert steps[0]["status"] == "circuit_breaker_open"
