import unittest
from pathlib import Path

from universal_supplier.commercial_collection import (
    CandidateManifest, CandidateManifestMismatch, MAX_MANUAL_COLLECTION_LIMIT, SITE_SEEDS, discover_commercial_candidates,
    run_commercial_collection,
)
from universal_supplier.http_capture import HttpResponse


class FakeCollectionClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_validated(self, url, *, timeout_seconds, validator):
        validator(url)
        self.calls.append(("catalog", url))
        return self.pages[url]

    def get(self, url, *, timeout_seconds):
        self.calls.append(("product", url))
        return self.pages[url]


class RecordingRepository:
    def __init__(self):
        self.calls = []

    def persist_commercial_observation(self, product, capture):
        self.calls.append((product, capture))
        return {"observation_created": True, "exact_noop": False}


class MemoryEvidenceStore:
    def __init__(self):
        self.saved = []

    def save(self, **kwargs):
        self.saved.append(kwargs)
        return f"fixture://{kwargs['evidence_sha256']}"


class CommercialCollectionTests(unittest.TestCase):
    def test_manual_limit_rejects_values_above_ui_ceiling_before_network_io(self):
        with self.assertRaises(ValueError):
            run_commercial_collection(sites=("beka_mak",), limit=MAX_MANUAL_COLLECTION_LIMIT + 1,
                                      pause_seconds=0, evidence_dir=Path.cwd(), dry_run=True,
                                      client=FakeCollectionClient({}), sleep=lambda _: None)

    def test_dry_run_discovers_then_captures_without_persistence(self):
        fixture = (Path(__file__).with_name("fixtures") / "commercial" / "real" / "bekamak_bms_230dg_sanitized.html").read_bytes()
        first, second = SITE_SEEDS["beka_mak"]
        product_url = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/"
        pages = {
            first: HttpResponse(first, 200, "text/html", f"<a href='{product_url}'>BMS 230 DG</a>".encode()),
            # A duplicate link in another category remains one candidate and
            # is fetched only once in the same run.
            second: HttpResponse(second, 200, "text/html", f"<a href='{product_url}'>BMS 230 DG</a>".encode()),
            product_url: HttpResponse(product_url, 200, "text/html; charset=UTF-8", fixture),
        }
        pauses = []
        progress = []
        result = run_commercial_collection(sites=("beka_mak",), limit=1, pause_seconds=0,
                                           evidence_dir=Path.cwd(), dry_run=True, client=FakeCollectionClient(pages),
                                           sleep=pauses.append, evidence_store=MemoryEvidenceStore(),
                                           on_progress=lambda row, processed, total: progress.append((row.result, processed, total)))
        site = result.site_results[0]
        self.assertEqual((site.discovered_urls, site.catalog_pages_fetched, site.fetched_cards, site.numeric_prices), (1, 2, 1, 1))
        self.assertEqual(site.observations_created, 0)
        self.assertTrue(any(row.result == "DRY_RUN" for row in result.rows))
        self.assertEqual(pauses, [0, 0])
        self.assertEqual(progress, [("DRY_RUN", 1, 1)])

    def test_offset_is_stable_resume_boundary_and_skips_previous_candidate(self):
        first, second = SITE_SEEDS["beka_mak"]
        bms_230 = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/"
        bmsy_440 = "https://beka-mak.su/product/poluavtomaticheskiy_lentochnopilnyy_stanok_beka_mak_bmsy_440dgh/"
        fixture_root = Path(__file__).with_name("fixtures") / "commercial" / "real"
        pages = {
            first: HttpResponse(first, 200, "text/html", f"<a href='{bms_230}'>BMS 230 DG</a>".encode()),
            second: HttpResponse(second, 200, "text/html", f"<a href='{bmsy_440}'>BMSY 440 DGH</a>".encode()),
            bms_230: HttpResponse(bms_230, 200, "text/html", (fixture_root / "bekamak_bms_230dg_sanitized.html").read_bytes()),
            bmsy_440: HttpResponse(bmsy_440, 200, "text/html", (fixture_root / "bekamak_bmsy_440dgh_sanitized.html").read_bytes()),
        }
        client = FakeCollectionClient(pages)
        result = run_commercial_collection(sites=("beka_mak",), limit=1, candidate_offset=1, pause_seconds=0,
                                           evidence_dir=Path.cwd(), dry_run=True, client=client,
                                           sleep=lambda _: None, evidence_store=MemoryEvidenceStore())
        site = result.site_results[0]
        self.assertEqual((site.discovered_urls, site.candidate_offset, site.candidates_selected, site.product_attempts),
                         (2, 1, 1, 1))
        self.assertEqual([url for kind, url in client.calls if kind == "product"], [bmsy_440])

    def test_manifest_mismatch_stops_before_product_capture(self):
        first, second = SITE_SEEDS["beka_mak"]
        product_url = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/"
        client = FakeCollectionClient({
            first: HttpResponse(first, 200, "text/html", f"<a href='{product_url}'>BMS 230 DG</a>".encode()),
            second: HttpResponse(second, 200, "text/html", b""),
            product_url: HttpResponse(product_url, 200, "text/html", b"unused"),
        })
        different = CandidateManifest({"beka_mak": ()})
        with self.assertRaises(CandidateManifestMismatch):
            run_commercial_collection(sites=("beka_mak",), limit=1, pause_seconds=0,
                                      evidence_dir=Path.cwd(), dry_run=True, client=client, sleep=lambda _: None,
                                      evidence_store=MemoryEvidenceStore(), expected_manifest=different)
        self.assertFalse(any(kind == "product" for kind, _ in client.calls))

    def test_ambiguous_product_is_review_with_evidence_and_no_persistence(self):
        first, second = SITE_SEEDS["beka_mak"]
        product_url = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/"
        client = FakeCollectionClient({
            first: HttpResponse(first, 200, "text/html", f"<a href='{product_url}'>BMS 230 DG</a>".encode()),
            second: HttpResponse(second, 200, "text/html", b""),
            product_url: HttpResponse(product_url, 200, "text/html", b"<h1>BMS 270 DG</h1><div id='elPrice'>1 RUB</div>"),
        })
        repository = RecordingRepository()
        evidence = MemoryEvidenceStore()
        result = run_commercial_collection(sites=("beka_mak",), limit=1, pause_seconds=0,
                                           evidence_dir=Path.cwd(), dry_run=False, repository=repository,
                                           client=client, sleep=lambda _: None, evidence_store=evidence)
        site = result.site_results[0]
        self.assertEqual((site.fetched_cards, site.errors, site.reviews), (0, 0, 1))
        self.assertEqual(repository.calls, [])
        review = next(row for row in result.rows if row.result == "REVIEW")
        self.assertIsNotNone(review.evidence_ref)

    def test_discovery_manifest_has_stable_checksum_and_global_pacing(self):
        first, second = SITE_SEEDS["beka_mak"]
        product_url = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/"
        client = FakeCollectionClient({
            first: HttpResponse(first, 200, "text/html", f"<a href='{product_url}'>BMS 230 DG</a>".encode()),
            second: HttpResponse(second, 200, "text/html", b""),
        })
        pauses = []
        manifest, _ = discover_commercial_candidates(sites=("beka_mak",), evidence_dir=Path.cwd(),
                                                      pause_seconds=0, client=client, sleep=pauses.append,
                                                      evidence_store=MemoryEvidenceStore())
        serialized = manifest.as_jsonable()
        self.assertEqual(len(serialized["sites"]["beka_mak"]["sha256"]), 64)
        self.assertEqual(CandidateManifest.from_jsonable(serialized).as_jsonable(), serialized)
        self.assertEqual(pauses, [0])

    def test_blocked_catalog_stops_site_without_product_request(self):
        first, second = SITE_SEEDS["intervesp"]
        client = FakeCollectionClient({
            first: HttpResponse(first, 429, "text/html", b"slow down"),
            second: HttpResponse(second, 200, "text/html", b"ignored"),
        })
        result = run_commercial_collection(sites=("intervesp",), limit=1, pause_seconds=0,
                                           evidence_dir=Path.cwd(), dry_run=True, client=client, sleep=lambda _: None,
                                           evidence_store=MemoryEvidenceStore())
        site = result.site_results[0]
        self.assertTrue(site.blocked)
        self.assertEqual((site.catalog_pages_fetched, site.product_attempts, site.fetched_cards), (1, 0, 0))
        self.assertEqual(client.calls, [("catalog", first)])

    def test_explicit_catalog_page_limit_bounds_pagination_before_product_capture(self):
        first, second = SITE_SEEDS["beka_mak"]
        page_two = f"{first}?PAGEN_1=2"
        first_product = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/"
        second_product = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_270dg/"
        client = FakeCollectionClient({
            first: HttpResponse(first, 200, "text/html", (
                f"<a href='{first_product}'>BMS 230 DG</a><a href='?PAGEN_1=2'>2</a>"
            ).encode()),
            second: HttpResponse(second, 200, "text/html", b""),
            page_two: HttpResponse(page_two, 200, "text/html", f"<a href='{second_product}'>BMS 270 DG</a>".encode()),
            first_product: HttpResponse(first_product, 200, "text/html", b"<h1>BMS 230 DG</h1>"),
        })
        result = run_commercial_collection(sites=("beka_mak",), limit=1, pause_seconds=0,
                                           evidence_dir=Path.cwd(), dry_run=True, client=client,
                                           sleep=lambda _: None, evidence_store=MemoryEvidenceStore(),
                                           catalog_page_limit=1)
        site = result.site_results[0]
        self.assertEqual((site.catalog_pages_fetched, site.discovered_urls), (1, 1))
        self.assertNotIn(("catalog", page_two), client.calls)

    def test_catalog_page_limit_rejects_unbounded_value(self):
        with self.assertRaises(ValueError):
            run_commercial_collection(sites=("beka_mak",), limit=1, pause_seconds=0,
                                      evidence_dir=Path.cwd(), dry_run=True, client=FakeCollectionClient({}),
                                      sleep=lambda _: None, catalog_page_limit=21)

    def test_persistence_is_opt_in_and_returns_observation_count(self):
        fixture = (Path(__file__).with_name("fixtures") / "commercial" / "real" / "bekamak_bms_230dg_sanitized.html").read_bytes()
        first, second = SITE_SEEDS["beka_mak"]
        product_url = "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/"
        client = FakeCollectionClient({
            first: HttpResponse(first, 200, "text/html", f"<a href='{product_url}'>BMS 230 DG</a>".encode()),
            second: HttpResponse(second, 200, "text/html", b""),
            product_url: HttpResponse(product_url, 200, "text/html", fixture),
        })
        repository = RecordingRepository()
        result = run_commercial_collection(sites=("beka_mak",), limit=1, pause_seconds=0,
                                           evidence_dir=Path.cwd(), dry_run=False, repository=repository,
                                           client=client, sleep=lambda _: None, evidence_store=MemoryEvidenceStore())
        self.assertEqual(len(repository.calls), 1)
        self.assertEqual(result.site_results[0].observations_created, 1)
