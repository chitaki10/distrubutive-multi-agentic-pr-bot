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
