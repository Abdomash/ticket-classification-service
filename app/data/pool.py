from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


@asynccontextmanager
async def open_pool(url: str, max_size: int) -> AsyncIterator[AsyncConnectionPool]:
    """Open a connection pool and wait until the database answers, so startup fails fast."""
    pool = AsyncConnectionPool(url, min_size=1, max_size=max_size, open=False, kwargs={"row_factory": dict_row})
    async with pool:
        await pool.wait(timeout=30)
        yield pool
