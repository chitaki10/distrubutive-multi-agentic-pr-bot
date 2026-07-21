import asyncpg

from prbot.config import get_settings

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(get_settings().postgres_dsn)
    return _pool


async def init_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pr_reviews (
              id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
              repo text NOT NULL,
              pr_number int NOT NULL,
              head_sha text NOT NULL,
              status text NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now(),
              UNIQUE(repo, pr_number, head_sha)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pr_review_state_versions (
              id BIGSERIAL PRIMARY KEY,
              workflow_id text NOT NULL,
              step_seq int NOT NULL,
              agent text NOT NULL,
              status text NOT NULL,
              output text,
              created_at timestamptz NOT NULL DEFAULT now(),
              UNIQUE(workflow_id, step_seq)
            )
            """
        )


async def set_review_status(repo: str, pr_number: int, head_sha: str, status: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pr_reviews (repo, pr_number, head_sha, status)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (repo, pr_number, head_sha)
            DO UPDATE SET status = EXCLUDED.status, updated_at = now()
            """,
            repo, pr_number, head_sha, status,
        )
