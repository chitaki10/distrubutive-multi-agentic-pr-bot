from prbot import review
from prbot.config import Settings
from prbot.api.events import PullRequestEvent


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
