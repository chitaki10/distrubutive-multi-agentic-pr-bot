from fastapi import FastAPI, HTTPException, Request
from temporalio.client import Client

from prbot.config import get_settings
from prbot.events import parse_pull_request_event, verify_signature
from prbot.workflows import PRReviewWorkflow, ReviewEvent

TASK_QUEUE = "pr-review-task-queue"

app = FastAPI()

_temporal_client: Client | None = None


async def get_temporal_client() -> Client:
    global _temporal_client
    if _temporal_client is None:
        _temporal_client = await Client.connect("localhost:7233")
    return _temporal_client


@app.post("/webhook")
async def handle_webhook(request: Request):
    settings = get_settings()
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")

    if not verify_signature(body, signature, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()
    event = parse_pull_request_event(payload)
    if event is None:
        return {"status": "ignored"}

    client = await get_temporal_client()
    review_event = ReviewEvent(
        owner=event.owner,
        repo=event.repo,
        pr_number=event.pr_number,
        head_sha=event.head_sha,
        installation_id=event.installation_id,
    )
    workflow_id = f"{event.owner}/{event.repo}#{event.pr_number}@{event.head_sha}"
    handle = await client.start_workflow(
        PRReviewWorkflow.run,
        review_event,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    return {"status": "started", "workflow_id": handle.id}
