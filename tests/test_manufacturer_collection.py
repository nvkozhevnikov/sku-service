import hashlib
import json
from pathlib import Path
import unittest

from universal_supplier.adapters.bekamak_official import parse_bekamak_official_detail
from universal_supplier.http_capture import CaptureStatus, HttpResponse, inspect_html_page
from universal_supplier.manufacturer_collection import (
    _manifest_from_xml, discover_official_manifest, run_official_collection,
)


URL = "https://www.bekamak.com/urun/bmso-460c/en"
CARD = (
    '<html><h1 class="pagetitle__heading">BMSO-460C</h1><table class="table"><tr><td>Weight</td>'
    '<td>1655 kg</td></tr></table><script type="application/ld+json">'
    + json.dumps({"@graph": [{"@type": "Product", "name": "BMSO-460C", "sku": "BMSO-460C",
                   "url": URL, "description": "Band saw", "image": "https://www.bekamak.com/images/a.jpg",
                   "additionalProperty": [{"name": "Weight", "value": "1655 kg"}],
                   "offers": {"priceSpecification": {"priceCurrency": "EUR"},
                              "availability": "https://schema.org/InStock"}}]})
    + "</script></html>"
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_validated(self, url, *, timeout_seconds, validator):
        validator(url)
        self.calls.append(url)
        response = self.responses.pop(0)
        validator(response.final_url)
        return response


class FakeEvidence:
    def save(self, **kwargs):
        return "capture://www.bekamak.com/fixture.html"


class ManufacturerCollectionTests(unittest.TestCase):
    def test_official_card_is_not_false_captcha_or_commercial_price(self):
        self.assertEqual(inspect_html_page(CARD.encode(), expected_model="BMSO-460C")[0], CaptureStatus.SUCCESS)
        product = parse_bekamak_official_detail(CARD, source_url=URL)
        self.assertEqual(product.supplier, "beka_mak_tr")
        self.assertEqual(product.site_internal_id, "bmso-460c")
        self.assertEqual(product.price.state.value, "missing")
        self.assertIsNone(product.price.current_price)
        self.assertIsNone(product.availability)
        self.assertEqual(product.technical_properties, (("Weight", "1655 kg"),))

    def test_other_official_equipment_series_is_not_discarded(self):
        other_url = "https://www.bekamak.com/urun/opus-300/en"
        other = CARD.replace(URL, other_url).replace("BMSO-460C", "OPUS-300")
        self.assertEqual(inspect_html_page(other.encode(), expected_model="OPUS-300")[0], CaptureStatus.SUCCESS)
        self.assertEqual(parse_bekamak_official_detail(other, source_url=other_url).supplier_model, "OPUS-300")

    def test_official_category_is_preserved_from_breadcrumb_evidence(self):
        breadcrumb = {"@graph": [{"@type": "BreadcrumbList", "itemListElement": [
            {"name": "Home", "item": "https://www.bekamak.com/"},
            {"name": "Straight Cut", "item": "https://www.bekamak.com/kategori/straight/en"},
            {"name": "BMSO-460C"}]}]}
        card = CARD.replace("</script>", "</script><script type=\"application/ld+json\">"
                            + json.dumps(breadcrumb) + "</script>")
        product = parse_bekamak_official_detail(card, source_url=URL)
        self.assertEqual(product.source_category, "Straight Cut")
        self.assertEqual(product.source_category_url, "https://www.bekamak.com/kategori/straight/en")

    def test_technical_details_table_is_used_when_jsonld_omits_properties(self):
        product_ld = {"@graph": [{"@type": "Product", "name": "BMDO-100XS", "sku": "bmdo-100-xs",
                                   "url": "https://www.bekamak.com/urun/bmdo-100-xs/en",
                                   "description": "Circular sawing machine"}]}
        html = ('<h1>BMDO-100XS</h1><div id="teknik-detaylar"><table>'
                '<tr><td>Main Drive Motor</td><td>15 kW</td></tr>'
                '<tr><td>Blade OD x T x t</td><td>360 mm</td></tr>'
                '</table></div><script type="application/ld+json">'
                + json.dumps(product_ld) + '</script>')
        product = parse_bekamak_official_detail(
            html, source_url="https://www.bekamak.com/urun/bmdo-100-xs/en")
        self.assertEqual(product.technical_properties,
                         (("Main Drive Motor", "15 kW"), ("Blade OD x T x t", "360 mm")))

    def test_unrelated_table_does_not_confirm_equipment(self):
        product_ld = {"@graph": [{"@type": "Product", "name": "PART-100", "sku": "part-100",
                                   "url": "https://www.bekamak.com/urun/part-100/en"}]}
        html = ('<h1>PART-100</h1><div id="teknik-detaylar"><table>'
                '<tr><td>Color</td><td>Red</td></tr></table></div>'
                '<script type="application/ld+json">' + json.dumps(product_ld) + '</script>')
        with self.assertRaisesRegex(ValueError, "technical properties"):
            parse_bekamak_official_detail(html, source_url="https://www.bekamak.com/urun/part-100/en")

    def test_sitemap_deduplicates_and_reviews_unstable_slug(self):
        body = ("<urlset><url><loc>" + URL + "</loc></url><url><loc>" + URL + "</loc></url>"
                "<url><loc>https://www.bekamak.com/urun/bmso-820c_random/en</loc></url></urlset>").encode()
        manifest = _manifest_from_xml(body)
        self.assertEqual(len(manifest.candidates), 1)
        self.assertEqual(len(manifest.review_urls), 1)
        self.assertEqual(manifest.candidates[0].expected_model, "BMSO-460C")

    def test_dry_run_never_writes_and_preserves_review(self):
        sitemap = ("<urlset><url><loc>" + URL + "</loc></url></urlset>").encode()
        manifest = _manifest_from_xml(sitemap)
        response = HttpResponse(URL, 200, "text/html", CARD.encode())
        client = FakeClient([response])
        result = run_official_collection(manifest=manifest, evidence_dir=Path("work/tmp"), client=client,
                                         evidence_store=FakeEvidence())
        self.assertEqual(result["attempted_count"], 1)
        self.assertEqual(result["rows"][0]["status"], "DRY_RUN")
        self.assertIsNone(result["rows"][0]["price"])

    def test_model_conflict_stays_review(self):
        manifest = _manifest_from_xml(("<urlset><url><loc>" + URL + "</loc></url></urlset>").encode())
        conflict = CARD.replace("BMSO-460C", "BMSO-560C")
        client = FakeClient([HttpResponse(URL, 200, "text/html", conflict.encode())])
        result = run_official_collection(manifest=manifest, evidence_dir=Path("work/tmp"), client=client,
                                         evidence_store=FakeEvidence())
        self.assertEqual(result["rows"][0]["status"], "REVIEW")

    def test_blocked_source_stops(self):
        manifest = _manifest_from_xml(("<urlset><url><loc>" + URL + "</loc></url></urlset>").encode())
        client = FakeClient([HttpResponse(URL, 429, "text/html", b"rate limited")])
        result = run_official_collection(manifest=manifest, evidence_dir=Path("work/tmp"), client=client,
                                         evidence_store=FakeEvidence())
        self.assertEqual(result["rows"][0]["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
