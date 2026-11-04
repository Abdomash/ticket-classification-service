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

from app.config import Settings, load_settings
from app.llm import build_classifier
from app.llm.base import Classifier, LLMError

ROOT = Path(__file__).parent.parent
EVALUATION_SET = ROOT / "samples" / "evaluation_set.json"
LOG_DIR = ROOT / "logs" / "evaluations"


# Classification


async def classify(classifier: Classifier, ticket: dict) -> dict:
    """Classify one ticket and return a record of the expected and actual answer."""
    record = {
        "id": ticket["id"],
        "expected_category": ticket["category"],
        "expected_priority": ticket["priority"],
        "category": None,
        "priority": None,
        "summary": None,
        "error": None,
    }

    start = time.monotonic()
    try:
        result = await classifier.classify(ticket["subject"], ticket["body"])
    except LLMError as error:
        record["error"] = str(error)
    else:
        record["category"] = result.classification.category
        record["priority"] = result.classification.priority
        record["summary"] = result.classification.summary
    record["seconds"] = round(time.monotonic() - start, 2)

    return record


async def classify_all(settings: Settings, tickets: list[dict]) -> list[dict]:
    """Classify the tickets one by one, showing progress."""
    records = []
    print_progress(0, len(tickets))

    async with build_classifier(settings) as classifier:
        for ticket in tickets:
            record = await classify(classifier, ticket)
            records.append(record)
            print_progress(len(records), len(tickets))

    print("\n")
    return records


def print_progress(done: int, total: int) -> None:
    """Redraw the progress bar in place on the current line."""
    WIDTH = 25
    filled = WIDTH * done // total
    bar = "█" * filled + "░" * (WIDTH - filled)
    print(f"\r{bar} {done}/{total} tickets classified", end="", flush=True)


# Log


def save_log(model: str, records: list[dict]) -> Path:
    """Write one JSON line per ticket and return the file path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = LOG_DIR / f"{timestamp}.jsonl"

    with path.open("w") as file:
        for record in records:
            line = json.dumps({"model": model, **record})
            file.write(line + "\n")

    return path


# Report


def print_report(
    model: str, records: list[dict], seconds: float, log_path: Path
) -> None:
    total = len(records)
    category_correct = sum(r["category"] == r["expected_category"] for r in records)
    priority_correct = sum(r["priority"] == r["expected_priority"] for r in records)
    errors = sum(r["error"] is not None for r in records)

    print(f"Model:    {model}")
    print(f"Category: {category_correct}/{total} correct")
    print(f"Priority: {priority_correct}/{total} correct")
    print(f"Errors:   {errors}/{total}")
    print(f"Duration: {seconds:.1f}s")
    print(f"Log:      {log_path.relative_to(ROOT)}")

    mistakes = [r for r in records if is_wrong(r)]
    if not mistakes:
        return

    print()
    print("Wrong labels (expected → got):")
    print(f"{'Ticket':<8}{'Category':<25}Priority")
    for record in mistakes:
        print(format_row(record))


def is_wrong(record: dict) -> bool:
    if record["error"] is not None:
        return True
    if record["category"] != record["expected_category"]:
        return True
    return record["priority"] != record["expected_priority"]


def format_row(record: dict) -> str:
    ticket = record["id"].ljust(8)
    if record["error"] is not None:
        return ticket + "error: " + record["error"]

    category = compare(record["expected_category"], record["category"], 9)
    priority = compare(record["expected_priority"], record["priority"], 6)
    return ticket + category.ljust(25) + priority


def compare(expected: str, got: str, width: int) -> str:
    """Return the label if it is right, or "expected → got" if it is wrong."""
    if expected == got:
        return expected
    return f"{expected:<{width}} → {got}"


# Main


async def main() -> None:
    # The settings require a database URL, but this script never connects to the database.
    os.environ.setdefault("DATABASE_URL", "unused")
    settings = load_settings()
    tickets = json.loads(EVALUATION_SET.read_text())

    model = settings.llm_model
    if settings.llm_provider == "fake":
        model = "fake"

    start = time.monotonic()
    records = await classify_all(settings, tickets)
    seconds = time.monotonic() - start

    log_path = save_log(model, records)
    print_report(model, records, seconds, log_path)


if __name__ == "__main__":
    asyncio.run(main())
