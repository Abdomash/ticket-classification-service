"""Test setup. Tests need Postgres: start it with `docker compose up -d db`.

They use a separate `tickets_test` database on the same server and recreate the table for each test.
"""

import json
import os
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from app.api import app
from app.config import Settings
from app.data import tickets
from app.data.pool import open_pool
from app.worker import process

SERVER_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
TEST_DB_URL = make_conninfo(SERVER_URL, dbname="tickets_test")

ROOT = Path(__file__).parent.parent
SCHEMA = (ROOT / "app" / "data" / "schema.sql").read_text()
SAMPLES = json.loads((ROOT / "samples" / "tickets.json").read_text())


async def create_test_database() -> None:
    async with await psycopg.AsyncConnection.connect(SERVER_URL, autocommit=True) as conn:
        cursor = await conn.execute("SELECT 1 FROM pg_database WHERE datname = 'tickets_test'")
        if await cursor.fetchone() is None:
            await conn.execute("CREATE DATABASE tickets_test")


@pytest.fixture
async def pool():
    await create_test_database()
    async with open_pool(TEST_DB_URL, max_size=10) as pool:
        async with pool.connection() as conn:
            await conn.execute("DROP TABLE IF EXISTS tickets")
            await conn.execute(SCHEMA)
        yield pool


@pytest.fixture
async def client(pool):
    app.state.pool = pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def settings():
    return Settings(_env_file=None, database_url=TEST_DB_URL, max_attempts=3, poll_seconds=0.01)


async def run_sql(pool, sql: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(sql)


async def drain(pool, classifier, settings) -> None:
    """Process every due ticket, the way a worker would."""
    while True:
        ticket = await tickets.claim_one(pool, settings.lease_seconds)
        if ticket is None:
            return
        await process(0, ticket, pool, classifier, settings)
