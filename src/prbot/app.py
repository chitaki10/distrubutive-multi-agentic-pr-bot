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
