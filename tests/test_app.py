import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from prbot.api import app as app_module
from prbot.orchestration.workflows import PRReviewWorkflow, ReviewEvent


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeHandle:
    def __init__(self, id: str):
        self.id = id


class FakeTemporalClient:
    def __init__(self):
        self.start_workflow_calls = []

    async def start_workflow(self, workflow_run, event, *, id, task_queue):
        self.start_workflow_calls.append((workflow_run, event, id, task_queue))
        return FakeHandle(id)


@pytest.fixture
def client(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("dummy-key")

    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "testsecret")

    app_module.get_settings.cache_clear()

    fake_client = FakeTemporalClient()

    async def fake_get_temporal_client():
        return fake_client

    monkeypatch.setattr(app_module, "get_temporal_client", fake_get_temporal_client)

    test_client = TestClient(app_module.app)
    test_client.fake_temporal = fake_client
    return test_client


def test_webhook_starts_workflow_on_valid_signature(client):
    payload = {
        "action": "opened",
        "pull_request": {"number": 7, "head": {"sha": "abc123"}},
        "repository": {"name": "demo", "owner": {"login": "chitaki10"}},
        "installation": {"id": 55},
    }
    body = json.dumps(payload).encode()
    signature = _sign(body, "testsecret")

    response = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": signature, "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "started", "workflow_id": "chitaki10/demo#7@abc123"}
    assert len(client.fake_temporal.start_workflow_calls) == 1

    # Verify the exact arguments passed to start_workflow
    workflow_run, event, workflow_id, task_queue = client.fake_temporal.start_workflow_calls[0]
    assert workflow_run is PRReviewWorkflow.run
    assert event == ReviewEvent(
        owner="chitaki10",
        repo="demo",
        pr_number=7,
        head_sha="abc123",
        installation_id="55",
    )
    assert workflow_id == "chitaki10/demo#7@abc123"
    assert task_queue == app_module.TASK_QUEUE


def test_webhook_rejects_invalid_signature(client):
    body = json.dumps({"action": "opened"}).encode()

    response = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": "sha256=deadbeef", "content-type": "application/json"},
    )

    assert response.status_code == 401


def test_webhook_ignores_non_pr_events(client):
    body = json.dumps({"action": "closed"}).encode()
    signature = _sign(body, "testsecret")

    response = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": signature, "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
