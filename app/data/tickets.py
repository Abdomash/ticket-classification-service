"""Every SQL statement in the service. Times always come from Postgres `now()`.

Values are passed to psycopg as parameters, which sends them separately from the SQL text.

How the queue works: `claim_one` locks a ticket for a while (its lease) and adds 1 to
`attempts`. That number is the worker's token. `complete`, `schedule_retry` and `fail` only
update the row if the token still matches. If the lease expired and another worker claimed the
ticket, the token has changed, nothing is written, and `LeaseLost` is raised.
"""

from datetime import datetime
from typing import Literal

from psycopg_pool import AsyncConnectionPool

from app.models import Category, Classification, Priority, Status, Ticket

type InsertOutcome = Literal["ingested", "duplicate", "conflict"]


class LeaseLost(Exception):
    """The ticket was claimed by another worker after this worker's lease expired."""


async def _fetch_one(
    pool: AsyncConnectionPool, sql: str, params: dict
) -> Ticket | None:
    async with pool.connection() as conn:
        cursor = await conn.execute(sql, params)
        row = await cursor.fetchone()
    if row is None:
        return None
    return Ticket(**row)


async def _update_claimed(pool: AsyncConnectionPool, sql: str, params: dict) -> None:
    """Run an update that must match exactly the ticket this worker claimed."""
    async with pool.connection() as conn:
        cursor = await conn.execute(sql, params)
    if cursor.rowcount != 1:
        raise LeaseLost(params["id"])


async def insert(
    pool: AsyncConnectionPool, id: str, subject: str, body: str
) -> tuple[Ticket, InsertOutcome]:
    """Add a pending ticket. If the id exists, leave it alone and say whether the content matches."""
    created = await _fetch_one(
        pool,
        """
        INSERT INTO tickets (id, subject, body)
        VALUES (%(id)s, %(subject)s, %(body)s)
        ON CONFLICT (id) DO NOTHING
        RETURNING *
        """,
        {"id": id, "subject": subject, "body": body},
    )
    if created is not None:
        return created, "ingested"

    existing = await get(pool, id)
    if existing.subject == subject and existing.body == body:
        return existing, "duplicate"
    return existing, "conflict"


async def get(pool: AsyncConnectionPool, id: str) -> Ticket | None:
    return await _fetch_one(pool, "SELECT * FROM tickets WHERE id = %(id)s", {"id": id})


async def list_page(
    pool: AsyncConnectionPool,
    status: Status | None,
    category: Category | None,
    priority: Priority | None,
    limit: int,
    after: tuple[datetime, str] | None,
) -> list[Ticket]:
    """Return tickets newest first. `after` is the (created_at, id) of the last ticket of the previous page."""
    after_created_at = None
    after_id = None
    if after is not None:
        after_created_at, after_id = after

    async with pool.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM tickets
            WHERE (%(status)s::text IS NULL OR status = %(status)s)
              AND (%(category)s::text IS NULL OR category = %(category)s)
              AND (%(priority)s::text IS NULL OR priority = %(priority)s)
              AND (%(after_created_at)s::timestamptz IS NULL
                   OR (created_at, id) < (%(after_created_at)s, %(after_id)s))
            ORDER BY created_at DESC, id DESC
            LIMIT %(limit)s
            """,
            {
                "status": status,
                "category": category,
                "priority": priority,
                "after_created_at": after_created_at,
                "after_id": after_id,
                "limit": limit,
            },
        )
        rows = await cursor.fetchall()
    return [Ticket(**row) for row in rows]


async def claim_one(pool: AsyncConnectionPool, lease_seconds: float) -> Ticket | None:
    """Claim the oldest due ticket, or return None if there is none.

    A ticket is due if it is pending and its `run_after` has passed, or if it is processing
    and its lease has expired. SKIP LOCKED makes concurrent claims pick different tickets.
    """
    return await _fetch_one(
        pool,
        """
        UPDATE tickets
        SET status = 'processing',
            attempts = attempts + 1,
            locked_until = now() + make_interval(secs => %(lease)s),
            updated_at = now()
        WHERE id = (
            SELECT id FROM tickets
            WHERE (status = 'pending' AND run_after <= now())
               OR (status = 'processing' AND locked_until < now())
            ORDER BY run_after
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
        """,
        {"lease": lease_seconds},
    )


async def complete(
    pool: AsyncConnectionPool,
    id: str,
    token: int,
    result: Classification,
    model: str,
    prompt_version: str,
) -> None:
    """Store a validated classification and mark the ticket classified."""
    await _update_claimed(
        pool,
        """
        UPDATE tickets
        SET status = 'classified',
            category = %(category)s,
            priority = %(priority)s,
            summary = %(summary)s,
            model = %(model)s,
            prompt_version = %(prompt_version)s,
            last_error = NULL,
            locked_until = NULL,
            updated_at = now()
        WHERE id = %(id)s AND attempts = %(token)s AND status = 'processing'
        """,
        {
            "id": id,
            "token": token,
            "category": result.category.value,
            "priority": result.priority.value,
            "summary": result.summary,
            "model": model,
            "prompt_version": prompt_version,
        },
    )


async def schedule_retry(
    pool: AsyncConnectionPool, id: str, token: int, error: str, delay_seconds: float
) -> None:
    """Put the ticket back to pending, due again after `delay_seconds`."""
    await _update_claimed(
        pool,
        """
        UPDATE tickets
        SET status = 'pending',
            run_after = now() + make_interval(secs => %(delay)s),
            last_error = %(error)s,
            locked_until = NULL,
            updated_at = now()
        WHERE id = %(id)s AND attempts = %(token)s AND status = 'processing'
        """,
        {"id": id, "token": token, "error": error, "delay": delay_seconds},
    )


async def fail(pool: AsyncConnectionPool, id: str, token: int, error: str) -> None:
    """Mark the ticket failed. It will not be claimed again."""
    await _update_claimed(
        pool,
        """
        UPDATE tickets
        SET status = 'failed',
            last_error = %(error)s,
            locked_until = NULL,
            updated_at = now()
        WHERE id = %(id)s AND attempts = %(token)s AND status = 'processing'
        """,
        {"id": id, "token": token, "error": error},
    )
