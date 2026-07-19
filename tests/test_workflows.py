import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from prbot.activity_types import (
    AggregateInput,
    FetchDiffInput,
    PostCommentInput,
    ReviewInput,
    SetStatusInput,
    StalenessCheckInput,
)
from prbot.workflows import PRReviewWorkflow, ReviewEvent


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

    return [fake_security, fake_style, fake_test_coverage, fake_aggregate]


async def test_workflow_posts_when_not_stale():
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

        async with Worker(
            env.client,
            task_queue="test-queue-4-1",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, fake_fetch_diff, *_agent_fakes(calls), fake_check_staleness, fake_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-4-1",
                task_queue="test-queue-4-1",
            )

        assert result == 42
        call_types = [c[0] for c in calls]
        assert "check_staleness" in call_types
        assert call_types.index("check_staleness") > call_types.index("aggregate")
        assert call_types.index("post_comment") > call_types.index("check_staleness")
        assert calls[-1] == ("set_status", "complete")


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
            task_queue="test-queue-4-2",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, fake_fetch_diff, *_agent_fakes(calls), fake_check_staleness, unused_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-4-2",
                task_queue="test-queue-4-2",
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

        async with Worker(
            env.client,
            task_queue="test-queue-4-3",
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
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=8, head_sha="def456", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-4-3",
                    task_queue="test-queue-4-3",
                )

        assert calls == ["running", "failed"]
