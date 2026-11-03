"""Prompt-injection defences applied to ticket text before it reaches the model."""

import re

_DELIMITER = re.compile(r"<\s*/?\s*ticket\s*>", re.IGNORECASE)


def strip_delimiters(text: str) -> str:
    """Remove anything that looks like a <ticket> tag, so ticket text cannot close its own block."""
    return _DELIMITER.sub("", text)


def wrap_ticket(subject: str, body: str) -> str:
    """Return the ticket inside one <ticket> block, the only place the system prompt treats as ticket text."""
    subject = strip_delimiters(subject)
    body = strip_delimiters(body)
    return f"<ticket>\nSubject: {subject}\n\nBody: {body}\n</ticket>"
