FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /srv
ENV PATH="/srv/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app ./app
COPY scripts ./scripts
COPY samples ./samples

RUN useradd --create-home app
USER app

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
