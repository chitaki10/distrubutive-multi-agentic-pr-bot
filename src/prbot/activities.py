from pathlib import Path

from temporalio import activity

from prbot import db, github_client, llm_client
from prbot.activity_types import AggregateInput, FetchDiffInput, PostCommentInput, ReviewInput, SetStatusInput
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


SECURITY_SYSTEM_PROMPT = (
    "You are a security-focused code reviewer. Look at this GitHub pull "
    "request diff and flag any injection risks, hardcoded secrets, or unsafe "
    "patterns. Keep it under 150 words. If there are no concerns, say so briefly."
)

STYLE_SYSTEM_PROMPT = (
    "You are a style and convention reviewer. Look at this GitHub pull request "
    "diff and flag convention violations, overly complex code, or naming "
    "issues. Keep it under 150 words. If there are no concerns, say so briefly."
)

TEST_COVERAGE_SYSTEM_PROMPT = (
    "You are a test-coverage reviewer. Look at this GitHub pull request diff "
    "and flag any new logic that appears to lack test coverage. Keep it "
    "under 150 words. If there are no concerns, say so briefly."
)


@activity.defn
async def security_review_activity(input: ReviewInput) -> str:
    settings = get_settings()
    return await llm_client.review_diff_with_prompt(
        input.diff_text, settings.ollama_base_url, settings.ollama_model, SECURITY_SYSTEM_PROMPT
    )


@activity.defn
async def style_review_activity(input: ReviewInput) -> str:
    settings = get_settings()
    return await llm_client.review_diff_with_prompt(
        input.diff_text, settings.ollama_base_url, settings.ollama_model, STYLE_SYSTEM_PROMPT
    )


@activity.defn
async def test_coverage_review_activity(input: ReviewInput) -> str:
    settings = get_settings()
    return await llm_client.review_diff_with_prompt(
        input.diff_text, settings.ollama_base_url, settings.ollama_model, TEST_COVERAGE_SYSTEM_PROMPT
    )


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
