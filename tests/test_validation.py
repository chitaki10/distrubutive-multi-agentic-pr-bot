from prbot.contracts.validation import validate_agent_output


def test_rejects_empty_output():
    result = validate_agent_output("   ")
    assert result.accepted is False
    assert result.reason == "empty_output"


def test_rejects_too_short_output():
    result = validate_agent_output("too short")
    assert result.accepted is False
    assert result.reason == "too_short"


def test_rejects_too_long_output():
    result = validate_agent_output("x" * 20001)
    assert result.accepted is False
    assert result.reason == "too_long"


def test_rejects_echo_of_reference_text():
    diff = "diff --git a/x.py b/x.py\n+print(1)\n" * 2
    result = validate_agent_output(diff, reference_text=diff)
    assert result.accepted is False
    assert result.reason == "echoed_input"


def test_rejects_output_that_looks_like_an_error():
    result = validate_agent_output("Traceback (most recent call last):\n  File something broke here badly")
    assert result.accepted is False
    assert result.reason == "looks_like_error"


def test_accepts_reasonable_review_text():
    result = validate_agent_output("No security concerns found in this diff. Looks good to merge.")
    assert result.accepted is True
    assert result.reason is None


def test_accepts_long_output_when_max_len_disabled():
    huge_diff = "diff --git a/x.py b/x.py\n+print(1)\n" * 2000
    result = validate_agent_output(huge_diff, max_len=None)
    assert result.accepted is True
