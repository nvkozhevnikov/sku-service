"""Read-only parser project for Beka-Mak manufacturer reference pages.

This module deliberately is not a SupplierAdapter: it makes no HTTP calls,
does not register a supplier, and cannot produce commercial offers.  Its
output is reference data for a later, explicitly authorised comparison pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re

from universal_supplier.models import PropertyValue


_TAG_RE = re.compile(r"<[^>]+>")


def _text(value: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", value)).split())


@dataclass(frozen=True)
class ManufacturerReferenceCard:
    """Technical manufacturer evidence, intentionally separate from offers."""

    manufacturer: str
    model: str
    source_url: str | None
    product_type: str
    properties: tuple[PropertyValue, ...]
    standard_features: tuple[str, ...]
    optional_features: tuple[str, ...]
    diagnostics: tuple[str, ...]


def _section_items(source: str, heading: str) -> tuple[str, ...]:
    """Return list items following a labelled section in a saved page.

    This is a deliberately narrow offline parser.  A live capture must first
    prove the page's HTML structure before this becomes a production adapter.
    """
    found = re.search(
        rf"<h[2-5][^>]*>\s*{re.escape(heading)}\s*</h[2-5]>\s*<ul[^>]*>(.*?)</ul>",
        source,
        re.I | re.S,
    )
    if not found:
        return ()
    return tuple(_text(item) for item in re.findall(r"<li[^>]*>(.*?)</li>", found.group(1), re.I | re.S) if _text(item))


def parse_bekamak_manufacturer_reference(
    source: str, *, source_url: str | None = None
) -> ManufacturerReferenceCard:
    """Extract a labelled technical table from one saved manufacturer page.

    No guess is made from nearby product cards.  Rows without a nonempty label
    and value are omitted.  The result must not be used as an identity decision.
    """
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", source, re.I | re.S)
    model = _text(h1.group(1)) if h1 else ""
    description = re.search(r"<p[^>]*class=[\"']product-type[\"'][^>]*>(.*?)</p>", source, re.I | re.S)
    product_type = _text(description.group(1)) if description else ""
    rows = re.findall(r"<tr[^>]*>\s*<t[dh][^>]*>(.*?)</t[dh]>\s*<t[dh][^>]*>(.*?)</t[dh]>\s*</tr>", source, re.I | re.S)
    properties = tuple(
        PropertyValue(name=_text(name), value=_text(value), position=index)
        for index, (name, value) in enumerate(rows, start=1)
        if _text(name) and _text(value)
    )
    diagnostics = ["offline_reference_only", "not_a_supplier_adapter", "not_an_identity_decision"]
    if not properties:
        diagnostics.append("technical_table_not_found_or_unproven_structure")
    return ManufacturerReferenceCard(
        manufacturer="Bekamak",
        model=model,
        source_url=source_url,
        product_type=product_type,
        properties=properties,
        standard_features=_section_items(source, "Standard Features"),
        optional_features=_section_items(source, "Optional Features"),
        diagnostics=tuple(diagnostics),
    )
