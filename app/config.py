from typing import Literal

from pydantic import (
    Field,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# A claimed ticket stays locked for the model timeout plus this margin.
LEASE_MARGIN_SECONDS = 30


class Settings(BaseSettings):
    """Settings read from environment variables and the optional .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    llm_provider: Literal["fake", "openai_compat"] = "fake"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "openai/gpt-6-luna"
    llm_timeout_seconds: PositiveFloat = 30
    fake_failure_rate: float = Field(0.05, ge=0, le=1)
    fake_seed: int = 42

    worker_concurrency: PositiveInt = 4
    max_attempts: PositiveInt = 3
    poll_seconds: PositiveFloat = 1

    @property
    def lease_seconds(self) -> float:
        """How long a worker owns a claimed ticket before another worker may take it."""
        return self.llm_timeout_seconds + LEASE_MARGIN_SECONDS

    @model_validator(mode="after")
    def _require_api_key(self) -> "Settings":
        if self.llm_provider != "fake" and not self.llm_api_key.get_secret_value():
            raise ValueError("LLM_API_KEY is required unless LLM_PROVIDER=fake")
        return self


def load_settings() -> Settings:
    """Return the settings, or exit with a list of the invalid ones. Values are never printed."""
    try:
        return Settings()
    except ValidationError as e:
        lines = ["Invalid configuration:"]
        for error in e.errors(include_input=False):
            field = ".".join(str(part) for part in error["loc"]).upper()
            lines.append(f"  {field} {error['msg']}")
        raise SystemExit("\n".join(lines)) from None
