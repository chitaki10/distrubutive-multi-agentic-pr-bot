"""Print the immutable per-agent-step version history for a PR review workflow run.

Usage: .venv/Scripts/python scripts/replay_state.py "<owner>/<repo>#<pr_number>@<head_sha>"
"""
import asyncio
import sys

sys.path.insert(0, "src")

from prbot.state import db, versioned_log


async def main(workflow_id: str) -> None:
    await db.init_db()
    steps = await versioned_log.get_steps_for_workflow(workflow_id)

    if not steps:
        print(f"No recorded steps for workflow_id={workflow_id!r}")
        return

    print(f"Version history for {workflow_id}:")
    for step in steps:
        output_preview = (step["output"] or "")[:80]
        print(f"  step {step['step_seq']} | {step['agent']:20s} | {step['status']:30s} | {output_preview}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: replay_state.py <workflow_id>")
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1]))
