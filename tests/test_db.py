import pytest

from prbot import db


@pytest.fixture(autouse=True)
async def reset_pool():
    db._pool = None
    yield
    if db._pool is not None:
        await db._pool.close()
        db._pool = None


async def test_init_db_creates_table():
    await db.init_db()
    pool = await db._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT to_regclass('public.pr_reviews') AS exists")
    assert row["exists"] == "pr_reviews"


async def test_set_review_status_inserts_and_updates():
    await db.init_db()
    await db.set_review_status("test-owner/test-repo", 9001, "test-sha-1", "running")

    pool = await db._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM pr_reviews WHERE repo = $1 AND pr_number = $2 AND head_sha = $3",
            "test-owner/test-repo", 9001, "test-sha-1",
        )
    assert row["status"] == "running"

    await db.set_review_status("test-owner/test-repo", 9001, "test-sha-1", "complete")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM pr_reviews WHERE repo = $1 AND pr_number = $2 AND head_sha = $3",
            "test-owner/test-repo", 9001, "test-sha-1",
        )
    assert row["status"] == "complete"
