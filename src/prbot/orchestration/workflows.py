import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

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


@dataclass
class ReviewEvent:
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    installation_id: str


@workflow.defn
class PRReviewWorkflow:
    @workflow.run
    async def run(self, event: ReviewEvent) -> int:
        retry_policy = RetryPolicy(maximum_attempts=3)
        workflow_id = workflow.info().workflow_id

        async def set_status(status: str) -> None:
            await workflow.execute_activity(
                "set_review_status_activity",
                SetStatusInput(repo=event.repo, pr_number=event.pr_number, head_sha=event.head_sha, status=status),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_policy,
            )

        async def record_step(
            step_seq: int, agent: str, raw_output: str | None, skip_reason: str | None, reference_text: str | None
        ) -> str | None:
            return await workflow.execute_activity(
                "record_state_version_activity",
                RecordStepInput(
                    workflow_id=workflow_id,
                    step_seq=step_seq,
                    agent=agent,
                    raw_output=raw_output,
                    skip_reason=skip_reason,
                    reference_text=reference_text,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_policy,
            )

        await set_status("running")

        try:
            raw_diff = await workflow.execute_activity(
                "fetch_diff_activity",
                FetchDiffInput(
                    installation_id=event.installation_id,
                    owner=event.owner,
                    repo=event.repo,
                    pr_number=event.pr_number,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )

            diff = await record_step(1, "fetch_diff", raw_diff, None, None)
            if diff is None:
                await set_status("failed")
                raise ApplicationError("Contract rejected fetch_diff output", non_retryable=True)

            review_input = ReviewInput(diff_text=diff)
            raw_security, raw_style, raw_test_coverage = await asyncio.gather(
                workflow.execute_activity(
                    "security_review_activity",
                    review_input,
                    start_to_close_timeout=timedelta(seconds=180),
                    retry_policy=retry_policy,
                ),
                workflow.execute_activity(
                    "style_review_activity",
                    review_input,
                    start_to_close_timeout=timedelta(seconds=180),
                    retry_policy=retry_policy,
                ),
                workflow.execute_activity(
                    "test_coverage_review_activity",
                    review_input,
                    start_to_close_timeout=timedelta(seconds=180),
                    retry_policy=retry_policy,
                ),
            )

            security_result, style_result, test_coverage_result = await asyncio.gather(
                record_step(
                    2, "security_review", raw_security,
                    None if raw_security is not None else "circuit_breaker_open",
                    diff,
                ),
                record_step(
                    3, "style_review", raw_style,
                    None if raw_style is not None else "circuit_breaker_open",
                    diff,
                ),
                record_step(
                    4, "test_coverage_review", raw_test_coverage,
                    None if raw_test_coverage is not None else "circuit_breaker_open",
                    diff,
                ),
            )

            review_body = await workflow.execute_activity(
                "aggregate_activity",
                AggregateInput(
                    security_result=security_result,
                    style_result=style_result,
                    test_coverage_result=test_coverage_result,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_policy,
            )

            await record_step(5, "aggregate", review_body, None, None)

            is_stale = await workflow.execute_activity(
                "check_staleness_activity",
                StalenessCheckInput(
                    installation_id=event.installation_id,
                    owner=event.owner,
                    repo=event.repo,
                    pr_number=event.pr_number,
                    head_sha=event.head_sha,
                ),
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=retry_policy,
            )

            if is_stale:
                await set_status("stale")
                return -1

            comment_id = await workflow.execute_activity(
                "post_comment_activity",
                PostCommentInput(
                    installation_id=event.installation_id,
                    owner=event.owner,
                    repo=event.repo,
                    pr_number=event.pr_number,
                    body=review_body,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )
        except ActivityError:
            await set_status("failed")
            raise

        should_fail = await workflow.execute_activity(
            "check_demo_failure_injection_activity",
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_policy,
        )

        if should_fail:
            await workflow.execute_activity(
                "delete_comment_activity",
                DeleteCommentInput(
                    installation_id=event.installation_id,
                    owner=event.owner,
                    repo=event.repo,
                    comment_id=comment_id,
                ),
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=retry_policy,
            )
            await set_status("failed")
            raise ApplicationError(
                "Demo failure injection triggered after posting comment", non_retryable=True
            )

        await set_status("complete")
        return comment_id
