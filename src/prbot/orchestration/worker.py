import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from prbot.agents.activities import (
    aggregate_activity,
    check_demo_failure_injection_activity,
    check_staleness_activity,
    delete_comment_activity,
    fetch_diff_activity,
    post_comment_activity,
    review_activity,
    set_review_status_activity,
)
from prbot.agents.security import security_review_activity
from prbot.agents.style import style_review_activity
from prbot.agents.test_coverage import test_coverage_review_activity
from prbot.state.db import init_db
from prbot.state.versioned_log import record_state_version_activity
from prbot.orchestration.workflows import PRReviewWorkflow

TASK_QUEUE = "pr-review-task-queue"


async def main() -> None:
    await init_db()
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[PRReviewWorkflow],
        activities=[
            fetch_diff_activity,
            review_activity,
            post_comment_activity,
            set_review_status_activity,
            security_review_activity,
            style_review_activity,
            test_coverage_review_activity,
            aggregate_activity,
            check_staleness_activity,
            check_demo_failure_injection_activity,
            delete_comment_activity,
            record_state_version_activity,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
