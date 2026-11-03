"""The queue is SQL, so these tests run against a real Postgres."""

import asyncio

import pytest

from app.data import tickets
from app.models import Classification, Status
from tests.conftest import run_sql

LEASE = 60


async def test_claims_skip_locked_rows_and_never_overlap(pool):
    for id in ("a", "b", "c"):
        await tickets.insert(pool, id, "", "x")

    # Another transaction holds a lock on "a". The claim must skip it, not wait for it.
    async with pool.connection() as other, other.transaction():
        await other.execute("SELECT * FROM tickets WHERE id = 'a' FOR UPDATE")
        claimed = await asyncio.wait_for(tickets.claim_one(pool, LEASE), timeout=2)
        assert claimed.id == "b"

    # Two claims at the same time get different tickets.
    first, second = await asyncio.gather(tickets.claim_one(pool, LEASE), tickets.claim_one(pool, LEASE))
    assert {first.id, second.id} == {"a", "c"}
    assert await tickets.claim_one(pool, LEASE) is None


async def test_expired_lease_is_reclaimed_and_the_old_worker_cannot_write(pool):
    await tickets.insert(pool, "a", "", "x")
    old = await tickets.claim_one(pool, LEASE)
    assert old.status == Status.PROCESSING
    assert await tickets.claim_one(pool, LEASE) is None  # the lease is still valid

    await run_sql(pool, "UPDATE tickets SET locked_until = now() - interval '1 second'")
    new = await tickets.claim_one(pool, LEASE)
    assert new.id == "a"
    assert new.attempts == old.attempts + 1

    result = Classification(category="billing", priority="low", summary="Asks about an invoice.")
    with pytest.raises(tickets.LeaseLost):
        await tickets.complete(pool, "a", old.attempts, result, "model", "v1")
    with pytest.raises(tickets.LeaseLost):
        await tickets.fail(pool, "a", old.attempts, "too late")

    await tickets.complete(pool, "a", new.attempts, result, "model", "v1")
    stored = await tickets.get(pool, "a")
    assert stored.status == Status.CLASSIFIED
    assert stored.category == "billing"
    assert stored.locked_until is None
