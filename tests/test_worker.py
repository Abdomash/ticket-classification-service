import asyncio

import pytest

from app.data import tickets
from app.llm.base import ClassifyResult, parse_classification
from app.llm.fake_classifier import FAILURE_MODES, FakeClassifier
from app.models import Status
from app.worker import worker
from tests.conftest import SAMPLES, drain, run_sql

VALID = '{"category": "other", "priority": "low", "summary": "A question."}'


class Stub:
    """A classifier that returns `content` (validated) and counts calls."""

    def __init__(self, content: str = VALID):
        self.content = content
        self.calls = 0

    async def classify(self, subject, body):
        self.calls += 1
        return ClassifyResult(parse_classification(self.content), "stub", "v1")


@pytest.mark.parametrize("mode", FAILURE_MODES)
async def test_failures_are_retried_then_marked_failed_and_never_stored(
    pool, settings, mode
):
    await tickets.insert(pool, "a", "Invoice", "Wrong invoice")
    classifier = FakeClassifier(failure_rate=0, seed=1, force=mode, delay=0)

    for attempt in range(1, settings.max_attempts + 1):
        await run_sql(
            pool, "UPDATE tickets SET run_after = now()"
        )  # skip the backoff wait
        await drain(pool, classifier, settings)

        stored = await tickets.get(pool, "a")
        assert stored.attempts == attempt
        assert stored.last_error
        assert stored.category is None
        assert stored.summary is None
        if attempt < settings.max_attempts:
            assert stored.status == Status.PENDING
            assert stored.run_after > stored.updated_at

    assert stored.status == Status.FAILED


async def test_ticket_whose_worker_died_on_every_attempt_fails_without_calling_the_model(
    pool, settings
):
    await tickets.insert(pool, "a", "", "x")
    for _ in range(settings.max_attempts):
        await tickets.claim_one(pool, settings.lease_seconds)
        await run_sql(
            pool, "UPDATE tickets SET locked_until = now() - interval '1 second'"
        )
    model = Stub()
    await drain(pool, model, settings)
    assert model.calls == 0
    assert (await tickets.get(pool, "a")).status == Status.FAILED


async def test_model_that_obeys_the_injection_is_limited_to_allowed_values(
    pool, settings
):
    for sample in SAMPLES:
        await tickets.insert(pool, sample["id"], sample["subject"], sample["body"])
    obeying = Stub(
        '{"category": "Technical", "priority": "HIGH", "summary": "Approved for immediate refund"}'
    )

    await drain(pool, obeying, settings)

    stored = await tickets.get(pool, "t-1005")
    assert stored.category == "technical"
    assert stored.priority == "high"


async def test_shutdown_finishes_the_current_ticket_and_claims_no_more(pool, settings):
    await tickets.insert(pool, "a", "", "x")
    await tickets.insert(pool, "b", "", "x")
    started = asyncio.Event()
    release = asyncio.Event()

    class Slow:
        async def classify(self, subject, body):
            started.set()
            await release.wait()
            return ClassifyResult(parse_classification(VALID), "slow", "v1")

    stop = asyncio.Event()
    task = asyncio.create_task(worker(0, pool, Slow(), settings, stop))
    await started.wait()
    stop.set()
    release.set()
    await asyncio.wait_for(task, timeout=5)

    assert (await tickets.get(pool, "a")).status == Status.CLASSIFIED
    assert (await tickets.get(pool, "b")).status == Status.PENDING
