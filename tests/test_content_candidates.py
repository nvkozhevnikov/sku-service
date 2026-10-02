import unittest

from universal_supplier.content_candidates import (
    CandidateVerificationStatus,
    ProductContentCandidate,
    ProductDocumentCandidate,
    ProductMediaCandidate,
    candidate_is_ready_for_field_resolution,
)


class ContentCandidateTests(unittest.TestCase):
    def test_content_candidate_needs_source_and_verification_metadata(self):
        candidate = ProductContentCandidate(
            source_url="https://www.bekamak.com/urun/bms-230-dg/",
            source_site="bekamak.com",
            model="BMS-230DG",
            execution="base",
            evidence_ref="capture:official-bms-230dg:2026-09-24",
            verification_status=CandidateVerificationStatus.VERIFIED_SOURCE,
            field="description",
            raw_value="Manual double miter cutting pivot type bandsaw machine",
            language="en",
        )
        self.assertTrue(candidate_is_ready_for_field_resolution(candidate))

    def test_conflicting_value_is_not_silently_selectable(self):
        candidate = ProductContentCandidate(
            source_url="https://www.bekamak.com/urun/bms-230-dg/ru",
            source_site="bekamak.com",
            model="BMS-230DG",
            execution="base",
            evidence_ref="search-index:official-ru:2026-09-24",
            verification_status=CandidateVerificationStatus.CONFLICT,
            field="working_height",
            raw_value="760",
            language="ru",
            conflict_group="bms-230dg-working-height",
        )
        self.assertFalse(candidate_is_ready_for_field_resolution(candidate))

    def test_media_and_document_preserve_provenance_and_model_scope(self):
        media = ProductMediaCandidate(
            source_url="https://intervesp.ru/example/",
            source_site="intervesp.ru",
            model="BMSY-440DGH",
            execution="WP2",
            evidence_ref="fixture:intervesp-wp2:2026-09-23",
            verification_status=CandidateVerificationStatus.REVIEW,
            media_url="https://intervesp.ru/upload/example.jpg",
            media_role="product_photo",
            visible_machine=True,
            watermark_present=None,
        )
        document = ProductDocumentCandidate(
            source_url="https://www.bekamak.com/e-katalog/en",
            source_site="bekamak.com",
            model="BMSY-440DGH",
            execution="base",
            evidence_ref="catalog:bekamak-2025",
            verification_status=CandidateVerificationStatus.VERIFIED_SOURCE,
            title="Bekamak Catalog 2025",
            document_type="catalog",
            language="en",
            applicability="model family; base execution must be checked per table",
            document_url="https://www.bekamak.com/images/ekatalog_diger_diller/bekamak-catalog-2025.pdf",
        )
        self.assertEqual(media.execution, "WP2")
        self.assertFalse(document.confirmed_model_binding)
