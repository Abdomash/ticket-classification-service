"""HTTP API for submitting tickets and reading them back. The worker does the classification."""

import base64
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import load_settings
from app.data import tickets
from app.data.pool import open_pool
from app.logging import log, request_id, setup_logging
from app.models import Category, Priority, Status, Ticket

INDEX_HTML = Path(__file__).parent / "static" / "index.html"

# Configure logging on import, before uvicorn writes its startup lines.
setup_logging(os.environ.get("LOG_LEVEL", "INFO"))


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    subject: str = Field("", max_length=300)
    body: str = Field(min_length=1, max_length=20_000)


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subject: str
    body: str
    status: Status
    attempts: int
    category: Category | None
    priority: Priority | None
    summary: str | None
    model: str | None
    prompt_version: str | None
    last_error: str | None = Field(description="Short reason the last attempt failed, if it did")
    created_at: datetime
    updated_at: datetime


class ErrorBody(BaseModel):
    code: str
    message: str


class Error(BaseModel):
    error: ErrorBody


class Page(BaseModel):
    items: list[TicketOut]
    next_cursor: str | None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    async with open_pool(settings.database_url, max_size=10) as pool:
        app.state.pool = pool
        yield


app = FastAPI(title="Ticket Classification Service", lifespan=lifespan)


def error_response(status: int, message: str) -> JSONResponse:
    """Build the error body used by every endpoint: {"error": {"code": "not_found", "message": ...}}."""
    phrase = HTTPStatus(status).phrase
    code = phrase.lower().replace(" ", "_")
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"])
        problems.append(f"{field}: {error['msg']}")
    return error_response(422, "; ".join(problems))


@app.middleware("http")
async def add_request_id(request: Request, call_next) -> Response:
    """Give each request an id, added to its log lines and to the X-Request-ID response header."""
    rid = uuid.uuid4().hex
    request_id.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.post(
    "/tickets",
    status_code=202,
    responses={
        200: {"model": TicketOut, "description": "The same ticket was already submitted"},
        409: {"model": Error, "description": "The id exists with different content"},
        422: {"model": Error, "description": "Invalid ticket"},
    },
)
async def create_ticket(ticket: TicketIn, request: Request, response: Response) -> TicketOut:
    """Return 202 for a new ticket, 200 if the same ticket was already sent, 409 if the id has other content."""
    stored, outcome = await tickets.insert(request.app.state.pool, ticket.id, ticket.subject, ticket.body)
    log(f"ticket.{outcome}", ticket_id=ticket.id)

    if outcome == "conflict":
        raise HTTPException(409, "a ticket with this id already exists with different content")
    if outcome == "duplicate":
        response.status_code = 200
    return TicketOut.model_validate(stored)


@app.get("/tickets/{ticket_id}", responses={404: {"model": Error, "description": "Ticket not found"}})
async def get_ticket(ticket_id: str, request: Request) -> TicketOut:
    ticket = await tickets.get(request.app.state.pool, ticket_id)
    if ticket is None:
        raise HTTPException(404, "ticket not found")
    return TicketOut.model_validate(ticket)


@app.get(
    "/tickets",
    responses={
        400: {"model": Error, "description": "Invalid cursor"},
        422: {"model": Error, "description": "Invalid query parameter"},
    },
)
async def list_tickets(
    request: Request,
    status: Status | None = None,
    category: Category | None = None,
    priority: Priority | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> Page:
    """List tickets, newest first. Pass `next_cursor` back as `cursor` to get the next page."""
    after = None
    if cursor is not None:
        after = decode_cursor(cursor)
        if after is None:
            raise HTTPException(400, "invalid cursor")

    # Fetch one extra row to know whether there is a next page.
    rows = await tickets.list_page(request.app.state.pool, status, category, priority, limit + 1, after)
    items = [TicketOut.model_validate(row) for row in rows[:limit]]

    next_cursor = None
    if len(rows) > limit:
        next_cursor = encode_cursor(rows[limit - 1])
    return Page(items=items, next_cursor=next_cursor)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the single-page web client."""
    return FileResponse(INDEX_HTML)


@app.get("/healthz")
async def healthz() -> dict:
    """Return 200 while the API process is up."""
    return {"status": "ok"}


def encode_cursor(ticket: Ticket) -> str:
    """Return a cursor pointing just after `ticket`: base64 of "created_at|id"."""
    text = f"{ticket.created_at.isoformat()}|{ticket.id}"
    return base64.urlsafe_b64encode(text.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    """Return (created_at, id) from a cursor, or None if the cursor is not valid."""
    try:
        text = base64.urlsafe_b64decode(cursor).decode()
        created_at, id = text.split("|", 1)
        return datetime.fromisoformat(created_at), id
    except ValueError:
        return None
