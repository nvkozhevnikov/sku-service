from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .constants import BASE_URL, TRACKING_PARAMS


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def normalize_url(value: str | None, base: str = BASE_URL) -> str | None:
    if not value:
        return None
    absolute = urljoin(base, value.strip())
    p = urlsplit(absolute)
    if p.scheme not in {"http", "https"} or p.hostname not in {"optimum.su", "www.optimum.su"}:
        return absolute
    path = re.sub(r"/{2,}", "/", p.path or "/")
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS])
    return urlunsplit(("https", "optimum.su", path, query, ""))


def decimal_price(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.replace("\xa0", " ")
    match = re.search(r"(?<!\d)(\d[\d\s]*)(?:[,.](\d{1,2}))?\s*(?:₽|руб(?:\.|лей)?)?", raw, re.I)
    if not match:
        return None
    try:
        number = Decimal(match.group(1).replace(" ", "") + "." + (match.group(2) or "00"))
    except InvalidOperation:
        return None
    if number <= 0:
        return None
    return f"{number:.2f}"


def availability(value: str | None) -> tuple[str | None, int | None]:
    text = clean_text(value)
    if not text:
        return None, None
    low = text.lower()
    if "под заказ" in low:
        return "on_request", None
    if "снят с производства" in low:
        return "discontinued", None
    if "нет в наличии" in low:
        return "out_of_stock", None
    if "в наличии" in low:
        m = re.search(r"в наличии\s*(\d+)", low)
        return "in_stock", int(m.group(1)) if m else None
    return None, None


def unit_from_name(name: str) -> str | None:
    match = re.search(r",\s*([^,]+)$", name)
    return clean_text(match.group(1)) if match else None
