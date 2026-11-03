"""A stand-in for a real model, used by default and in tests.

It picks labels from keywords and, at a seeded random rate, fails the way real models do.
Its answers are raw JSON text and go through the same validation as a real model's.
"""

import asyncio
import json
import random
from typing import Self

from app.llm.base import ClassifyResult, LLMError, parse_classification

FAILURE_MODES = ("malformed_json", "bad_category", "missing_field", "timeout", "rate_limit")

_CATEGORY_KEYWORDS = {
    "billing": ("invoice", "charge", "refund", "overcharged", "subscription", "payment"),
    "technical": ("error", "api", "http", "export", "upload", "timeout", "broken", "zip"),
    "account": ("log in", "login", "password", "credentials", "email address", "account"),
}
_HIGH_PRIORITY_WORDS = ("production", "blocking", "outage")
_MEDIUM_PRIORITY_WORDS = ("refund", "charge", "cannot", "can't", "error", "broken", "empty", "before the")


class FakeClassifier:
    def __init__(self, failure_rate: float, seed: int, force: str | None = None, delay: float = 0.2):
        """`force` makes every call fail with that mode. `delay` is the maximum simulated latency."""
        self._rng = random.Random(seed)
        self._failure_rate = failure_rate
        self._force = force
        self._delay = delay

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    async def classify(self, subject: str, body: str) -> ClassifyResult:
        await asyncio.sleep(self._rng.uniform(0, self._delay))

        mode = self._force
        if mode is None and self._rng.random() < self._failure_rate:
            mode = self._rng.choice(FAILURE_MODES)

        if mode == "timeout":
            raise LLMError("APITimeoutError")
        if mode == "rate_limit":
            raise LLMError("RateLimitError", retry_after=2)

        content = _respond(subject, body, mode)
        classification = parse_classification(content)
        return ClassifyResult(classification, "fake", "fake-v1")


def _respond(subject: str, body: str, mode: str | None) -> str:
    """Return the raw text a model would send: a keyword-based answer, broken if `mode` says so."""
    text = f"{subject}\n{body}".lower()
    category = _category(text)

    topic = " ".join(subject.split())
    if not topic:
        topic = "no subject"

    answer = {
        "category": category,
        "priority": _priority(text),
        "summary": f"{category.capitalize()} request: {topic[:150]}.",
    }
    if mode == "bad_category":
        answer["category"] = "sales"
    if mode == "missing_field":
        del answer["summary"]

    content = json.dumps(answer)
    if mode == "malformed_json":
        content = content[:-8]
    return content


def _category(text: str) -> str:
    """Return the category whose keywords appear most often, or "other" if none appear."""
    best_category = "other"
    best_count = 0
    for category, keywords in _CATEGORY_KEYWORDS.items():
        count = 0
        for keyword in keywords:
            count += text.count(keyword)
        if count > best_count:
            best_category = category
            best_count = count
    return best_category


def _priority(text: str) -> str:
    for word in _HIGH_PRIORITY_WORDS:
        if word in text:
            return "high"
    for word in _MEDIUM_PRIORITY_WORDS:
        if word in text:
            return "medium"
    return "low"
