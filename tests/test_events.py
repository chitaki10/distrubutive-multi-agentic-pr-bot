import hashlib
import hmac

from prbot.events import PullRequestEvent, parse_pull_request_event, verify_signature


def test_verify_signature_accepts_valid_signature():
    secret = "testsecret"
    body = b'{"action": "opened"}'
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_signature(body, signature, secret) is True


def test_verify_signature_rejects_invalid_signature():
    assert verify_signature(b'{"action": "opened"}', "sha256=deadbeef", "testsecret") is False


def test_verify_signature_rejects_missing_header():
    assert verify_signature(b"{}", None, "testsecret") is False


def test_parse_pull_request_event_extracts_fields_on_opened():
    payload = {
        "action": "opened",
        "pull_request": {"number": 7, "head": {"sha": "abc123"}},
        "repository": {"name": "demo", "owner": {"login": "chitaki10"}},
        "installation": {"id": 55},
    }

    event = parse_pull_request_event(payload)

    assert event == PullRequestEvent(
        owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55"
    )


def test_parse_pull_request_event_extracts_fields_on_synchronize():
    payload = {
        "action": "synchronize",
        "pull_request": {"number": 7, "head": {"sha": "abc123"}},
        "repository": {"name": "demo", "owner": {"login": "chitaki10"}},
        "installation": {"id": 55},
    }

    event = parse_pull_request_event(payload)

    assert event == PullRequestEvent(
        owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55"
    )


def test_parse_pull_request_event_returns_none_for_other_actions():
    assert parse_pull_request_event({"action": "closed"}) is None
