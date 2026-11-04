"""Classify the evaluation set with the configured model and compare against the expected labels.

It calls the model directly, without the API, the database or retries.
Each run saves one line per ticket to logs/evaluations/<time>.jsonl.

Usage: python -m scripts.evaluate
"""

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

from app.config import load_settings
from app.llm import build_classifier
from app.llm.base import LLMError

ROOT = Path(__file__).parent.parent
EVALUATION_SET = ROOT / "samples" / "evaluation_set.json"
LOG_DIR = ROOT / "logs" / "evaluations"


async def main() -> None:
    # The settings require a database URL, but this script never connects to the database.
    os.environ.setdefault("DATABASE_URL", "unused")
    settings = load_settings()
    model = "fake" if settings.llm_provider == "fake" else settings.llm_model
    tickets = json.loads(EVALUATION_SET.read_text())

    records = []
    run_start = time.monotonic()
    async with build_classifier(settings) as classifier:
        for ticket in tickets:
            record = {
                "model": model,
                "id": ticket["id"],
                "category": None,
                "priority": None,
            }
            start = time.monotonic()
            try:
                result = await classifier.classify(ticket["subject"], ticket["body"])
                record |= result.classification.model_dump(mode="json")
            except LLMError as error:
                record["error"] = str(error)
            record["seconds"] = round(time.monotonic() - start, 2)
            records.append(record)

            expected = f"{ticket['category']}/{ticket['priority']}"
            got = record.get("error") or f"{record['category']}/{record['priority']}"
            if got != expected:
                print(f"{ticket['id']}: expected {expected}, got {got}")

    seconds = time.monotonic() - run_start
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.jsonl"
    log_path.write_text("".join(json.dumps(r) + "\n" for r in records))

    total = len(tickets)
    pairs = list(zip(tickets, records))
    print(f"\nModel:    {model}")
    print(
        f"Category: {sum(t['category'] == r['category'] for t, r in pairs)}/{total} correct"
    )
    print(
        f"Priority: {sum(t['priority'] == r['priority'] for t, r in pairs)}/{total} correct"
    )
    print(f"Errors:   {sum('error' in r for r in records)}/{total}")
    print(f"Duration: {seconds:.1f}s")
    print(f"Log:      {log_path.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
