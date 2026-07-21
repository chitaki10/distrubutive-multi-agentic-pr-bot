from prbot.contracts.schemas import MAX_OUTPUT_LEN, MIN_OUTPUT_LEN, ContractResult


def validate_agent_output(output: str, *, reference_text: str | None = None) -> ContractResult:
    if not output or not output.strip():
        return ContractResult(accepted=False, reason="empty_output")

    if len(output) < MIN_OUTPUT_LEN:
        return ContractResult(accepted=False, reason="too_short")

    if len(output) > MAX_OUTPUT_LEN:
        return ContractResult(accepted=False, reason="too_long")

    if reference_text is not None and output.strip() == reference_text.strip():
        return ContractResult(accepted=False, reason="echoed_input")

    if output.lstrip().startswith("Traceback") or output.lstrip().startswith("Error:"):
        return ContractResult(accepted=False, reason="looks_like_error")

    return ContractResult(accepted=True)
