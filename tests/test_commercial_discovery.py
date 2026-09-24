import unittest
from pathlib import Path

from universal_supplier.commercial_discovery import discover_catalog_page, fetch_catalog_page, model_and_execution, validate_catalog_url
from universal_supplier.http_capture import CaptureStatus, HttpResponse


class FakeCatalogClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_validated(self, url, *, timeout_seconds, validator):
        self.calls.append(url)
        validator(url)
        return self.response


class RecordingEvidence:
    def __init__(self):
        self.saved = []

    def save(self, **kwargs):
        self.saved.append(kwargs)
        return "fixture://catalog"


class CommercialDiscoveryTests(unittest.TestCase):
    def test_intervesp_keeps_wp_execution_and_rejects_navigation(self):
        page = """<a href='/catalog/lentochnye/beka-mak-bmsy-440dgh-wp2/'>BMSY-440DGH WP2</a>
        <a href='/catalog/lentochnye/'>Раздел</a><a href='/catalog/accessories/'>Аксессуары</a>
        <a href='?PAGEN_1=2'>2</a>"""
        result = discover_catalog_page("intervesp", "https://intervesp.ru/catalog/lentochnye/", page)
        self.assertEqual([(item.expected_model, item.execution) for item in result.products], [("BMSY-440DGH-WP2", "WP2")])
        self.assertEqual(result.pagination_urls, ("https://intervesp.ru/catalog/lentochnye/?PAGEN_1=2",))

    def test_bekamak_accepts_product_cards_only(self):
        page = """<a href='/product/bms_230_dg/'>BMS 230 DG</a>
        <a href='/product/rolik/'>Ролик для BMS</a><a href='/catalog/ruchnye/'>Каталог</a>
        <a href='/product/bmsy_440_dgh/'>BMSY 440 DGH</a>"""
        result = discover_catalog_page("beka_mak", "https://beka-mak.su/catalog/ruchnye/", page)
        self.assertEqual([item.expected_model for item in result.products], ["BMS-230DG", "BMSY-440DGH"])

    def test_rejects_tokens_and_non_pagination_queries(self):
        with self.assertRaises(ValueError):
            validate_catalog_url("https://intervesp.ru/catalog/x/?sessid=test", site="intervesp")
        with self.assertRaises(ValueError):
            validate_catalog_url("https://beka-mak.su/catalog/x/?filter=1", site="beka_mak")
        self.assertEqual(model_and_execution("BMSY-440DGH WP2"), ("BMSY-440DGH-WP2", "WP2"))

    def test_catalog_fetch_saves_only_sanitised_html_and_stops_challenge(self):
        evidence = RecordingEvidence()
        product_page = b"<html><script>BX.message({bitrix_sessid: 'test-value'})</script><a href='/product/bms_230_dg/'>BMS 230 DG</a></html>"
        response = HttpResponse("https://beka-mak.su/catalog/ruchnye/", 200, "text/html; charset=utf-8", product_page)
        result = fetch_catalog_page("beka_mak", response.final_url, evidence_dir=Path("."), client=FakeCatalogClient(response), evidence_store=evidence)
        self.assertEqual(result.status, CaptureStatus.SUCCESS)
        self.assertNotIn("test-value", evidence.saved[0]["body"].decode())
        blocked = HttpResponse(response.final_url, 200, "text/html", b"<h1>Checking your browser</h1><p>Verify you are human</p>")
        result = fetch_catalog_page("beka_mak", response.final_url, evidence_dir=Path("."), client=FakeCatalogClient(blocked), evidence_store=RecordingEvidence())
        self.assertEqual(result.status, CaptureStatus.BLOCKED)
