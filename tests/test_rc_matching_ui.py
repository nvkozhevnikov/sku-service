from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from unittest.mock import patch

from scripts.run_local_rc_ui import LocalRcReadOnlyStore
from universal_supplier.control_plane.store import PostgresControlPlaneStore


def test_rc_matching_page_shows_status_filters_both_sides_and_evidence():
    template_root = Path(__file__).resolve().parents[1] / "universal_supplier" / "control_plane" / "templates"
    env = Environment(loader=FileSystemLoader(template_root), autoescape=select_autoescape(["html"]))
    body = env.get_template("commercial_proposals.html").render(
        url_for=lambda *_args, **_kwargs: "/static/file.css", current_user=None,
        safety_banner="import disabled", unavailable="", active="commercial_qa",
        proposals={"registry_sha256": "a" * 64, "manifest_sha256": "b" * 64, "sha256": "c" * 64,
                   "counts": {"existing": 1, "new": 1, "review": 1, "review_only": 0, "conflict": 1},
                   "tables": {"existing": [{"selected_source_proposed": "beka_mak", "source_external_id": "19240",
                                            "model": "BMS 230 DG", "source_url": "https://beka-mak.su/product/example/",
                                            "sterbrust_product_id": "18077", "sterbrust_name": "Sterbrust BMS 230 DG",
                                            "price": "772191", "currency": "RUB", "availability": "in_stock",
                                            "observed_at": "2026-09-30T00:00:00+00:00", "all_source_offers": []}],
                              "new": [{"new_candidate_id": "NEWC-EXAMPLE", "source": "intervesp",
                                       "external_id": "1", "source_url": "https://intervesp.ru/catalog/example/",
                                       "model": "BMSX", "execution": "BMSX-WP2", "section_id": 4004,
                                       "section_path": "Станки / Ленточнопильные"}],
                              "review": [{"source": "intervesp", "external_id": "2", "model": "BMSY-440DGH-WP2",
                                          "source_url": "https://intervesp.ru/catalog/example2/",
                                          "export_status": "CONFLICT", "export_reason": "execution_mismatch",
                                          "proposed_sterbrust_id": "18077", "match_status": "CONFLICT",
                                          "match_conflicts": ["WP2 != base"]}]}})
    assert 'value="existing"' in body and 'value="new"' in body
    assert 'value="review"' in body and 'value="conflict"' in body
    assert "beka_mak" in body and "Sterbrust BMS 230 DG" in body
    assert "NEWC-EXAMPLE" in body and "SECTION_ID 4004" in body
    assert "WP2 != base" in body and "Предполагаемый ID 18077" in body


def test_rc_dashboard_labels_snapshot_and_passive_sources_truthfully():
    rows = [{"code": "partner_st"}, {"code": "optimum"},
            {"code": "beka_mak"}, {"code": "beka_mak_tr"}]
    with patch.object(PostgresControlPlaneStore, "dashboard", return_value=rows):
        labelled = object.__new__(LocalRcReadOnlyStore).dashboard()
    assert all("55449" in row["data_source"] for row in labelled)
    assert all("исторический снимок" in row["source_reason"] for row in labelled[:2])
    assert "справочный источник" in labelled[-1]["source_reason"]
    with patch.object(PostgresControlPlaneStore, "dashboard", return_value=[{"code": "partner_st"}]):
        detail = object.__new__(LocalRcReadOnlyStore).suppliers()[0]
    assert "исторический снимок" in detail["source_reason"]
    template = (Path(__file__).resolve().parents[1] / "universal_supplier" / "control_plane"
                / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "товаров источника" in template
    assert "активных товаров" not in template
    assert "не включают отдельные диагностические предложения" in template
    assert 'href="/commercial-qa/proposals"' in template
