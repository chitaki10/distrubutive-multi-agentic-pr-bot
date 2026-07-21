import logging

from prbot.integrations import github_client, llm_client
from prbot.config import Settings
from prbot.api.events import PullRequestEvent

logger = logging.getLogger(__name__)


async def run_stage1_review(event: PullRequestEvent, settings: Settings, app_jwt: str) -> int:
    pr_id = f"{event.owner}/{event.repo}#{event.pr_number}"
    logger.info(f"Starting review pipeline for {pr_id}")

    token = await github_client.get_installation_token(app_jwt, event.installation_id)
    logger.info(f"Exchanged app JWT for installation token ({pr_id})")

    diff = await github_client.fetch_pr_diff(token, event.owner, event.repo, event.pr_number)
    logger.info(f"Fetched PR diff ({pr_id})")

    review_body = await llm_client.review_diff(diff, settings.ollama_base_url, settings.ollama_model)
    logger.info(f"Generated review via LLM ({pr_id})")

    comment_id = await github_client.post_pr_comment(
        token, event.owner, event.repo, event.pr_number, review_body
    )
    logger.info(f"Posted review comment ({pr_id}), comment_id={comment_id}")

    return comment_id
