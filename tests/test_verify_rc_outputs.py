import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts.verify_rc_outputs import FILES, verify


class VerifyRcOutputsTests(unittest.TestCase):
    def _fixture(self, base: Path) -> None:
        final = base / "FINAL_PROPOSALS"
        final.mkdir()
        proof = {"verified": True, "manifest_sha256": "a" * 64,
                 "source_counts": {code: {"source_products": count} for code, count in
                                   (("intervesp", 1), ("beka_mak", 0), ("beka_mak_tr", 0))}}
        (base / "RUN2_VERIFIED.json").write_text(json.dumps(proof), encoding="utf-8")
        (base / "MATCHING_FINAL.json").write_text(json.dumps({"manifest_sha256": "a" * 64,
                                                            "rows": [{}]}), encoding="utf-8")
        (final / "PROPOSAL_TABLES.json").write_text(json.dumps({"manifest_sha256": "a" * 64,
            "diagnostic_only": True, "esol_payload": False,
            "tables": {"existing": [], "new": [], "review": []}}), encoding="utf-8")
        qa_xml = base / "DIAGNOSTIC_QA.xml"
        qa_xml.write_bytes(b"<diagnostic />")
        (base / "DIAGNOSTIC_QA.summary.json").write_text(json.dumps({
            "diagnostic_only": True, "products": 1,
            "sha256": hashlib.sha256(qa_xml.read_bytes()).hexdigest()}), encoding="utf-8")
        xml = (b'<?xml version="1.0" encoding="utf-8"?>\n'
               b'<UniversalSupplierProposal diagnosticOnly="true" esolPayload="false" />')
        for name in FILES:
            path = final / name
            if name.endswith(".xml"):
                path.write_bytes(xml)
            elif name.endswith(".xlsx"):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("[Content_Types].xml", "<Types />")
        with (final / "MATCHING_REPORT.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["source"])
            writer.writeheader()
            writer.writerow({"source": "intervesp"})

    def test_coherent_diagnostic_files_pass(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            self._fixture(base)
            self.assertTrue(verify(base=base)["verified"])

    def test_manifest_mismatch_fails_closed(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            self._fixture(base)
            path = base / "FINAL_PROPOSALS" / "PROPOSAL_TABLES.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["manifest_sha256"] = "b" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "verified diagnostic RUN2 basis"):
                verify(base=base)


if __name__ == "__main__":
    unittest.main()
