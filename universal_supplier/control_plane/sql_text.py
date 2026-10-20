"""Helpers for user-supplied text inside SQL patterns."""
from __future__ import annotations


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters; PostgreSQL's default escape character is a backslash."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(value: str) -> str:
    """Pattern matching ``value`` literally anywhere in the text."""
    return f"%{escape_like(value)}%"
