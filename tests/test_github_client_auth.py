import httpx
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from prbot.github_client import generate_app_jwt, get_installation_token


def _generate_test_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def test_generate_app_jwt_has_correct_claims():
    private_pem, public_pem = _generate_test_keypair()

    token = generate_app_jwt("123456", private_pem)

    decoded = jwt.decode(token, public_pem, algorithms=["RS256"], issuer="123456")
    assert decoded["iss"] == "123456"
    assert decoded["exp"] > decoded["iat"]


@respx.mock
async def test_get_installation_token_returns_token():
    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(return_value=httpx.Response(201, json={"token": "ghs_abc123"}))

    token = await get_installation_token("fake.jwt.token", "999")

    assert token == "ghs_abc123"
    assert route.called
    sent_headers = route.calls.last.request.headers
    assert sent_headers["authorization"] == "Bearer fake.jwt.token"
