from temporalio import activity

from prbot.activity_types import RecordStepInput
from prbot.contracts.validation import validate_agent_output
from prbot.state import db


async def record_step(workflow_id: str, step_seq: int, agent: str, status: str, output: str | None) -> None:
    pool = await db._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pr_review_state_versions (workflow_id, step_seq, agent, status, output)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (workflow_id, step_seq) DO NOTHING
            """,
            workflow_id, step_seq, agent, status, output,
        )


async def get_steps_for_workflow(workflow_id: str) -> list[dict]:
    pool = await db._get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT step_seq, agent, status, output, created_at
            FROM pr_review_state_versions
            WHERE workflow_id = $1
            ORDER BY step_seq
            """,
            workflow_id,
        )
    return [dict(row) for row in rows]


@activity.defn
async def record_state_version_activity(input: RecordStepInput) -> str | None:
    if input.skip_reason is not None:
        status, output = input.skip_reason, None
    else:
        result = validate_agent_output(input.raw_output, reference_text=input.reference_text)
        if result.accepted:
            status, output = "ok", input.raw_output
        else:
            status, output = f"contract_rejected:{result.reason}", None

    await record_step(input.workflow_id, input.step_seq, input.agent, status, output)
    return output
