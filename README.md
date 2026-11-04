<div align="center">
  <img src="assets/logo.svg" alt="Ticket Classification Service logo" width="96" height="96" />

  <h1>Ticket Classification Service</h1>

  <p>
    An HTTP service that classifies support tickets with an LLM in the background:
    a category, a priority and a one-sentence summary for each ticket.
  </p>

  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white" />
    <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.162-009688?logo=fastapi&logoColor=white" />
    <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white" />
    <img alt="Docker Compose" src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" />
    <img alt="OpenAI-compatible API" src="https://img.shields.io/badge/LLM-OpenAI--compatible-412991" />
  </p>
</div>

## About

The system runs as three services in Docker Compose: the API, Postgres (data store and queue), and the workers. The API and the workers scale independently.

- **Submit:** `POST /tickets` stores a ticket and returns right away.
- **Classify:** workers ask the model for a category, a priority and a summary, and retry failures.
- **Read:** get or list tickets through the API or the web client at `/`.

## Tech stack

| Area           | Tools                                                         |
| -------------- | ------------------------------------------------------------- |
| API            | Python 3.13 (asyncio), FastAPI, Pydantic                      |
| Data and queue | PostgreSQL 16, psycopg 3                                      |
| LLM            | OpenAI SDK, any OpenAI-compatible API (OpenRouter by default) |
| Web client     | One HTML page with Tailwind CSS                               |
| Tooling        | Docker Compose, uv, pytest                                    |

## Installation

You need Docker and `uv`.

1. Create `.env`.
   ```sh
   cp .env.example .env
   ```

2. Build and start the services.
   ```sh
   docker compose up -d --build --wait
   ```

> [!NOTE]
> The system works with a built-in fake LLM model. To use a real model, set `LLM_PROVIDER=openai_compat`, `LLM_API_KEY`, `LLM_MODEL` and `LLM_BASE_URL` in `.env` for any OpenAI-compatible API.

## Usage

1. Seed the database.
   ```sh
   docker compose exec api python scripts/load_samples.py
   ```

2. Open the web client at http://localhost:8000.

3. Browse the API docs at http://localhost:8000/docs.

4. Submit a new ticket.
   ```sh
   curl -X POST localhost:8000/tickets -H 'Content-Type: application/json' \
     -d '{"id": "t-2001", "subject": "Refund", "body": "I was charged twice for May."}'
   ```

5. Get a ticket.
   ```sh
   curl localhost:8000/tickets/t-2001
   ```

6. List and filter tickets.
   ```sh
   curl 'localhost:8000/tickets?category=billing&limit=5'
   ```

7. Review logs.
   ```sh
   docker compose logs -f api worker
   ```

8. Add more workers. Each worker processes `WORKER_CONCURRENCY` tickets at a time.
   ```sh
   docker compose up -d --scale worker=3
   ```

9. Stop the services.
   ```sh
   docker compose down
   ```

### Tests

Tests use the Compose Postgres server, with a separate database `tickets_test`.
```sh
docker compose up -d --wait db
uv run pytest
```

### Evaluation

`samples/evaluation_set.json` holds labelled tickets, including prompt-injection attempts. This script classifies them with the model configured in `.env` and reports how many labels it got right.
```sh
uv run python -m scripts.evaluate
```
Each run also saves one JSON line per ticket to `logs/evaluations/`, which git ignores.

## How it works

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant DB as Postgres
    participant Worker
    participant LLM

    Client->>API: POST /tickets
    API->>DB: insert ticket (pending)
    API-->>Client: 202 Accepted (ticket pending)
    Worker->>DB: poll and claim a ready ticket (processing)
    Worker->>LLM: classify subject and body
    LLM-->>Worker: JSON text
    Worker->>DB: save result (classified), or retry later (pending), or give up (failed)
    Client->>API: GET /tickets/{id}
    API->>DB: read ticket
    DB-->>API: ticket
    API-->>Client: ticket with its status
```

- `app/api.py` is the API service.
- `app/static/index.html` is a simple web client for the API.
- `app/worker.py` is the worker that classifies tickets.
- `app/data/` has the schema and all SQL.
- `app/llm/` has the model clients, the prompt, and the prompt-injection guard.

## Design decisions

### How and where tickets are stored

The `tickets` table in Postgres holds the ticket data and is also the job queue. Creating a ticket and queueing it is one `INSERT`, so the two can never disagree. Because Postgres is the only store, the same id is never stored twice.

**Why not RabbitMQ or Kafka?** RabbitMQ or Kafka are reasonable alternatives for this queue. However, a ticket must be queryable with `status = 'pending'` as soon as it is submitted, and duplicate ids must be rejected. A broker cannot do either easily, so we would store the ticket in a durable store first anyway and then publish it to RabbitMQ or Kafka. That adds extra synchronization for little gain, and Postgres already has the primitives a queue needs: `run_after` for scheduling, `locked_until` for leases, and `SELECT ... FOR UPDATE SKIP LOCKED` for claiming, so one store does both jobs.

### How asynchronous work is executed

The API only stores the ticket and fetches its details. Separate worker processes (`app/worker.py`) claim these tickets from Postgres and classify them. Unlike a background task inside the API, queued work is not lost when the API restarts. This also means the API and the workers can scale independently as needed.

A ticket's status moves through:

- `pending`: waiting to be claimed, once `run_after` has passed.
- `processing`: a worker claimed it and holds a lease until `locked_until`.
- `classified`: done, with a validated result.
- `failed`: all `MAX_ATTEMPTS` attempts are used, with the reason in `last_error`.

Workers claim tickets with `SELECT ... FOR UPDATE SKIP LOCKED`. Each worker locks the next free row and skips rows other workers have locked, so workers never wait on each other or take the same ticket.

### How many classifications may run at once

Each worker process runs `WORKER_CONCURRENCY=4` classifications at a time, and Compose can run several worker containers, so at most `WORKER_CONCURRENCY × N` model calls run at once (4 by default, 8 with `--scale worker=2`). For this workflow, the likely bottleneck is the provider's rate limit and latency, so the workers mostly wait for the model response. We kept these numbers low to avoid exceeding the model provider's rate limit.

### What happens to in-flight work on restart

The API does not return success unless the ticket is committed to the database, so if the API fails midway, the client can safely retry: it gets `202` if the ticket was not stored yet, or `200` if it was. The user request is idempotent, so it is safe for the client to send the same request several times.

In terms of processing queue, Postgres handles the queue state. If a worker crashes or is killed, it cannot put its ticket back to `pending`, so its ticket stays `processing` and eventually its lease ends at `locked_until` and becomes claimable again. This allows another worker to claim the ticket and process it again. Workers query for these tickets as well as new `pending` tickets, so we don't need a separate cleanup job.

**What if a worker takes too long and another worker claims its ticket?** Each claim adds 1 to `attempts`. A worker can only store its result if `attempts` still matches the value from its own claim. So only the worker with the latest claim gets to store the result, even if the first worker finishes earlier.

**Graceful shutdown:** on SIGTERM, each worker finishes its current ticket and exits. A model call is cut off after `LLM_TIMEOUT_SECONDS`, and Docker Compose gives the stopping worker another 10 seconds to finish and save the result. If a worker is killed anyway, its lease expires and another worker can pick the ticket up.

### Retry policy and how a ticket ends up failed

When an attempt fails, the worker sets the ticket back to `pending` with a later `run_after`. A model error, a timeout, and an invalid reply (see [Model boundary](#model-boundary)) all count as a failed attempt.

**Retries:** the queue gives each ticket `MAX_ATTEMPTS` (3) attempts. Once a worker claims a ticket, it can retry its model call up to 3 times (the OpenAI SDK's `max_retries`), all within the `LLM_TIMEOUT_SECONDS` duration. If the worker still fails to process the ticket, because it crashed or its model calls kept failing, the ticket goes back to the queue with one attempt used, ready for another worker to claim. Once all 3 attempts are used, the ticket is `failed` and is no longer processed.

Before the next attempt, a ticket waits 30 seconds, or longer if the provider's `Retry-After` asks for it.

### Prompt injection

Ticket content comes from the public, so we cannot fully protect the model from prompt injection. We follow common practices to make it harder, and keep an evaluation set to measure how a model actually behaves:

1. The system prompt says the ticket is untrusted data to classify, never instructions for the model.
2. The ticket is wrapped in `<ticket>` tags, and any similar tag inside it is removed first (`app/llm/guard.py`), so the ticket cannot close its own block.

Neither step removes the risk. What limits the damage is the model boundary below: whatever the model is told, it can only store one of the allowed categories and priorities and a short summary.

We evaluated three models (GPT-6 Luna, DeepSeek v4.1 Flash, and Claude Haiku 5.5) on the labelled dataset `evaluation_set.json` that contains 3 injection attempts and they all ignored the injection attempts successfully.

### Model boundary

The worker treats the model's reply as untrusted text. It asks for JSON that matches a strict schema, but it does not rely on the provider to enforce it: our code validates the reply itself. Labels must be in the allowed sets, the summary must be short, and unknown or missing fields are rejected. An invalid reply stores nothing and counts as a failed attempt.

There is no failover between LLM providers in the code. The code relies on OpenRouter to route and handle failing providers. Otherwise, we would need to implement the failover logic ourselves.

### API shape

The full API schema is at `/docs`, generated by FastAPI.

| Endpoint              | Input                                                        | Responses                                                                                      |
| --------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `POST /tickets`       | JSON `{"id", "subject", "body"}`                             | `202` new ticket, `200` same ticket already submitted, `409` same id with other content, `422` |
| `GET /tickets/{id}`   |                                                              | `200` the ticket, `404`                                                                        |
| `GET /tickets`        | Query `status`, `category`, `priority`, `limit` (1–100, default 20), `cursor` | `200` `{"items": [...], "next_cursor": ...}`, `400` invalid cursor, `422`             |
| `GET /healthz`        |                                                              | `200` while the API is up                                                                      |

Submitting the same ticket again returns `200` with the stored ticket, so a client can safely retry a submission. The same id with different content returns `409`. `POST` returns `202` because the ticket is only classified later.

- **Input:** `id` is 1 to 64 letters, digits, `_` or `-`. `subject` is optional, up to 300 characters. `body` is required, up to 20,000 characters. Unknown fields are rejected.
- **Ticket:** every ticket in a response has its `status`, `attempts`, `category`, `priority`, `summary`, `model`, `prompt_version`, `last_error` and timestamps.
- **Pagination:** tickets are listed newest first. Pass `next_cursor` back as `cursor` to get the next page.
- **Errors:** every error has the same body, `{"error": {"code": "not_found", "message": "ticket not found"}}`, where `code` is the HTTP status name.

## Scalability

This service is a prototype and is not fully ready for production. This is how we expect it to scale, and what we might change as the load grows:

- **API:** it keeps no state, so several copies could run behind a load balancer.
- **Workers:** they scale on their own, with more containers (`--scale worker=N`) or a higher `WORKER_CONCURRENCY`. Postgres gives each ticket to one worker, so the workers need no other coordination.
- **Model provider:** its rate limit is likely the real ceiling. We might spread calls over several providers or API keys, behind the same `Classifier` interface.
- **Postgres:** at high volume, the frequent queue updates would compete with reads of the same rows. We might move the job state (lease, attempts, `run_after`) into its own table, and wake workers with `LISTEN/NOTIFY` instead of polling.

## Weaknesses

Scalability above is about handling the increasing load, but here are some of the trade-offs and weaknesses of our design choices:

- **Processing is at-least-once:** The queue lease mechanism is at-least-once, meaning that tickets may get processed more than once. This could happen when a worker is slow or temporarily partitioned from the database. With no way to know about this out-of-reach worker, the database will allow another worker to process this ticket, potentially paying for the model call twice, although we only store the latest worker's result.
- **Coupling ticket storage and queuing logic:** Tickets are stored and scheduled from a single PostgreSQL. This keeps submitting and queueing tickets consistent, but creates a single point of pressure and failure: a slow or unavailable database stops both ticket ingestion and classification. If we want to expand the database horizontally, we would first need to split the queue state from the ticket data, starting with moving the job state into its own table as described in Scalability.
- **Model failures limit this system's reliability:** LLMs inherently are not reliable, and LLM inference has lots of variability, due to network issues and provider outages. OpenRouter already routes between several providers, but it itself is still a single dependency. We can mitigate lots of these issues by balancing between several LLM providers, including potentially failing over to on-prem or non-LLM solutions.

## Future features

These are some of the features we could add next.

- **Metrics:** queue depth, outcomes per attempt and model latency would show whether the workers keep up and how the model behaves. We could start by querying the database for the number of tickets in each status, and move to Prometheus as the system grows.
- **Reviewing failed tickets:** `failed` tickets work like a dead-letter queue (`GET /tickets?status=failed`). Reviewing them by hand could reveal regressions in the prompt or the model. A way to re-queue them after a fix would be a natural next step.
- **Reclassifying after prompt changes:** each result stores its `prompt_version`. After a prompt change, we could re-queue tickets classified by an older version and compare the results.
- **Schema migrations:** `schema.sql` only runs when the database is first created. A migration tool would be needed before the schema can change on a live database.
