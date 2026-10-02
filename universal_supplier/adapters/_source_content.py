"""Small source-content helpers; never make identity or price decisions."""

from __future__ import annotations

from html import unescape
import re
from urllib.parse import urljoin


_BREADCRUMB_ANCHOR_RE = re.compile(
    r'<a\b[^>]*\bitemprop=["\']item["\'][^>]*\bhref=["\']([^"\']+)["\'][^>]*>'
    r'\s*<span\b[^>]*\bitemprop=["\']name["\'][^>]*>(.*?)</span>',
    re.I | re.S,
)
_BREADCRUMB_HREF_FIRST_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*\bitemprop=["\']item["\'][^>]*>'
    r'\s*<span\b[^>]*\bitemprop=["\']name["\'][^>]*>(.*?)</span>',
    re.I | re.S,
)


def breadcrumb_category(source: str, page_url: str | None) -> tuple[str, str | None]:
    if not page_url:
        return "", None
    matches = _BREADCRUMB_ANCHOR_RE.findall(source) + _BREADCRUMB_HREF_FIRST_RE.findall(source)
    categories = []
    for href, name in matches:
        label = " ".join(unescape(re.sub(r"<[^>]+>", " ", name)).split())
        if label and href.startswith("/catalog/") and href.rstrip("/") != "/catalog":
            categories.append((label, urljoin(page_url, href)))
    return categories[-1] if categories else ("", None)
