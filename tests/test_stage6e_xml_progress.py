from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from pathlib import Path

from universal_supplier.adapters.optimum import _collect_product_batch
from universal_supplier.control_plane.progress import CrawlProgressTracker, ProgressClock, format_eta_ru
from universal_supplier.xml_export import generate_canonical_xml, iter_full_xml

from test_stage5 import row, snapshot


class FakeClock:
    def __init__(self):
        self.value = 0.0
    def monotonic(self):
        return self.value
    def now(self):
        return datetime(2026, 9, 17, tzinfo=timezone.utc)


def tracker(clock=None, **kwargs):
    persisted = []
    value = CrawlProgressTracker(persisted.append, clock=ProgressClock(clock.monotonic, clock.now) if clock else None,
                                 **kwargs)
    return value, persisted


def test_discovery_is_indeterminate_until_total_known():
    progress, persisted = tracker()
    progress.callback("DISCOVERY")
    assert persisted[-1]["total"] is None and persisted[-1]["percent"] is None
    progress.callback("FETCHING_PRODUCTS", total=100)
    assert persisted[-1]["total"] == 100 and persisted[-1]["percent"] == 0


def test_concurrent_one_hundred_completions_are_not_lost():
    progress, _ = tracker(persist_interval=9999, persist_every=25)
    progress.callback("FETCHING_PRODUCTS", total=100)
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda number: progress.callback("PRODUCT_COMPLETED", success=number % 4 != 0), range(100)))
    final = progress.finish("succeeded")
    assert (final["processed"], final["succeeded"], final["errors"], final["percent"]) == (100, 75, 25, 100.0)


def test_eta_and_rate_use_injected_clock():
    clock = FakeClock()
    progress, _ = tracker(clock, persist_interval=0)
    progress.callback("FETCHING_PRODUCTS", total=10)
    clock.value = 2
    snap = progress.callback("PRODUCT_COMPLETED", success=True)
    assert snap["rate_per_second"] == 0.5 and snap["eta_seconds"] == 18


def test_eta_excludes_slow_discovery_time_and_uses_fetch_evidence_only():
    clock = FakeClock()
    progress, _ = tracker(clock, persist_interval=0)
    progress.callback("DISCOVERY")
    clock.value = 100
    progress.callback("FETCHING_PRODUCTS", total=100)
    clock.value = 110
    snap = progress.callback("PRODUCT_COMPLETED", success=True)
    assert snap["elapsed_seconds"] == 110
    assert snap["fetch_elapsed_seconds"] == 10
    assert snap["rate_per_second"] == 0.1
    assert snap["eta_seconds"] == 990


def test_duplicate_fetch_callback_does_not_reset_fetch_clock():
    clock = FakeClock()
    progress, _ = tracker(clock, persist_interval=0)
    progress.callback("FETCHING_PRODUCTS", total=10)
    clock.value = 5
    progress.callback("FETCHING_PRODUCTS", total=10)
    clock.value = 10
    snap = progress.callback("PRODUCT_COMPLETED", success=True)
    assert snap["rate_per_second"] == 0.1 and snap["eta_seconds"] == 90


def test_fresh_tracker_does_not_invent_rate_or_eta_after_restart():
    clock = FakeClock()
    progress, _ = tracker(clock, persist_interval=0)
    clock.value = 500
    snap = progress.callback("FETCHING_PRODUCTS", total=50)
    assert snap["rate_per_second"] is None and snap["eta_seconds"] is None


def test_russian_eta_formatting():
    assert format_eta_ru(35) == "Осталось примерно 35 сек"
    assert format_eta_ru(130) == "Осталось примерно 2 мин 10 сек"
    assert format_eta_ru(3900) == "Осталось примерно 1 ч 05 мин"


def test_post_fetch_phase_has_no_fake_eta():
    clock = FakeClock()
    progress, _ = tracker(clock, persist_interval=0)
    progress.callback("FETCHING_PRODUCTS", total=10)
    clock.value = 1
    progress.callback("PRODUCT_COMPLETED", success=True)
    assert progress.set_phase("INGESTING")["eta_seconds"] is None


def test_failure_retains_last_real_counts():
    progress, persisted = tracker(persist_interval=9999)
    progress.callback("FETCHING_PRODUCTS", total=10)
    progress.callback("PRODUCT_COMPLETED", success=False)
    final = progress.finish("failed")
    assert final["processed"] == 1 and final["errors"] == 1 and persisted[-1] == final


def test_throttle_and_final_forced_persistence():
    progress, persisted = tracker(persist_interval=9999, persist_every=25)
    progress.callback("FETCHING_PRODUCTS", total=100)
    for _ in range(24):
        progress.callback("PRODUCT_COMPLETED", success=True)
    assert len(persisted) == 1
    progress.callback("PRODUCT_COMPLETED", success=True)
    assert len(persisted) == 2
    progress.finish("partial")
    assert len(persisted) == 3 and persisted[-1]["status"] == "partial"


def test_optimum_batch_reports_every_settled_url():
    class Adapter:
        async def _one(self, value):
            return {"classification": "FETCH_ERROR" if value == 2 else "PRODUCT"}
    events = []
    rows, stopped = asyncio.run(_collect_product_batch(Adapter(), [1, 2, 3], lambda event, **values: events.append((event, values))))
    assert not stopped and len(rows) == 3 and len(events) == 3
    assert sum(values["success"] for _, values in events) == 2


def test_partner_live_loop_reports_discovery_total_and_every_attempt():
    from test_stage6c1_partner import FakeAdapter, FakeResponse, FakeSession, make_transport, policy
    from universal_supplier.crawl import run_partner_st_live
    events = []
    result = run_partner_st_live(
        baseline=None, rate_policy=policy(max_concurrency=2, min_request_interval_seconds=0),
        transport_factory=make_transport(FakeSession([FakeResponse(), FakeResponse()])),
        adapter=FakeAdapter(2), progress_callback=lambda event, **values: events.append((event, values)),
    )
    assert len(result.cards) == 2
    assert events[0][0] == "DISCOVERY" and events[1] == ("FETCHING_PRODUCTS", {"total": 2})
    assert events[2:] == [("PRODUCT_COMPLETED", {"success": True})] * 2


def test_full_xml_has_distinct_sections_and_alias_only_for_matched():
    rows = [
        {"supplier_code": "partner_st", "external_id": "297", "source_product_id": 1,
         "sku": "PP-800F", "name": "PP-800F", "proposed_sterbrust_id": "88585",
         "confirmed_sterbrust_id": "88585", "decision": "EXACT_EXISTING"},
        {"supplier_code": "optimum", "external_id": "unmatched", "source_product_id": 2,
         "sku": "X", "name": "Unknown", "decision": "INSUFFICIENT_IDENTITY"},
    ]
    root = ET.fromstring(b"".join(iter_full_xml(rows)))
    products = root.findall(".//product")
    for section in ("identity", "matching", "commercial", "content", "properties", "media", "relations", "options", "provenance"):
        assert products[0].find(section) is not None
    assert products[0].findtext("identity/sterbrust_product_id") == "88585"
    assert products[0].findtext("identity/stb_1c_bitrix_id") == "88585"
    assert products[1].find("identity/stb_1c_bitrix_id") is None


def test_review_candidate_is_proposed_and_never_an_identity_alias():
    row = {"supplier_code": "partner_st", "external_id": "review", "source_product_id": 1,
           "decision": "REVIEW_EXISTING", "proposed_sterbrust_id": "123"}
    product = ET.fromstring(b"".join(iter_full_xml([row]))).find(".//product")
    assert product.find("identity/sterbrust_product_id") is None
    assert product.find("identity/stb_1c_bitrix_id") is None
    assert product.findtext("matching/proposed_sterbrust_id") == "123"


def test_manual_confirmed_mapping_emits_both_equal_identity_aliases():
    row = {"supplier_code": "optimum", "external_id": "manual", "source_product_id": 1,
           "decision": "REVIEW_EXISTING", "proposed_sterbrust_id": "candidate",
           "confirmed_sterbrust_id": "manual-500", "confirmed_link_source": "MANUAL_CONFIRMED"}
    product = ET.fromstring(b"".join(iter_full_xml([row]))).find(".//product")
    assert product.findtext("identity/sterbrust_product_id") == "manual-500"
    assert product.findtext("identity/stb_1c_bitrix_id") == "manual-500"
    assert product.find("matching/proposed_sterbrust_id") is None


def test_generic_best_candidate_can_never_be_promoted_to_confirmed_alias():
    row = {"supplier_code": "partner_st", "external_id": "legacy-review", "source_product_id": 1,
           "decision": "REVIEW_EXISTING", "best_sterbrust_id": "123"}
    product = ET.fromstring(b"".join(iter_full_xml([row]))).find(".//product")
    assert product.find("identity/sterbrust_product_id") is None
    assert product.find("identity/stb_1c_bitrix_id") is None


def test_conflict_and_safe_new_candidates_never_emit_aliases():
    rows = [
        {"supplier_code": "partner_st", "external_id": decision, "source_product_id": number,
         "decision": decision, "proposed_sterbrust_id": "123"}
        for number, decision in enumerate(("CONFLICT", "SAFE_NEW_PRODUCT_CANDIDATE"), 1)
    ]
    products = ET.fromstring(b"".join(iter_full_xml(rows))).findall(".//product")
    assert all(product.find("identity/sterbrust_product_id") is None for product in products)
    assert all(product.find("identity/stb_1c_bitrix_id") is None for product in products)


def test_full_xml_is_deterministic_for_same_rows():
    rows = [{"supplier_code": "optimum", "external_id": "1", "source_product_id": 1,
             "identity_characteristics": {"z": 1, "a": 2}}]
    assert b"".join(iter_full_xml(rows)) == b"".join(iter_full_xml(rows))


def test_short_xml_pp800f_uses_sterbrust_id_and_supplier_sku():
    artifact = generate_canonical_xml(snapshot(row()))
    offer = ET.fromstring(artifact.data).find(".//offer")
    assert offer.get("id") == "88585" and offer.findtext("vendorCode") == "508002"
    assert offer.findtext("vendorCode") != "297"


def test_short_xml_matches_operator_sample_offer_shape():
    sample = ET.parse(Path(__file__).parent / "fixtures" / "short_xml_operator_sample.xml").getroot()
    expected = {node.tag for node in sample.find(".//offer")}
    actual = {node.tag for node in ET.fromstring(generate_canonical_xml(snapshot(row())).data).find(".//offer")}
    assert {"url", "price", "currencyId", "categoryId", "name", "vendor", "vendorCode"} <= expected
    assert actual <= expected | {"oldprice", "param", "stb_1c_bitrix_id"}


def test_full_xml_streams_in_multiple_chunks():
    chunks = list(iter_full_xml({"supplier_code": "optimum", "external_id": str(i), "source_product_id": i} for i in range(450)))
    assert len(chunks) > 450 and len(ET.fromstring(b"".join(chunks)).findall(".//product")) == 450


def test_viewer_can_read_sanitized_progress_api():
    from test_stage6e import make_client
    client, _ = make_client(role="VIEWER")
    response = client.get("/api/runs/1/progress")
    assert response.status_code == 200
    assert response.json()["processed"] == 25 and response.json()["phase_label_ru"] == "Загрузка товаров"
    assert not ({"password", "token", "settings_snapshot"} & response.json().keys())


def test_progress_templates_compile():
    from jinja2 import Environment, FileSystemLoader
    templates = Path(__file__).parents[1] / "universal_supplier" / "control_plane" / "templates"
    environment = Environment(loader=FileSystemLoader(templates))
    environment.filters["ru"] = str
    environment.globals["url_for"] = lambda *args, **kwargs: "/static"
    environment.get_template("run_detail.html")
    environment.get_template("runs.html")
    environment.get_template("supplier_detail.html")


def test_initial_discovery_progress_bar_is_indeterminate():
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader
    templates = Path(__file__).parents[1] / "universal_supplier" / "control_plane" / "templates"
    environment = Environment(loader=FileSystemLoader(templates))
    environment.filters["ru"] = str
    environment.globals["url_for"] = lambda *args, **kwargs: "/static"
    item = SimpleNamespace(id=1, supplier_name="QA", started_at="now", finished_at=None,
                           status="running", metadata={"progress": {"phase_label_ru": "Поиск товаров",
                           "percent": None, "processed": 0, "total": None}}, discovered_count=0,
                           fetched_count=0, new_count=0, changed_count=0, error_count=0, errors=[])
    rendered = environment.get_template("run_detail.html").render(item=item, current_user=None)
    progress_tag = rendered.split("<progress", 1)[1].split(">", 1)[0]
    assert "value=" not in progress_tag
