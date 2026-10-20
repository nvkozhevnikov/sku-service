"""Scheme checks for supplier-provided URLs that end up in links or exports."""
from __future__ import annotations

from urllib.parse import urlsplit

HTTP_SCHEMES = frozenset({"http", "https"})


def is_http_url(value: object) -> bool:
    """True only for absolute http(s) URLs with a host and no control characters."""
    text = str(value or "").strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return parsed.scheme.lower() in HTTP_SCHEMES and bool(parsed.netloc)
