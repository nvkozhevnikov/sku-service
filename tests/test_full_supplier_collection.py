import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from universal_supplier.full_supplier_collection import (
    FullCandidate, FullManifest, PublicPacer, _candidate_sha,
    discover_full_manifest, run_full_collection,
)
from scripts.summarize_working_v1 import summarize
from universal_supplier.http_capture import HttpResponse
from universal_supplier.http_capture import CaptureStatus
from scripts.run_full_supplier_qa import _validate_resume


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_validated(self, url, *, timeout_seconds, validator):
        validator(url)
        self.calls.append(url)
        response = self.responses[url]
        validator(response.final_url)
        return response


class FakeEvidence:
    def save(self, **kwargs):
        return "capture://www.bekamak.com/fixture.html"


def xml(*urls, index=False):
    entry = "sitemap" if index else "url"
    root = "sitemapindex" if index else "urlset"
    return ("<" + root + ">" + "".join(f"<{entry}><loc>{url}</loc></{entry}>" for url in urls)
            + "</" + root + ">").encode()


class FullSupplierCollectionTests(unittest.TestCase):
    def test_summary_rejects_unpinned_report_url(self):
        item = FullCandidate("beka_mak_tr", "https://www.bekamak.com/urun/bmso-460c/en", "BMSO-460C")
        manifest = FullManifest((item,), {}, (), _candidate_sha((item,)))
        report = {"manifest_sha256": manifest.sha256,
                  "rows": [{"source": "beka_mak_tr", "url": "https://www.bekamak.com/urun/other/en",
                            "status": "PERSISTED"}]}
        with self.assertRaisesRegex(ValueError, "unapproved candidate"):
            summarize(manifest, [report], {}, {})
    def test_pacer_waits_full_interval_after_slow_response(self):
        waits = []
        pacer = PublicPacer(sleep=waits.append)
        pacer.before_get()
        pacer.before_get()
        self.assertEqual(waits, [20.0])

    def test_full_sitemaps_are_distinct_and_exclude_project_links(self):
        sources = {
            "https://intervesp.ru/sitemap.xml": xml("https://intervesp.ru/sitemap-iblock-1.xml", index=True),
            "https://intervesp.ru/sitemap-iblock-1.xml": xml(
                "https://intervesp.ru/catalog/bandsaw/beka-mak-bmsy-440dgh-wp2/",
                "https://intervesp.ru/catalog/bandsaw/beka-mak-bmsy-440dgh-wp2/",
                "https://intervesp.ru/catalog/other/bmsy-440dgh/"),
            "https://beka-mak.su/sitemap.xml": xml("https://beka-mak.su/sitemap-iblock-18.xml", index=True),
            "https://beka-mak.su/sitemap-iblock-18.xml": xml(
                "https://beka-mak.su/product/bmsy_440_dgh/",
                "https://beka-mak.su/projects/bmsy_440_dgh/"),
            "https://www.bekamak.com/sitemap.xml": xml(
                "https://www.bekamak.com/urun/bmso-460c/en",
                "https://www.bekamak.com/urun/opus-300/en",
                "https://www.bekamak.com/urun/bmso-460c/tr"),
        }
        client = FakeClient({url: HttpResponse(url, 200, "text/xml", body) for url, body in sources.items()})
        pacer = PublicPacer(sleep=lambda seconds: None)
        evidence = []
        result = discover_full_manifest(client=client, pacer=pacer, evidence_log=evidence)
        self.assertEqual(len(result.candidates), 4)
        self.assertEqual({item.source for item in result.candidates}, {"intervesp", "beka_mak", "beka_mak_tr"})
        self.assertIn("BMSY-440DGH-WP2", {item.expected_model for item in result.candidates})
        self.assertIn("OPUS-300", {item.expected_model for item in result.candidates})
        self.assertEqual(result.sha256, _candidate_sha(result.candidates))
        self.assertEqual(len(client.calls), 5)
        self.assertEqual(len(evidence), 5)
        self.assertTrue(all(item["http_status"] == 200 and len(item["raw_sha256"]) == 64
                            for item in evidence))

    def test_manifest_checksum_cannot_be_bypassed(self):
        item = FullCandidate("beka_mak_tr", "https://www.bekamak.com/urun/bmso-460c/en", "BMSO-460C")
        manifest = FullManifest((item,), {}, (), "0" * 64)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            run_full_collection(manifest=manifest, repository=None, evidence_dir=Path("work/tmp"),
                                report_path=Path("work/tmp/fake-full-report.json"), dry_run=True)

    def test_429_blocks_source_before_any_write(self):
        item = FullCandidate("beka_mak_tr", "https://www.bekamak.com/urun/bmso-460c/en", "BMSO-460C")
        manifest = FullManifest((item,), {}, (), _candidate_sha((item,)))
        client = FakeClient({item.url: HttpResponse(item.url, 429, "text/html", b"rate limited")})
        with patch.object(Path, "write_text"), patch.object(Path, "replace"):
            result = run_full_collection(manifest=manifest, repository=None, evidence_dir=Path("work/tmp"),
                                         report_path=Path("work/tmp/fake-full-report.json"), dry_run=True,
                                         client=client, pacer=PublicPacer(sleep=lambda seconds: None),
                                         evidence_store=FakeEvidence())
        self.assertEqual(result["blocked_sources"], ["beka_mak_tr"])
        self.assertEqual(result["rows"][0]["status"], "BLOCKED")

    def test_resume_requires_pinned_completed_prefix(self):
        items = (
            FullCandidate("beka_mak", "https://beka-mak.su/product/bms_230_dg/", "BMS-230DG"),
            FullCandidate("beka_mak", "https://beka-mak.su/product/bmsy_440_dgh/", "BMSY-440DGH"),
        )
        manifest = FullManifest(items, {}, (), _candidate_sha(items))
        previous = {"manifest_sha256": manifest.sha256, "rows": [
            {"source": "beka_mak", "url": items[0].url,
             "expected_model": items[0].expected_model, "status": "PERSISTED"},
            {"source": "beka_mak", "url": items[1].url,
             "expected_model": items[1].expected_model, "status": "DB_ERROR"},
        ]}
        with patch.object(Path, "read_text", return_value=json.dumps(previous)):
            _validate_resume(manifest, "beka_mak", 1, Path("previous.json"))
            with self.assertRaisesRegex(ValueError, "incomplete"):
                _validate_resume(manifest, "beka_mak", 2, Path("previous.json"))
            with self.assertRaisesRegex(ValueError, "one source"):
                _validate_resume(manifest, "all", 1, Path("previous.json"))
            previous["manifest_sha256"] = "0" * 64
        with patch.object(Path, "read_text", return_value=json.dumps(previous)):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                _validate_resume(manifest, "beka_mak", 1, Path("previous.json"))

    def test_database_error_stops_all_sources_without_second_get(self):
        items = (
            FullCandidate("beka_mak", "https://beka-mak.su/product/bms_230_dg/", "BMS-230DG"),
            FullCandidate("intervesp", "https://intervesp.ru/catalog/a/beka-mak-bms-230dg/", "BMS-230DG"),
        )
        manifest = FullManifest(items, {}, (), _candidate_sha(items))
        captured = SimpleNamespace(status=CaptureStatus.SUCCESS, capture=object(), evidence_body=b"<html></html>",
                                   final_url=items[0].url, http_status=200, evidence_ref="capture://test/evidence.html",
                                   diagnostics=())
        product = SimpleNamespace(site_internal_id="19240", supplier_model="BMS-230DG",
                                  price=SimpleNamespace(state=SimpleNamespace(value="missing"), current_price=None,
                                                        currency=None), availability="unknown")
        repository = SimpleNamespace(persist_commercial_observation=lambda *_: (_ for _ in ()).throw(RuntimeError("DB gone")))
        with (patch("universal_supplier.full_supplier_collection.verify_qa_identity", return_value={}),
              patch("universal_supplier.full_supplier_collection.capture_public_html", return_value=captured) as getter,
              patch.dict("universal_supplier.full_supplier_collection._PARSERS", {"beka_mak": lambda *_a, **_k: product}),
              patch.object(Path, "write_text"), patch.object(Path, "replace")):
            result = run_full_collection(manifest=manifest, repository=repository,
                                         evidence_dir=Path("work/tmp"), report_path=Path("work/tmp/fake-report.json"),
                                         dry_run=False, pacer=PublicPacer(sleep=lambda _seconds: None))
        self.assertEqual(getter.call_count, 1)
        self.assertEqual(result["aborted_reason"], "database_error")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["status"], "DB_ERROR")


if __name__ == "__main__":
    unittest.main()
