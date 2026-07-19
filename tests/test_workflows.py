# tests/test_workflows.py
import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from prbot.activity_types import FetchDiffInput, PostCommentInput, ReviewInput, SetStatusInput
from prbot.workflows import PRReviewWorkflow, ReviewEvent


async def test_workflow_calls_activities_in_order_and_completes():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

        @activity.defn(name="review_activity")
        async def fake_review(input: ReviewInput) -> str:
            calls.append(("review", input.diff_text))
            return "review-body"

        @activity.defn(name="post_comment_activity")
        async def fake_post_comment(input: PostCommentInput) -> int:
            calls.append(("post_comment", input.body))
            return 42

        async with Worker(
            env.client,
            task_queue="test-queue-1",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, fake_fetch_diff, fake_review, fake_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-1",
                task_queue="test-queue-1",
            )

        assert result == 42
        assert calls == [
            ("set_status", "running"),
            ("fetch_diff",),
            ("review", "diff-text"),
            ("post_comment", "review-body"),
            ("set_status", "complete"),
        ]


async def test_workflow_marks_failed_when_activity_exhausts_retries():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(input.status)

        @activity.defn(name="fetch_diff_activity")
        async def failing_fetch_diff(input: FetchDiffInput) -> str:
            raise RuntimeError("boom")

        @activity.defn(name="review_activity")
        async def unused_review(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called")

        async with Worker(
            env.client,
            task_queue="test-queue-2",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, failing_fetch_diff, unused_review, unused_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=8, head_sha="def456", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-2",
                    task_queue="test-queue-2",
                )

        assert calls == ["running", "failed"]
