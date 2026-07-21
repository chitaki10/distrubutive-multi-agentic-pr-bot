from dataclasses import dataclass

MIN_OUTPUT_LEN = 20
MAX_OUTPUT_LEN = 20000


@dataclass
class ContractResult:
    accepted: bool
    reason: str | None = None
