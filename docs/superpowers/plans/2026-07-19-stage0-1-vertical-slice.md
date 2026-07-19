# Stage 0+1: Scaffold + Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the repo scaffold, then build the thinnest real end-to-end path: a GitHub App receives a PR webhook, the bot fetches the diff, calls a local Ollama model once, and posts one review comment back to the real PR. No Temporal, no Postgres usage yet (Postgres is provisioned but unused until Stage 2).

**Architecture:** FastAPI app exposes `POST /webhook`, verifies the GitHub HMAC signature, parses the `pull_request` event, exchanges the GitHub App's JWT for an installation token, fetches the PR diff, sends it to a local Ollama model, and posts the model's response as a PR comment — all synchronously in the request handler. See `docs/superpowers/specs/2026-07-19-pr-review-bot-design.md` for the full multi-stage design; this plan covers only Stage 0 and Stage 1 of that doc.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx, PyJWT (RS256), pydantic-settings, pytest + pytest-asyncio + respx for tests, Postgres 16 via docker-compose (provisioned, not yet used), Ollama serving `qwen2.5-coder:3b` (Q4_K_M quant), smee.io for local webhook tunneling.

## Global Constraints

- Python >= 3.11.
- No Redis in the stack (see spec: Temporal's task queues + one-workflow-per-PR make it redundant).
- Ollama, not vLLM, for local model serving — target GPU is 4GB VRAM (RTX 3050 Ti Laptop).
- Model: `qwen2.5-coder:3b` Q4_K_M as primary; fall back to `qwen2.5-coder:1.5b` only if latency is unworkable.
- Single worker process, single local GPU — no scaling concerns in this build.
- GitHub App (not PAT) for auth, per approved design.
- Package layout: application code under `src/prbot/`, tests under `tests/`.

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/prbot/__init__.py`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `.gitignore`

**Interfaces:**
- Produces: installable package `prbot` (editable install), pytest test-discovery under `tests/`, `docker-compose up -d` bringing up a `postgres:16` service on port 5432.

- [ ] **Step 1: Create directory structure**

Run:
```bash
mkdir -p src/prbot tests/fixtures
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "prbot"
version = "0.1.0"
description = "Multi-agent GitHub PR review bot"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "httpx>=0.27",
    "pyjwt[crypto]>=2.9",
    "pydantic-settings>=2.5",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: Write `src/prbot/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Write `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: prbot
      POSTGRES_PASSWORD: prbot
      POSTGRES_DB: prbot
    ports:
      - "5432:5432"
    volumes:
      - prbot_pgdata:/var/lib/postgresql/data

volumes:
  prbot_pgdata:
```

Note: Temporal's dev server (`temporal server start-dev`) is a single CLI binary, not containerized here — install it separately when Stage 2 needs it. Only Postgres is provisioned now, ahead of Stage 2's `pr_reviews` table.

- [ ] **Step 5: Write `.env.example`**

```
GITHUB_APP_ID=
GITHUB_PRIVATE_KEY_PATH=./github-app-private-key.pem
GITHUB_WEBHOOK_SECRET=
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:3b
```

- [ ] **Step 6: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.env
*.pem
```

- [ ] **Step 7: Create venv and install package**

Run:
```bash
python -m venv .venv
```

Windows activation + install:
```bash
.venv/Scripts/pip install -e ".[dev]"
```

- [ ] **Step 8: Verify scaffold**

Run:
```bash
docker-compose up -d
docker-compose ps
.venv/Scripts/python -c "import prbot; print(prbot.__version__)"
```
Expected: `postgres` service listed as running/healthy; prints `0.1.0`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml src/prbot/__init__.py docker-compose.yml .env.example .gitignore
git commit -m "chore: project scaffold (Stage 0)"
```

---

### Task 2: Settings/config loading

**Files:**
- Create: `src/prbot/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `prbot.config.Settings` (pydantic `BaseSettings`) with fields `github_app_id: str`, `github_private_key_path: str`, `github_webhook_secret: str`, `ollama_base_url: str = "http://localhost:11434"`, `ollama_model: str = "qwen2.5-coder:3b"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from prbot.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "/tmp/key.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cr3t")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.github_app_id == "12345"
    assert settings.github_private_key_path == "/tmp/key.pem"
    assert settings.github_webhook_secret == "s3cr3t"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_model == "qwen2.5-coder:3b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/prbot/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    github_app_id: str
    github_private_key_path: str
    github_webhook_secret: str
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:3b"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/prbot/config.py tests/test_config.py
git commit -m "feat: add Settings config loader"
```

---

### Task 3: GitHub App JWT + installation token exchange

**Files:**
- Create: `src/prbot/github_client.py`
- Test: `tests/test_github_client_auth.py`

**Interfaces:**
- Consumes: none (first github_client functions)
- Produces: `prbot.github_client.generate_app_jwt(app_id: str, private_key_pem: str) -> str`; `prbot.github_client.get_installation_token(app_jwt: str, installation_id: str, base_url: str = "https://api.github.com") -> str` (async)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_github_client_auth.py
import httpx
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from prbot.github_client import generate_app_jwt, get_installation_token


def _generate_test_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def test_generate_app_jwt_has_correct_claims():
    private_pem, public_pem = _generate_test_keypair()

    token = generate_app_jwt("123456", private_pem)

    decoded = jwt.decode(token, public_pem, algorithms=["RS256"], issuer="123456")
    assert decoded["iss"] == "123456"
    assert decoded["exp"] > decoded["iat"]


@respx.mock
async def test_get_installation_token_returns_token():
    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(return_value=httpx.Response(201, json={"token": "ghs_abc123"}))

    token = await get_installation_token("fake.jwt.token", "999")

    assert token == "ghs_abc123"
    assert route.called
    sent_headers = route.calls.last.request.headers
    assert sent_headers["authorization"] == "Bearer fake.jwt.token"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest tests/test_github_client_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.github_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/prbot/github_client.py
import time

import httpx
import jwt


def generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


async def get_installation_token(
    app_jwt: str, installation_id: str, base_url: str = "https://api.github.com"
) -> str:
    url = f"{base_url}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        return response.json()["token"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest tests/test_github_client_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/prbot/github_client.py tests/test_github_client_auth.py
git commit -m "feat: GitHub App JWT and installation token exchange"
```

---

### Task 4: Fetch PR diff + post PR comment

**Files:**
- Modify: `src/prbot/github_client.py`
- Test: `tests/test_github_client_pr.py`

**Interfaces:**
- Consumes: nothing from Task 3 directly (takes a token string as input)
- Produces: `prbot.github_client.fetch_pr_diff(token: str, owner: str, repo: str, pr_number: int, base_url: str = "https://api.github.com") -> str` (async); `prbot.github_client.post_pr_comment(token: str, owner: str, repo: str, pr_number: int, body: str, base_url: str = "https://api.github.com") -> int` (async)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_github_client_pr.py
import httpx
import respx

from prbot.github_client import fetch_pr_diff, post_pr_comment


@respx.mock
async def test_fetch_pr_diff_returns_diff_text():
    respx.get("https://api.github.com/repos/chitaki10/demo/pulls/7").mock(
        return_value=httpx.Response(200, text="diff --git a/x.py b/x.py\n+print(1)\n")
    )

    diff = await fetch_pr_diff("ghs_token", "chitaki10", "demo", 7)

    assert "print(1)" in diff


@respx.mock
async def test_post_pr_comment_returns_comment_id():
    route = respx.post(
        "https://api.github.com/repos/chitaki10/demo/issues/7/comments"
    ).mock(return_value=httpx.Response(201, json={"id": 42}))

    comment_id = await post_pr_comment("ghs_token", "chitaki10", "demo", 7, "nice PR")

    assert comment_id == 42
    sent_body = route.calls.last.request.content
    assert b"nice PR" in sent_body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest tests/test_github_client_pr.py -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_pr_diff'`

- [ ] **Step 3: Add to `src/prbot/github_client.py`**

```python
async def fetch_pr_diff(
    token: str, owner: str, repo: str, pr_number: int, base_url: str = "https://api.github.com"
) -> str:
    url = f"{base_url}/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.diff",
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text


async def post_pr_comment(
    token: str,
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
    base_url: str = "https://api.github.com",
) -> int:
    url = f"{base_url}/repos/{owner}/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json={"body": body})
        response.raise_for_status()
        return response.json()["id"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest tests/test_github_client_pr.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/prbot/github_client.py tests/test_github_client_pr.py
git commit -m "feat: fetch PR diff and post PR comment"
```

---

### Task 5: Webhook signature verification + event parsing

**Files:**
- Create: `src/prbot/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Produces: `prbot.events.PullRequestEvent` dataclass (`owner: str, repo: str, pr_number: int, head_sha: str, installation_id: str`); `prbot.events.verify_signature(payload_body: bytes, signature_header: str | None, secret: str) -> bool`; `prbot.events.parse_pull_request_event(payload: dict) -> PullRequestEvent | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_events.py
import hashlib
import hmac

from prbot.events import PullRequestEvent, parse_pull_request_event, verify_signature


def test_verify_signature_accepts_valid_signature():
    secret = "testsecret"
    body = b'{"action": "opened"}'
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_signature(body, signature, secret) is True


def test_verify_signature_rejects_invalid_signature():
    assert verify_signature(b'{"action": "opened"}', "sha256=deadbeef", "testsecret") is False


def test_verify_signature_rejects_missing_header():
    assert verify_signature(b"{}", None, "testsecret") is False


def test_parse_pull_request_event_extracts_fields_on_opened():
    payload = {
        "action": "opened",
        "pull_request": {"number": 7, "head": {"sha": "abc123"}},
        "repository": {"name": "demo", "owner": {"login": "chitaki10"}},
        "installation": {"id": 55},
    }

    event = parse_pull_request_event(payload)

    assert event == PullRequestEvent(
        owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55"
    )


def test_parse_pull_request_event_returns_none_for_other_actions():
    assert parse_pull_request_event({"action": "closed"}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.events'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/prbot/events.py
import hashlib
import hmac
from dataclasses import dataclass


@dataclass
class PullRequestEvent:
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    installation_id: str


def verify_signature(payload_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def parse_pull_request_event(payload: dict) -> PullRequestEvent | None:
    if payload.get("action") not in ("opened", "synchronize"):
        return None
    pr = payload["pull_request"]
    repo = payload["repository"]
    installation = payload.get("installation", {})
    return PullRequestEvent(
        owner=repo["owner"]["login"],
        repo=repo["name"],
        pr_number=pr["number"],
        head_sha=pr["head"]["sha"],
        installation_id=str(installation["id"]),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest tests/test_events.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/prbot/events.py tests/test_events.py
git commit -m "feat: webhook signature verification and event parsing"
```

---

### Task 6: Ollama LLM client wrapper

**Files:**
- Create: `src/prbot/llm_client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Produces: `prbot.llm_client.review_diff(diff_text: str, base_url: str, model: str) -> str` (async)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_client.py
import httpx
import respx

from prbot.llm_client import review_diff


@respx.mock
async def test_review_diff_returns_model_content():
    route = respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Looks good, add tests for edge case X."}}]},
        )
    )

    result = await review_diff(
        "diff --git a/x.py b/x.py\n+print(1)", "http://localhost:11434", "qwen2.5-coder:3b"
    )

    assert result == "Looks good, add tests for edge case X."
    sent_body = route.calls.last.request.content
    assert b"qwen2.5-coder:3b" in sent_body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest tests/test_llm_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.llm_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/prbot/llm_client.py
import httpx

REVIEW_SYSTEM_PROMPT = (
    "You are a senior software engineer reviewing a GitHub pull request diff. "
    "Give a concise, actionable code review in markdown covering security, "
    "style, and missing test coverage. Keep it under 300 words."
)


async def review_diff(diff_text: str, base_url: str, model: str) -> str:
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": diff_text},
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/pytest tests/test_llm_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/prbot/llm_client.py tests/test_llm_client.py
git commit -m "feat: Ollama review client"
```

---

### Task 7: Stage-1 review orchestration

**Files:**
- Create: `src/prbot/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `prbot.github_client.get_installation_token`, `prbot.github_client.fetch_pr_diff`, `prbot.github_client.post_pr_comment`, `prbot.llm_client.review_diff`, `prbot.events.PullRequestEvent`, `prbot.config.Settings`
- Produces: `prbot.review.run_stage1_review(event: PullRequestEvent, settings: Settings, app_jwt: str) -> int` (async, returns posted comment id)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review.py
from prbot import review
from prbot.config import Settings
from prbot.events import PullRequestEvent


def _settings():
    return Settings(
        _env_file=None,
        github_app_id="1",
        github_private_key_path="unused.pem",
        github_webhook_secret="unused",
    )


async def test_run_stage1_review_calls_apis_in_order(monkeypatch):
    calls = []

    async def fake_get_installation_token(app_jwt, installation_id):
        calls.append(("token", app_jwt, installation_id))
        return "ghs_token"

    async def fake_fetch_pr_diff(token, owner, repo, pr_number):
        calls.append(("diff", token, owner, repo, pr_number))
        return "diff-content"

    async def fake_review_diff(diff_text, base_url, model):
        calls.append(("review", diff_text, base_url, model))
        return "review-body"

    async def fake_post_pr_comment(token, owner, repo, pr_number, body):
        calls.append(("comment", token, owner, repo, pr_number, body))
        return 99

    monkeypatch.setattr(review.github_client, "get_installation_token", fake_get_installation_token)
    monkeypatch.setattr(review.github_client, "fetch_pr_diff", fake_fetch_pr_diff)
    monkeypatch.setattr(review.llm_client, "review_diff", fake_review_diff)
    monkeypatch.setattr(review.github_client, "post_pr_comment", fake_post_pr_comment)

    event = PullRequestEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")

    comment_id = await review.run_stage1_review(event, _settings(), "fake.jwt")

    assert comment_id == 99
    assert calls == [
        ("token", "fake.jwt", "55"),
        ("diff", "ghs_token", "chitaki10", "demo", 7),
        ("review", "diff-content", "http://localhost:11434", "qwen2.5-coder:3b"),
        ("comment", "ghs_token", "chitaki10", "demo", 7, "review-body"),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/pytest tests/test_review.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.review'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/prbot/review.py
from prbot import github_client, llm_client
from prbot.config import Settings
from prbot.events import PullRequestEvent


async def run_stage1_review(event: PullRequestEvent, settings: Settings, app_jwt: str) -> int:
    token = await github_client.get_installation_token(app_jwt, event.installation_id)
    diff = await github_client.fetch_pr_diff(token, event.owner, event.repo, event.pr_number)
    review_body = await llm_client.review_diff(diff, settings.ollama_base_url, settings.ollama_model)
    comment_id = await github_client.post_pr_comment(
        token, event.owner, event.repo, event.pr_number, review_body
    )
    return comment_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/pytest tests/test_review.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/prbot/review.py tests/test_review.py
git commit -m "feat: stage-1 review orchestration"
```

---

### Task 8: FastAPI webhook endpoint

**Files:**
- Create: `src/prbot/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `prbot.config.Settings`, `prbot.github_client.generate_app_jwt`, `prbot.events.verify_signature`, `prbot.events.parse_pull_request_event`, `prbot.review.run_stage1_review`
- Produces: `prbot.app.app` (FastAPI instance), `prbot.app.get_settings() -> Settings` (lru-cached), `POST /webhook` route

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_app.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.app'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/prbot/app.py
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from prbot.config import Settings
from prbot.events import parse_pull_request_event, verify_signature
from prbot.github_client import generate_app_jwt
from prbot.review import run_stage1_review

app = FastAPI()


@lru_cache
def get_settings() -> Settings:
    return Settings()


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

    private_key = Path(settings.github_private_key_path).read_text()
    app_jwt = generate_app_jwt(settings.github_app_id, private_key)

    comment_id = await run_stage1_review(event, settings, app_jwt)
    return {"status": "posted", "comment_id": comment_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/pytest tests/test_app.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `.venv/Scripts/pytest -v`
Expected: all tests pass (Tasks 2-8 combined)

- [ ] **Step 6: Commit**

```bash
git add src/prbot/app.py tests/test_app.py
git commit -m "feat: FastAPI webhook endpoint wiring Stage 1"
```

---

### Task 9: GitHub App registration + smee tunnel + manual E2E verification

**Files:**
- Create: `docs/superpowers/plans/2026-07-19-stage0-1-verification.md` (checklist record of the manual run)

**Interfaces:**
- Consumes: `prbot.app.app` (running via uvicorn), a real GitHub App installed on a scratch/test repo.

This task has no automated test — it is the Stage 1 demo checkpoint from the design doc: prove the GitHub App + webhook + Ollama loop end-to-end against a real PR.

- [ ] **Step 1: Create the GitHub App**

In GitHub: Settings → Developer settings → GitHub Apps → New GitHub App.
- Permissions: `Pull requests: Read-only`, `Issues: Read & write` (issue-comment endpoint is used for PR comments), `Metadata: Read-only` (default).
- Subscribe to events: `Pull request`.
- Webhook: leave URL blank for now, fill in after Step 2. Generate and note a webhook secret.
- Generate a private key (downloads a `.pem` file) — save it as `github-app-private-key.pem` in the repo root (already gitignored).
- Note the App ID shown on the app's settings page.
- Install the app on a scratch/test repo you control.

- [ ] **Step 2: Start the smee tunnel**

Create a channel at https://smee.io and note its URL, then run:
```bash
npx smee-client -u https://smee.io/<your-channel-id> -t http://localhost:8000/webhook
```
Set the GitHub App's Webhook URL to `https://smee.io/<your-channel-id>` and save.

- [ ] **Step 3: Pull the model and start Ollama**

```bash
ollama pull qwen2.5-coder:3b
```
Confirm Ollama is serving on `http://localhost:11434` (default when installed as a service, or run `ollama serve`).

- [ ] **Step 4: Set environment variables**

Copy `.env.example` to `.env` and fill in `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY_PATH=./github-app-private-key.pem`, `GITHUB_WEBHOOK_SECRET` (matching what you set in Step 1).

- [ ] **Step 5: Run the webhook server**

```bash
.venv/Scripts/uvicorn prbot.app:app --reload --port 8000
```

- [ ] **Step 6: Trigger and verify**

Open a real pull request (or push a commit to an existing open PR) on the scratch repo the App is installed on. Confirm:
- The FastAPI server logs a request to `/webhook`.
- Within the model's response time, a new comment appears on the PR containing an LLM-generated review.

- [ ] **Step 7: Record the verification**

Write `docs/superpowers/plans/2026-07-19-stage0-1-verification.md` noting the scratch repo URL, PR number used, and confirmation the comment was posted (screenshot optional, link is enough).

- [ ] **Step 8: Commit the verification record**

```bash
git add docs/superpowers/plans/2026-07-19-stage0-1-verification.md
git commit -m "docs: record Stage 1 vertical-slice E2E verification"
```

Note: pushing to `origin` is done by the user, not automatically, per instruction.

---

## After this plan

Stages 2-7 (Temporal durability, multi-agent LangGraph + aggregator, staleness, circuit breaker, saga, polish) each get their own implementation plan when reached, per `docs/superpowers/specs/2026-07-19-pr-review-bot-design.md`.
