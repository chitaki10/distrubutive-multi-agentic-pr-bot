import httpx
import respx

from prbot.llm_client import review_diff, review_diff_with_prompt


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


@respx.mock
async def test_review_diff_with_prompt_sends_custom_system_prompt():
    route = respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Custom agent review."}}]},
        )
    )

    result = await review_diff_with_prompt(
        "diff --git a/x.py b/x.py\n+print(1)",
        "http://localhost:11434",
        "qwen2.5-coder:3b",
        "You are a security-focused reviewer.",
    )

    assert result == "Custom agent review."
    sent_body = route.calls.last.request.content
    assert b"You are a security-focused reviewer." in sent_body
