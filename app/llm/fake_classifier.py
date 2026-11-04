"""A stand-in for a real model, used by default and in tests.

It picks the category and the priority whose keywords appear most often, and at a seeded random
rate fails the way real models do. Its answers are raw JSON text and go through the same
validation as a real model's.
"""

import asyncio
import json
import random
from typing import Self

from app.llm.base import ClassifyResult, LLMError, parse_classification

FAILURE_MODES = (
    "malformed_json",
    "bad_category",
    "missing_field",
    "timeout",
    "rate_limit",
)

CATEGORY_KEYWORDS = {
    "billing": (
        "invoice",
        "charge",
        "refund",
        "overcharged",
        "subscription",
        "payment",
    ),
    "technical": (
        "error",
        "api",
        "http",
        "export",
        "upload",
        "timeout",
        "broken",
        "zip",
    ),
    "account": (
        "log in",
        "login",
        "password",
        "credentials",
        "email address",
        "account",
    ),
}

PRIORITY_KEYWORDS = {
    "high": ("production", "blocking", "outage"),
    "medium": (
        "refund",
        "charge",
        "cannot",
        "can't",
        "error",
        "broken",
        "empty",
        "before the",
    ),
    "low": (
        "question",
        "nice to have",
        "not urgent",
        "feature request",
        "what do i need",
    ),
}


class FakeClassifier:
    def __init__(self, failure_rate: float, seed: int, delay: float = 0.2):
        """`delay` is the maximum simulated latency."""
        self._rng = random.Random(seed)
        self._failure_rate = failure_rate
        self._delay = delay

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    async def classify(self, subject: str, body: str) -> ClassifyResult:
        await asyncio.sleep(self._rng.uniform(0, self._delay))

        mode = None
        if self._rng.random() < self._failure_rate:
            mode = self._rng.choice(FAILURE_MODES)

        if mode == "timeout":
            raise LLMError("APITimeoutError")
        if mode == "rate_limit":
            raise LLMError("RateLimitError", retry_after=2)

        text = f"{subject}\n{body}".lower()
        category = most_mentioned(text, CATEGORY_KEYWORDS, default="other")
        answer = {
            "category": category,
            "priority": most_mentioned(text, PRIORITY_KEYWORDS, default="low"),
            "summary": f"{category.capitalize()} request: {subject.strip()[:150] or 'no subject'}.",
        }
        if mode == "bad_category":
            answer["category"] = "sales"
        if mode == "missing_field":
            del answer["summary"]

        content = json.dumps(answer)
        if mode == "malformed_json":
            content = content[:-8]

        classification = parse_classification(content)
        return ClassifyResult(classification, "fake", "fake-v1")


def most_mentioned(
    text: str, keywords: dict[str, tuple[str, ...]], default: str
) -> str:
    """Return the label whose keywords appear most often in `text`, or `default` if none appear."""
    best_label = default
    best_count = 0
    for label, words in keywords.items():
        count = sum(text.count(word) for word in words)
        if count > best_count:
            best_label, best_count = label, count
    return best_label
