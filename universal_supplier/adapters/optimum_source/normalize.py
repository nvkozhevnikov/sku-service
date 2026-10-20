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


OWN_HOSTS = frozenset({"optimum.su", "www.optimum.su"})


def normalize_url(value: str | None, base: str = BASE_URL, *, allow_foreign: bool = False) -> str | None:
    """Canonical Optimum URL, or None for anything the crawler must not follow.

    Foreign hosts return None unless ``allow_foreign`` (media such as embedded
    videos); even then only plain http(s) URLs survive.
    """
    if not value:
        return None
    try:
        absolute = urljoin(base, value.strip())
        p = urlsplit(absolute)
        port = p.port
    except ValueError:
        return None
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        return None
    if p.hostname not in OWN_HOSTS or port not in {None, 80, 443}:
        return absolute if allow_foreign and p.hostname not in OWN_HOSTS else None
    path = re.sub(r"/{2,}", "/", p.path or "/")
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS])
    return urlunsplit(("https", "optimum.su", path, query, ""))


_PRICE_RUN_RE = re.compile(r"(?<![\d.,])\d[\d .,]*")
_CURRENCY_AFTER_RE = re.compile(r"\s*(?:₽|руб)", re.I)


def _parse_price_run(run: str) -> Decimal | None:
    run = run.strip(" .,")
    if re.fullmatch(r"\d+\.0+", run):
        # Machine format such as "12990.000"; never a grouped thousands value.
        return Decimal(run.split(".")[0])
    integer, fraction = run, "00"
    split = re.fullmatch(r"(.*\d)[.,](\d{1,2})", run)
    if split and not re.search(r"[.,]", split.group(1)):
        integer, fraction = split.group(1), split.group(2)
    separators = set(re.findall(r"[ .,]", integer))
    if len(separators) > 1:
        return None
    groups = re.split(r"[ .,]", integer) if separators else [integer]
    if len(groups) > 1 and (not 1 <= len(groups[0]) <= 3 or any(len(group) != 3 for group in groups[1:])):
        # "12 990 15 000" is two adjacent amounts, not one glued number.
        return None
    try:
        return Decimal("".join(groups) + "." + fraction)
    except InvalidOperation:
        return None


def decimal_price(value: str | None) -> str | None:
    if not value:
        return None
    raw = re.sub(r"[\xa0\u202f\s]+", " ", value)
    runs = [match for match in _PRICE_RUN_RE.finditer(raw) if match.group(0).strip(" .,")]
    if not runs:
        return None
    priced = [match for match in runs if _CURRENCY_AFTER_RE.match(raw, match.start() + len(match.group(0).rstrip(" .,")))]
    if len(priced) == 1:
        chosen = priced[0]
    elif len(runs) == 1:
        chosen = runs[0]
    else:
        # "от 3 шт. 12 990" without a currency marker is ambiguous.
        return None
    number = _parse_price_run(chosen.group(0))
    if number is None or number <= 0:
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
