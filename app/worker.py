"""The classification worker. Run it with `python -m app.worker`.

One process runs WORKER_CONCURRENCY workers. Each worker claims a ticket, classifies it, and
stores the result, a retry, or a failure. On SIGTERM or SIGINT, each worker finishes its current
ticket and exits. If the process is killed instead, the ticket's lease expires and another worker
claims it again. An unexpected error while processing a ticket is logged and does not stop the
process. An error while claiming, such as the database being down, does: Compose restarts it.
"""

import asyncio
import logging
import signal
from functools import partial

from psycopg_pool import AsyncConnectionPool

from app.config import Settings, load_settings
from app.data import tickets
from app.data.pool import open_pool
from app.llm import build_classifier
from app.llm.base import Classifier, LLMError
from app.logging import log, setup_logging
from app.models import Ticket

# How long a failed ticket waits before its next attempt.
RETRY_DELAY_SECONDS = 30


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    stop = stop_on_signals()

    async with (
        open_pool(settings.database_url, max_size=settings.worker_concurrency) as pool,
        build_classifier(settings) as classifier,
        asyncio.TaskGroup() as group,
    ):
        for n in range(settings.worker_concurrency):
            group.create_task(worker(n, pool, classifier, settings, stop))

    log("worker_process.stopped")


async def worker(
    n: int,
    pool: AsyncConnectionPool,
    classifier: Classifier,
    settings: Settings,
    stop: asyncio.Event,
):
    """Claim and process one ticket at a time until `stop` is set."""
    log("worker.started", worker=n)

    while not stop.is_set():
        ticket = await tickets.claim_one(pool, settings.lease_seconds)
        if ticket is None:
            await sleep_unless_stopped(settings.poll_seconds, stop)
            continue

        try:
            await process(n, ticket, pool, classifier, settings)
        except tickets.LeaseLost:
            log(
                "ticket.lease_lost",
                logging.WARNING,
                ticket_id=ticket.id,
                attempt=ticket.attempts,
                worker=n,
            )
        except Exception as error:  # noqa: BLE001
            # Stops one ticket's error from crashing the other workers in this process.
            log(
                "ticket.error",
                logging.ERROR,
                ticket_id=ticket.id,
                attempt=ticket.attempts,
                worker=n,
                error=type(error).__name__,
            )

    log("worker.stopped", worker=n)


def stop_on_signals() -> asyncio.Event:
    """Return an event that is set when the process receives SIGTERM or SIGINT."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT, stop.set)
    return stop


async def sleep_unless_stopped(seconds: float, stop: asyncio.Event) -> None:
    try:
        async with asyncio.timeout(seconds):
            await stop.wait()
    except TimeoutError:
        pass


async def process(
    n: int,
    ticket: Ticket,
    pool: AsyncConnectionPool,
    classifier: Classifier,
    settings: Settings,
):
    """Classify one claimed ticket, then store the result, schedule a retry, or mark it failed.

    Raises `tickets.LeaseLost` if another worker took the ticket in the meantime.
    """
    ticket_log = partial(log, ticket_id=ticket.id, attempt=ticket.attempts, worker=n)
    ticket_log("ticket.claimed")

    # The lease ran out on the last attempt, for example because the worker was killed each time.
    if ticket.attempts > settings.max_attempts:
        await tickets.fail(
            pool, ticket.id, ticket.attempts, "lease expired on the last attempt"
        )
        ticket_log(
            "ticket.failed", logging.ERROR, error="lease expired on the last attempt"
        )
        return

    try:
        result = await classifier.classify(ticket.subject, ticket.body)
    except LLMError as error:
        reason = str(error)
        if ticket.attempts < settings.max_attempts:
            await tickets.schedule_retry(
                pool, ticket.id, ticket.attempts, reason, RETRY_DELAY_SECONDS
            )
            ticket_log(
                "ticket.retry_scheduled",
                error=reason,
                delay_seconds=RETRY_DELAY_SECONDS,
            )
        else:
            await tickets.fail(pool, ticket.id, ticket.attempts, reason)
            ticket_log("ticket.failed", logging.ERROR, error=reason)
        return

    await tickets.complete(
        pool,
        ticket.id,
        ticket.attempts,
        result.classification,
        result.model,
        result.prompt_version,
    )
    ticket_log("ticket.classified")


if __name__ == "__main__":
    asyncio.run(main())
