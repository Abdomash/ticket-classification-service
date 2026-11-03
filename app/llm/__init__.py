from app.config import Settings
from app.llm.base import Classifier
from app.llm.fake_classifier import FakeClassifier
from app.llm.openai_classifier import OpenAIClassifier


def build_classifier(settings: Settings) -> Classifier:
    if settings.llm_provider == "fake":
        return FakeClassifier(settings.fake_failure_rate, settings.fake_seed)

    return OpenAIClassifier(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
    )
