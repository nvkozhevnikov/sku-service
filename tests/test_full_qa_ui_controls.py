from pathlib import Path
import json
from threading import Lock
from unittest.mock import patch
import unittest
from jinja2 import Environment, FileSystemLoader

from universal_supplier.commercial_collection import CandidateManifest
from universal_supplier.commercial_discovery import DiscoveredCommercialProduct
from universal_supplier.control_plane.commercial_qa import CommercialQaService
from universal_supplier.full_supplier_collection import FullCandidate, FullManifest, _candidate_sha


class NoStartThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


def service_with_full_manifest(count=120):
    service = object.__new__(CommercialQaService)
    candidates = tuple(FullCandidate("beka_mak_tr", f"https://www.bekamak.com/urun/bmso-{i}c/en",
                                     f"BMSO-{i}C") for i in range(100, 100 + count))
    service.full_manifest = FullManifest(candidates, {}, (), _candidate_sha(candidates))
    service.manifest = CandidateManifest({
        "intervesp": (DiscoveredCommercialProduct("intervesp", "https://intervesp.ru/catalog/x/bms-230dg/",
                                                  "BMS-230DG", None, ""),),
        "beka_mak": (DiscoveredCommercialProduct("beka_mak", "https://beka-mak.su/product/bms_230_dg/",
                                                 "BMS-230DG", None, ""),),
    })
    service.write_repository_factory = lambda: object()
    service._preflight_write_target = lambda source: None
    service._record_event = lambda run: None
    service._runs = {}
    service._lock = Lock()
    return service


class FullQaUiControlsTests(unittest.TestCase):
    def test_full_manifest_above_old_100_cap_requires_explicit_write_confirmation(self):
        service = service_with_full_manifest()
        with self.assertRaisesRegex(ValueError, "подтверждение"):
            service.start("beka_mak_tr", 1, False, full_scope=True, full_manifest=True)
        with patch("universal_supplier.control_plane.commercial_qa.Thread", NoStartThread):
            run = service.start("beka_mak_tr", 1, False, full_scope=True, full_manifest=True,
                                write_confirmed=True)
        self.assertEqual(run.limit, 120)
        self.assertEqual(run.scope, "full_sitemap")
        self.assertEqual(run.status, "queued")
        with self.assertRaisesRegex(ValueError, "Уже выполняется"):
            service.start("beka_mak_tr", 1, False, full_scope=True, write_confirmed=True)

    def test_bounded_dry_run_keeps_three_card_limit(self):
        service = service_with_full_manifest()
        with self.assertRaisesRegex(ValueError, "Лимит"):
            service.start("intervesp", 4, True)

    def test_full_scope_dry_run_can_use_entire_manifest_without_database(self):
        service = service_with_full_manifest()
        with patch("universal_supplier.control_plane.commercial_qa.Thread", NoStartThread):
            run = service.start("beka_mak_tr", 1, True, full_scope=True, full_manifest=True)
        self.assertEqual(run.limit, 120)
        self.assertEqual(run.mode, "dry_run")

    def test_all_three_sources_are_one_manual_job(self):
        service = service_with_full_manifest()
        with patch("universal_supplier.control_plane.commercial_qa.Thread", NoStartThread):
            run = service.start("all", 1, True, full_scope=True, full_manifest=True)
        self.assertEqual(run.limit, 120)
        self.assertEqual(run.source, "all")
        self.assertEqual(run.scope, "full_sitemap")

    def test_full_run_template_shows_persisted_review_and_http_error(self):
        template_dir = Path(__file__).resolve().parents[1] / "universal_supplier" / "control_plane" / "templates"
        environment = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
        run = {"id": "test", "source": "beka_mak", "mode": "qa_write", "target": "stage4_commercial_qa",
               "status": "partial", "processed": 3, "total": 3, "manifest_sha256": "a" * 64,
               "rows": [
                   {"status": "PERSISTED", "model": "BMS-230DG", "price": "772191", "currency": "RUB",
                    "availability": "in_stock", "url": "https://beka-mak.su/product/one/"},
                   {"status": "AMBIGUOUS", "url": "https://beka-mak.su/product/two/",
                    "expected_model": "BMSY-320DGH", "diagnostics": ["title_conflict"]},
                   {"status": "HTTP_STATUS", "url": "https://beka-mak.su/product/three/",
                    "http_status": 404, "diagnostics": ["http_404"]}],
               "site_summary": None, "counts_before": None, "xml_sha256": "", "report_path": ""}
        body = environment.get_template("commercial_qa_run.html").render(
            run=run, current_user=None, url_for=lambda *_a, **_k: "/static/app.css", safety_banner="QA")
        self.assertIn("BMS-230DG", body)
        self.assertIn("title_conflict", body)
        self.assertIn("HTTP_STATUS", body)

    def test_proposal_view_requires_diagnostic_payload_and_fixed_download_name(self):
        service = service_with_full_manifest()
        service.proposals_path = Path("reports/PROPOSAL_TABLES.json")
        payload = {"diagnostic_only": True, "esol_payload": False, "manifest_sha256": "a" * 64,
                   "registry_sha256": "b" * 64, "tables": {"existing": [], "new": [], "review": []}}
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_bytes", return_value=json.dumps(payload).encode()):
            self.assertEqual(service.proposals()["counts"],
                             {"existing": 0, "new": 0, "review": 0, "conflict": 0, "review_only": 0})
            with self.assertRaisesRegex(Exception, "разрешённый набор"):
                service.proposal_artifact("../secret.txt")
        payload["artifact_dir"] = "../outside"
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_bytes", return_value=json.dumps(payload).encode()):
            with self.assertRaisesRegex(Exception, "Путь версии"):
                service.proposals()


if __name__ == "__main__":
    unittest.main()
