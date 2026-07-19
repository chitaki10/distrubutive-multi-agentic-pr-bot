import time

import httpx
import jwt


def generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


async def get_installation_token(
    app_jwt: str, installation_id: str, base_url: str = "https://api.github.com"
) -> str:
    url = f"{base_url}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        return response.json()["token"]
