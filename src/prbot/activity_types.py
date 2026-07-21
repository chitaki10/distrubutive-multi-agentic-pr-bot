from dataclasses import dataclass


@dataclass
class FetchDiffInput:
    installation_id: str
    owner: str
    repo: str
    pr_number: int


@dataclass
class ReviewInput:
    diff_text: str


@dataclass
class PostCommentInput:
    installation_id: str
    owner: str
    repo: str
    pr_number: int
    body: str


@dataclass
class SetStatusInput:
    repo: str
    pr_number: int
    head_sha: str
    status: str


@dataclass
class AggregateInput:
    security_result: str | None
    style_result: str | None
    test_coverage_result: str | None


@dataclass
class StalenessCheckInput:
    installation_id: str
    owner: str
    repo: str
    pr_number: int
    head_sha: str


@dataclass
class DeleteCommentInput:
    installation_id: str
    owner: str
    repo: str
    comment_id: int


@dataclass
class RecordStepInput:
    workflow_id: str
    step_seq: int
    agent: str
    raw_output: str | None
    skip_reason: str | None
    reference_text: str | None
