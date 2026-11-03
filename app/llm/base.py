"""The classifier interface, the one error it raises, and validation of model output."""

from dataclasses import dataclass
from typing import Protocol, Self

from pydantic import ValidationError

from app.models import Classification


class LLMError(Exception):
    """A model call that did not produce a valid classification.

    The worker retries every LLMError until MAX_ATTEMPTS. `retry_after` is the provider's
    Retry-After in seconds, if it sent one. The message is short and never contains model output.
    """

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class ClassifyResult:
    classification: Classification
    model: str
    prompt_version: str


class Classifier(Protocol):
    async def classify(self, subject: str, body: str) -> ClassifyResult: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc: object) -> None: ...


def parse_classification(content: str) -> Classification:
    """Validate the model's raw text. Raise an LLMError naming the fields that failed."""
    try:
        return Classification.model_validate_json(content)
    except ValidationError as e:
        fields = []
        for error in e.errors():
            field = ".".join(str(part) for part in error["loc"])
            fields.append(field or "json")
        # `from None` drops pydantic's error from the traceback, because it contains the model output.
        raise LLMError(f"invalid output: {', '.join(fields)}") from None
