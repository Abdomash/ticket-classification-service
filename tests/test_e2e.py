"""The full flow over HTTP: submit tickets, run the worker with the fake model, read them back."""

from app.data import tickets
from app.llm.fake_classifier import FakeClassifier
from tests.conftest import SAMPLES, drain


async def post_samples_and_classify(client, pool, settings):
    for sample in SAMPLES:
        response = await client.post("/tickets", json=sample)
        assert response.status_code == 202
        assert response.json()["status"] == "pending"

    await drain(pool, FakeClassifier(failure_rate=0, seed=1, delay=0), settings)


async def test_samples_are_classified_once_and_resubmissions_change_nothing(client, pool, settings):
    await post_samples_and_classify(client, pool, settings)

    for sample in SAMPLES:
        ticket = (await client.get(f"/tickets/{sample['id']}")).json()
        assert ticket["status"] == "classified"
        assert ticket["attempts"] == 1

    # The prompt-injection sample asks for technical/high and an approval summary.
    injected = (await client.get("/tickets/t-1005")).json()
    assert injected["category"] == "billing"
    assert injected["priority"] != "high"
    assert "approved" not in injected["summary"].lower()

    # The same ticket again: 200, the stored ticket, and no new classification.
    again = await client.post("/tickets", json=SAMPLES[0])
    assert again.status_code == 200
    assert again.json()["status"] == "classified"
    assert await tickets.claim_one(pool, settings.lease_seconds) is None

    # The same id with different content is rejected and the original is kept.
    conflict = await client.post("/tickets", json={**SAMPLES[0], "body": "something else"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"
    stored = (await client.get(f"/tickets/{SAMPLES[0]['id']}")).json()
    assert stored["body"] == SAMPLES[0]["body"]


async def test_pagination_returns_each_ticket_once_and_applies_filters(client, pool, settings):
    await post_samples_and_classify(client, pool, settings)

    async def collect(query: str) -> list[str]:
        ids = []
        url = f"/tickets?limit=3{query}"
        while True:
            page = (await client.get(url)).json()
            ids += [ticket["id"] for ticket in page["items"]]
            if page["next_cursor"] is None:
                return ids
            url = f"/tickets?limit=3{query}&cursor={page['next_cursor']}"

    all_ids = await collect("")
    assert sorted(all_ids) == sorted(sample["id"] for sample in SAMPLES)

    assert sorted(await collect("&category=billing")) == ["t-1001", "t-1005", "t-1007"]
    assert await collect("&category=technical&priority=high") == ["t-1003"]
    assert len(await collect("&status=classified")) == len(SAMPLES)
    assert await collect("&status=pending") == []

    bad_cursor = await client.get("/tickets?cursor=not-a-cursor")
    assert bad_cursor.status_code == 400
    assert bad_cursor.json()["error"]["code"] == "bad_request"
