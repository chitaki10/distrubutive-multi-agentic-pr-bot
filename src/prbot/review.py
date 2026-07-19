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
