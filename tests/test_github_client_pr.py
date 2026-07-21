import httpx
import respx

from prbot.integrations.github_client import delete_pr_comment, fetch_pr_diff, get_pr_head_sha, post_pr_comment


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


@respx.mock
async def test_get_pr_head_sha_returns_current_head_sha():
    respx.get("https://api.github.com/repos/chitaki10/demo/pulls/7").mock(
        return_value=httpx.Response(200, json={"head": {"sha": "newsha123"}})
    )

    result = await get_pr_head_sha("ghs_token", "chitaki10", "demo", 7)

    assert result == "newsha123"


@respx.mock
async def test_delete_pr_comment_sends_delete_request():
    route = respx.delete("https://api.github.com/repos/chitaki10/demo/issues/comments/42").mock(
        return_value=httpx.Response(204)
    )

    await delete_pr_comment("ghs_token", "chitaki10", "demo", 42)

    assert route.called
