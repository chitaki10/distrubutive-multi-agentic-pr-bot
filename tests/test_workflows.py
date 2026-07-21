import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from prbot.activity_types import (
    AggregateInput,
    DeleteCommentInput,
    FetchDiffInput,
    PostCommentInput,
    RecordStepInput,
    ReviewInput,
    SetStatusInput,
    StalenessCheckInput,
)
from prbot.orchestration.workflows import PRReviewWorkflow, ReviewEvent


def _agent_fakes(calls):
    @activity.defn(name="security_review_activity")
    async def fake_security(input: ReviewInput) -> str:
        calls.append(("security", input.diff_text))
        return "security-result"

    @activity.defn(name="style_review_activity")
    async def fake_style(input: ReviewInput) -> str:
        calls.append(("style", input.diff_text))
        return "style-result"

    @activity.defn(name="test_coverage_review_activity")
    async def fake_test_coverage(input: ReviewInput) -> str:
        calls.append(("test_coverage", input.diff_text))
        return "test-coverage-result"

    @activity.defn(name="aggregate_activity")
    async def fake_aggregate(input: AggregateInput) -> str:
        calls.append(("aggregate", input.security_result, input.style_result, input.test_coverage_result))
        return "aggregated-body"

    @activity.defn(name="record_state_version_activity")
    async def fake_record_step(input: RecordStepInput) -> str | None:
        calls.append(("record_step", input.step_seq, input.agent, input.skip_reason))
        if input.skip_reason is not None:
            return None
        return input.raw_output

    return [fake_security, fake_style, fake_test_coverage, fake_aggregate, fake_record_step]


async def test_workflow_completes_normally_when_no_failure_injected():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

        @activity.defn(name="check_staleness_activity")
        async def fake_check_staleness(input: StalenessCheckInput) -> bool:
            calls.append(("check_staleness", input.head_sha))
            return False

        @activity.defn(name="post_comment_activity")
        async def fake_post_comment(input: PostCommentInput) -> int:
            calls.append(("post_comment", input.body))
            return 42

        @activity.defn(name="check_demo_failure_injection_activity")
        async def fake_check_failure_injection() -> bool:
            calls.append(("check_failure_injection",))
            return False

        @activity.defn(name="delete_comment_activity")
        async def unused_delete_comment(input: DeleteCommentInput) -> None:
            raise AssertionError("should not be called when no failure is injected")

        async with Worker(
            env.client,
            task_queue="test-queue-6-1",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                fake_fetch_diff,
                *_agent_fakes(calls),
                fake_check_staleness,
                fake_post_comment,
                fake_check_failure_injection,
                unused_delete_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-6-1",
                task_queue="test-queue-6-1",
            )

        assert result == 42
        assert ("check_failure_injection",) in calls
        assert calls[-1] == ("set_status", "complete")
        assert ("record_step", 1, "fetch_diff", None) in calls
        assert ("record_step", 5, "aggregate", None) in calls


async def test_workflow_compensates_when_failure_injected():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

        @activity.defn(name="check_staleness_activity")
        async def fake_check_staleness(input: StalenessCheckInput) -> bool:
            calls.append(("check_staleness", input.head_sha))
            return False

        @activity.defn(name="post_comment_activity")
        async def fake_post_comment(input: PostCommentInput) -> int:
            calls.append(("post_comment", input.body))
            return 42

        @activity.defn(name="check_demo_failure_injection_activity")
        async def fake_check_failure_injection() -> bool:
            calls.append(("check_failure_injection",))
            return True

        @activity.defn(name="delete_comment_activity")
        async def fake_delete_comment(input: DeleteCommentInput) -> None:
            calls.append(("delete_comment", input.comment_id))

        async with Worker(
            env.client,
            task_queue="test-queue-6-2",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                fake_fetch_diff,
                *_agent_fakes(calls),
                fake_check_staleness,
                fake_post_comment,
                fake_check_failure_injection,
                fake_delete_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-6-2",
                    task_queue="test-queue-6-2",
                )

        assert ("delete_comment", 42) in calls
        assert calls[-1] == ("set_status", "failed")


async def test_workflow_discards_stale_run_without_posting():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

        @activity.defn(name="check_staleness_activity")
        async def fake_check_staleness(input: StalenessCheckInput) -> bool:
            calls.append(("check_staleness", input.head_sha))
            return True

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called when stale")

        async with Worker(
            env.client,
            task_queue="test-queue-6-3",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, fake_fetch_diff, *_agent_fakes(calls), fake_check_staleness, unused_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-6-3",
                task_queue="test-queue-6-3",
            )

        assert result == -1
        assert calls[-1] == ("set_status", "stale")


async def test_workflow_marks_failed_when_activity_exhausts_retries():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(input.status)

        @activity.defn(name="fetch_diff_activity")
        async def failing_fetch_diff(input: FetchDiffInput) -> str:
            raise RuntimeError("boom")

        @activity.defn(name="security_review_activity")
        async def unused_security(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="style_review_activity")
        async def unused_style(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="test_coverage_review_activity")
        async def unused_test_coverage(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="aggregate_activity")
        async def unused_aggregate(input: AggregateInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="check_staleness_activity")
        async def unused_check_staleness(input: StalenessCheckInput) -> bool:
            raise AssertionError("should not be called")

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called")

        @activity.defn(name="record_state_version_activity")
        async def unused_record_step(input: RecordStepInput) -> str | None:
            raise AssertionError("should not be called")

        async with Worker(
            env.client,
            task_queue="test-queue-6-4",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                failing_fetch_diff,
                unused_security,
                unused_style,
                unused_test_coverage,
                unused_aggregate,
                unused_check_staleness,
                unused_post_comment,
                unused_record_step,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=8, head_sha="def456", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-6-4",
                    task_queue="test-queue-6-4",
                )

        assert calls == ["running", "failed"]


async def test_workflow_marks_failed_when_fetch_diff_output_contract_rejected():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return ""

        @activity.defn(name="record_state_version_activity")
        async def fake_record_step(input: RecordStepInput) -> str | None:
            calls.append(("record_step", input.step_seq, input.agent))
            return None

        @activity.defn(name="security_review_activity")
        async def unused_security(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="style_review_activity")
        async def unused_style(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="test_coverage_review_activity")
        async def unused_test_coverage(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called")

        async with Worker(
            env.client,
            task_queue="test-queue-8-1",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                fake_fetch_diff,
                fake_record_step,
                unused_security,
                unused_style,
                unused_test_coverage,
                unused_post_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=9, head_sha="ghi789", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-8-1",
                    task_queue="test-queue-8-1",
                )

        assert calls == [("set_status", "running"), ("fetch_diff",), ("record_step", 1, "fetch_diff"), ("set_status", "failed")]
