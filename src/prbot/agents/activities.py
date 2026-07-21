import os
from pathlib import Path

from temporalio import activity

from prbot.state import db
from prbot.integrations import github_client, llm_client
from prbot.activity_types import AggregateInput, DeleteCommentInput, FetchDiffInput, PostCommentInput, ReviewInput, SetStatusInput, StalenessCheckInput
from prbot.config import get_settings


def _generate_jwt() -> str:
    settings = get_settings()
    private_key = Path(settings.github_private_key_path).read_text()
    return github_client.generate_app_jwt(settings.github_app_id, private_key)


@activity.defn
async def fetch_diff_activity(input: FetchDiffInput) -> str:
    app_jwt = _generate_jwt()
    token = await github_client.get_installation_token(app_jwt, input.installation_id)
    return await github_client.fetch_pr_diff(token, input.owner, input.repo, input.pr_number)


@activity.defn
async def review_activity(input: ReviewInput) -> str:
    settings = get_settings()
    return await llm_client.review_diff(input.diff_text, settings.ollama_base_url, settings.ollama_model)


@activity.defn
async def post_comment_activity(input: PostCommentInput) -> int:
    app_jwt = _generate_jwt()
    token = await github_client.get_installation_token(app_jwt, input.installation_id)
    return await github_client.post_pr_comment(token, input.owner, input.repo, input.pr_number, input.body)


@activity.defn
async def set_review_status_activity(input: SetStatusInput) -> None:
    await db.set_review_status(input.repo, input.pr_number, input.head_sha, input.status)


@activity.defn
async def aggregate_activity(input: AggregateInput) -> str:
    sections = [
        ("Security", input.security_result),
        ("Style", input.style_result),
        ("Test Coverage", input.test_coverage_result),
    ]
    parts = []
    for title, result in sections:
        if result is None:
            parts.append(f"### {title}\n\n_{title} check skipped._")
        else:
            parts.append(f"### {title}\n\n{result}")
    return "\n\n".join(parts)


@activity.defn
async def check_staleness_activity(input: StalenessCheckInput) -> bool:
    app_jwt = _generate_jwt()
    token = await github_client.get_installation_token(app_jwt, input.installation_id)
    current_head_sha = await github_client.get_pr_head_sha(token, input.owner, input.repo, input.pr_number)
    return current_head_sha != input.head_sha


@activity.defn
async def check_demo_failure_injection_activity() -> bool:
    return os.environ.get("PRBOT_DEMO_FORCE_FAILURE_AFTER_POST", "").lower() == "true"


@activity.defn
async def delete_comment_activity(input: DeleteCommentInput) -> None:
    app_jwt = _generate_jwt()
    token = await github_client.get_installation_token(app_jwt, input.installation_id)
    await github_client.delete_pr_comment(token, input.owner, input.repo, input.comment_id)
