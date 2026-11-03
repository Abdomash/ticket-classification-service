"""Classify the evaluation set with the configured model and compare against the expected labels.

It calls the model directly, without the API, the database or retries.

Usage: python -m scripts.evaluate
"""

import asyncio
import json
from pathlib import Path

from app.config import load_settings
from app.llm import build_classifier
from app.llm.base import LLMError

EVALUATION_SET = Path(__file__).parent.parent / "samples" / "evaluation_set.json"


async def main() -> None:
    settings = load_settings()
    tickets = json.loads(EVALUATION_SET.read_text())

    category_correct = 0
    priority_correct = 0
    errors = 0
    mistakes = []

    async with build_classifier(settings) as classifier:
        for ticket in tickets:
            try:
                result = await classifier.classify(ticket["subject"], ticket["body"])
            except LLMError as error:
                errors += 1
                mistakes.append(f"{ticket['id']}  error: {error}")
                continue

            category = result.classification.category
            priority = result.classification.priority
            if category == ticket["category"]:
                category_correct += 1
            if priority == ticket["priority"]:
                priority_correct += 1
            if category != ticket["category"] or priority != ticket["priority"]:
                expected = f"{ticket['category']}/{ticket['priority']}"
                mistakes.append(f"{ticket['id']}  expected {expected}, got {category}/{priority}")

    model = settings.llm_model
    if settings.llm_provider == "fake":
        model = "fake"

    total = len(tickets)
    print(f"Model:    {model}")
    print(f"Category: {category_correct}/{total} correct")
    print(f"Priority: {priority_correct}/{total} correct")
    print(f"Errors:   {errors}/{total}")
    print()
    for line in mistakes:
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
