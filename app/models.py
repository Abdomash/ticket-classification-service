"""Types shared by the data layer, the LLM layer and the API."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator


class Category(StrEnum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    OTHER = "other"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Status(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    CLASSIFIED = "classified"
    FAILED = "failed"


class Classification(BaseModel):
    """The model's answer after validation. Output that does not fit is rejected and never stored."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    priority: Priority
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

    @field_validator("category", "priority", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        """Accept " Billing " as "billing"."""
        if isinstance(value, str):
            return value.strip().lower()
        return value


class Ticket(BaseModel):
    """A row of the tickets table."""

    id: str
    subject: str
    body: str
    status: Status
    category: Category | None
    priority: Priority | None
    summary: str | None
    model: str | None
    prompt_version: str | None
    last_error: str | None
    attempts: int
    locked_until: datetime | None
    run_after: datetime
    created_at: datetime
    updated_at: datetime
