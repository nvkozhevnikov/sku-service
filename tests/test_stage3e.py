import asyncio
from types import SimpleNamespace

from universal_supplier.adapters.optimum import to_product_card
from universal_supplier.adapters.optimum_source.discovery import OptimumDiscovery
from universal_supplier.adapters.optimum_source.parser import OptimumProductParser
from universal_supplier.repository import InMemoryRepository

from test_optimum_source import HTML, URL, product


class SitemapFetcher:
    def __init__(self):
        self.maps = [f"https://optimum.su/sitemap-{index}.xml" for index in range(11)]

    async def get(self, url):
        if url == "https://optimum.su/sitemap.xml":
            body = "<sitemapindex>" + "".join(
                f"<sitemap><loc>{item}</loc></sitemap>" for item in self.maps
            ) + "</sitemapindex>"
        else:
            index = self.maps.index(url)
            body = f"<urlset><url><loc>https://optimum.su/catalog/item-{index}/</loc></url></urlset>"
        return SimpleNamespace(text=body)


def test_dynamic_sitemap_handles_all_eleven_child_maps():
    maps, urls = asyncio.run(OptimumDiscovery(SitemapFetcher()).sitemap_urls())
    assert maps == SitemapFetcher().maps
    assert len(urls) == 11


def test_canonical_url_is_identity_fallback_when_bitrix_id_absent():
    html = HTML.replace(' data-data=\'{"id":"1001"}\'', "")
    parsed = OptimumProductParser().parse_product(html, URL)
    assert parsed.identity.external_id_type == "canonical_url"
    assert parsed.identity.external_id == parsed.identity.canonical_url


def test_bridge_uses_optimum_namespace_and_keeps_supplier_article_separate():
    card = to_product_card(product(), final_url=URL)
    assert card.supplier_code == "optimum"
    assert card.external_id == "1001"
    assert card.sku == "3420350"
    assert card.raw_data["supplier_article_namespace"] == "ARTIKUL_OPTIMUM"


def test_bridge_preserves_aliases_without_changing_product_identity():
    alias = "https://optimum.su/catalog/alias/"
    card = to_product_card(product(), final_url=URL, aliases=(alias,))
    assert card.external_id == "1001"
    assert card.alias_urls == (alias,)


def test_bridge_maps_media_and_documents_to_generic_media():
    card = to_product_card(product(), final_url=URL)
    kinds = {item.media_type for item in card.media}
    assert {"image", "document", "video"} <= kinds


def test_navigation_links_are_not_sellable_variants():
    card = to_product_card(product(), final_url=URL)
    values = card.options[0].values
    assert values and {item.classification for item in values} == {"NAVIGATION_TO_PRODUCT"}


def test_one_card_creates_one_default_offer_and_run2_is_idempotent():
    repository = InMemoryRepository()
    card = to_product_card(product(), final_url=URL)
    run1 = repository.ingest([card], "2026-09-03T00:00:00Z")
    run2 = repository.ingest([card], "2026-09-03T01:00:00Z")
    assert (run1.new_products, run1.new_offers) == (1, 1)
    assert (run2.new_products, run2.new_offers) == (0, 0)
    assert (run2.unchanged_products, run2.unchanged_offers) == (1, 1)


def test_same_raw_sku_is_isolated_by_supplier_namespace():
    repository = InMemoryRepository()
    optimum = to_product_card(product(), final_url=URL)
    partner = optimum.__class__(**{**optimum.__dict__, "supplier_code": "partner_st"})
    counts = repository.ingest([optimum, partner], "2026-09-03T00:00:00Z")
    assert counts.new_products == counts.new_offers == 2
    assert len(repository.state.products) == len(repository.state.offers) == 2


def test_source_availability_is_preserved_beside_database_normalization():
    parsed = product()
    parsed.commercial.availability_normalized = "on_request"
    card = to_product_card(parsed, final_url=URL)
    assert card.availability_normalized == "backorder"
    assert card.raw_data["source_record"]["commercial"]["availability_normalized"] == "on_request"
