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
