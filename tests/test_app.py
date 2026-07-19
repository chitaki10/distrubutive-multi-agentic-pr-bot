import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from prbot import app as app_module


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("dummy-key")

    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "testsecret")

    app_module.get_settings.cache_clear()

    monkeypatch.setattr(app_module, "generate_app_jwt", lambda app_id, key: "fake.jwt")

    async def fake_run_stage1_review(event, settings, app_jwt):
        return 42

    monkeypatch.setattr(app_module, "run_stage1_review", fake_run_stage1_review)

    return TestClient(app_module.app)


def test_webhook_posts_review_on_valid_signature(client):
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
    assert response.json() == {"status": "posted", "comment_id": 42}


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
