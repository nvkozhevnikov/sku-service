import asyncio
import http.client
import io
from decimal import Decimal
from pathlib import Path

import pytest

from universal_supplier.adapters.bekamak import parse_bekamak_detail
from universal_supplier.adapters.optimum_source.constants import CATALOG_URL
from universal_supplier.adapters.optimum_source.discovery import OptimumDiscovery
from universal_supplier.adapters.optimum_source.fetch import FetchResult
from universal_supplier.adapters.optimum_source.normalize import decimal_price
from universal_supplier.adapters.partner_st import normalize_availability
from universal_supplier.change_detection import card_hashes
from universal_supplier.commercial import PriceState, extract_public_price
from universal_supplier.http_capture import (
    MAX_RESPONSE_BYTES, CaptureStatus, HttpResponse, ResponseTooLargeError, _read_bounded,
    capture_public_html, sanitise_html,
)
from universal_supplier.kami_robots import KamiRobots
from universal_supplier.models import ProductCard


ROOT = Path(__file__).resolve().parents[1]


def test_bekamak_identity_with_quote_is_escaped():
    page = ('<h1 id="pagetitle">Станок 7" дюймов</h1>'
            '<div class="price" data-currency="RUB" data-value="12990"></div>')
    product = parse_bekamak_detail(page, source_url="https://beka-mak.ru/x/")
    assert product.price.state is PriceState.NUMERIC_PUBLIC
    assert product.price.current_price == Decimal("12990")


def test_machine_price_is_not_treated_as_thousands():
    page = ('<main data-product-identity="X1"><meta itemprop="priceCurrency" content="RUB">'
            '<span itemprop="price" content="12990.000"></span></main>')
    assert extract_public_price(page, product_identity="X1").current_price == Decimal("12990")
    json_ld = ('<script type="application/ld+json">{"@type":"Product","sku":"X1",'
               '"offers":{"price":"12990.000","priceCurrency":"RUB"}}</script>')
    assert extract_public_price(json_ld, product_identity="X1").current_price == Decimal("12990")


@pytest.mark.parametrize(("raw", "expected"), [
    ("191 487 ₽", "191487.00"), ("191487.00", "191487.00"), ("1.234", "1234.00"),
    ("12 990 15 000", None), ("от 3 шт. 12 990 ₽", "12990.00"), ("0 ₽", None),
    ("1 234,50 руб.", "1234.50"), ("12990.000", "12990.00"),
])
def test_optimum_decimal_price_does_not_glue_numbers(raw, expected):
    assert decimal_price(raw) == expected


class _Fetcher:
    def __init__(self, pages):
        self.pages = pages

    async def get(self, url):
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def test_optimum_category_failures_are_counted():
    ok = FetchResult(CATALOG_URL + "a/", CATALOG_URL + "a/", 200, "<html></html>", 1)
    discovery = OptimumDiscovery(_Fetcher({
        CATALOG_URL: FetchResult(CATALOG_URL, CATALOG_URL, 403, "", 1),
        CATALOG_URL + "a/": ok,
        CATALOG_URL + "b/": FetchResult(CATALOG_URL + "b/", CATALOG_URL + "b/", 500, "", 1),
        CATALOG_URL + "c/": OSError("boom"),
    }))
    categories = [{"category_url": url} for url in
                  (CATALOG_URL, CATALOG_URL + "a/", CATALOG_URL + "b/", CATALOG_URL + "c/")]
    asyncio.run(discovery.category_urls(categories))
    assert sorted(item["url"] for item in discovery.category_page_failures) == [
        CATALOG_URL + "b/", CATALOG_URL + "c/"]


def test_partner_negative_availability_wins():
    assert normalize_availability("Нет в наличии") == "out_of_stock"
    assert normalize_availability("https://schema.org/OutOfStock") == "out_of_stock"
    assert normalize_availability("https://schema.org/InStock") == "in_stock"


class _Client:
    def __init__(self, *responses):
        self.responses = list(responses)

    def get(self, url, *, timeout_seconds):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _Store:
    def save(self, *, evidence_dir, final_url, evidence_sha256, body):
        return f"capture://example.test/{evidence_sha256}.html"


def test_incomplete_read_is_a_network_error():
    client = _Client(http.client.IncompleteRead(b"x"), http.client.IncompleteRead(b"x"))
    result = capture_public_html("https://example.test/card", evidence_dir=ROOT, client=client,
                                 evidence_store=_Store())
    assert result.status is CaptureStatus.NETWORK_ERROR
    assert "network_error:IncompleteRead" in result.diagnostics


def test_body_read_is_bounded():
    assert _read_bounded(io.BytesIO(b"x" * 10)) == b"x" * 10
    with pytest.raises(ResponseTooLargeError):
        _read_bounded(io.BytesIO(b"x" * (MAX_RESPONSE_BYTES + 1)))
    result = capture_public_html("https://example.test/card", evidence_dir=ROOT,
                                 client=_Client(ResponseTooLargeError("big")), evidence_store=_Store())
    assert result.status is CaptureStatus.NETWORK_ERROR
    assert "response_too_large" in result.diagnostics


def test_sanitise_html_uses_content_type_charset():
    body = "<h1>Станок</h1>".encode("cp1251")
    assert "Станок" in sanitise_html(body, "text/html; charset=windows-1251").decode("utf-8")
    assert sanitise_html(b"<h1>x</h1>", None) == b"<h1>x</h1>"
    assert sanitise_html(b"<h1>x</h1>", "text/html; charset=bogus") == b"<h1>x</h1>"


def test_kami_robots_matches_product_token_exactly():
    text = "User-agent: Universal\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    robots = KamiRobots(text, agent="UniversalSupplier-KamiAudit/1.0")
    assert robots.can_fetch("https://stanki.ru/catalog/")
    exact = KamiRobots("User-agent: universalsupplier-kamiaudit\nDisallow: /a\n\nUser-agent: *\nDisallow: /\n",
                       agent="UniversalSupplier-KamiAudit/1.0")
    assert exact.can_fetch("https://stanki.ru/b") and not exact.can_fetch("https://stanki.ru/a")


def _card(price, old_price=None):
    return ProductCard(
        supplier_code="s", external_id="1", sku="A", name="n", requested_url="u", final_url="u",
        canonical_url="u", http_status=200, redirect_urls=(), price=price, old_price=old_price,
        currency="RUB", availability_raw="", availability_normalized="in_stock", quantity=None,
        description_text="", description_html="", brand="", manufacturer="",
    )


def test_commercial_hash_ignores_decimal_format_only():
    two_places = card_hashes(_card(Decimal("12990.00")))["commercial"]
    assert card_hashes(_card(Decimal("12990")))["commercial"] == two_places
    assert card_hashes(_card(Decimal("12990.000")))["commercial"] == two_places
    assert card_hashes(_card(Decimal("12990.01")))["commercial"] != two_places
    assert card_hashes(_card(Decimal("12990.001")))["commercial"] != two_places
