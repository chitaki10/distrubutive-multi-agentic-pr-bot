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
)
from prbot.workflows import PRReviewWorkflow, ReviewEvent


async def test_workflow_runs_agents_concurrently_and_completes():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

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

        @activity.defn(name="post_comment_activity")
        async def fake_post_comment(input: PostCommentInput) -> int:
            calls.append(("post_comment", input.body))
            return 42

        async with Worker(
            env.client,
            task_queue="test-queue-3-1",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                fake_fetch_diff,
                fake_security,
                fake_style,
                fake_test_coverage,
                fake_aggregate,
                fake_post_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-3-1",
                task_queue="test-queue-3-1",
            )

        assert result == 42

        call_types = [c[0] for c in calls]
        assert call_types[0] == "set_status"
        assert calls[0][1] == "running"
        assert call_types[1] == "fetch_diff"
        # the three agent calls happen concurrently; assert all three occurred
        # between fetch_diff and aggregate, in any relative order
        agent_calls = {c[0] for c in calls if c[0] in ("security", "style", "test_coverage")}
        assert agent_calls == {"security", "style", "test_coverage"}
        for c in calls:
            if c[0] in ("security", "style", "test_coverage"):
                assert c[1] == "diff-text"
        aggregate_call = next(c for c in calls if c[0] == "aggregate")
        assert aggregate_call == ("aggregate", "security-result", "style-result", "test-coverage-result")
        assert call_types[-2] == "post_comment"
        assert calls[-2][1] == "aggregated-body"
        assert call_types[-1] == "set_status"
        assert calls[-1][1] == "complete"


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

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called")

        async with Worker(
            env.client,
            task_queue="test-queue-3-2",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                failing_fetch_diff,
                unused_security,
                unused_style,
                unused_test_coverage,
                unused_aggregate,
                unused_post_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=8, head_sha="def456", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-3-2",
                    task_queue="test-queue-3-2",
                )

        assert calls == ["running", "failed"]
