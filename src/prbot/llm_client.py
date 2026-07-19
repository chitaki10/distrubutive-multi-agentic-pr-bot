import httpx

REVIEW_SYSTEM_PROMPT = (
    "You are a senior software engineer reviewing a GitHub pull request diff. "
    "Give a concise, actionable code review in markdown covering security, "
    "style, and missing test coverage. Keep it under 300 words."
)


async def review_diff(diff_text: str, base_url: str, model: str) -> str:
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": diff_text},
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
