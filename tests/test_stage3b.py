import json
import inspect
import sys
import unittest
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from universal_supplier.adapters.partner_st import PartnerStAdapter, normalize_availability, normalize_currency, parse_decimal
from universal_supplier.http import HttpTransport, RetryPolicy
from universal_supplier.models import FetchRecord
from universal_supplier.repository import InMemoryRepository, IngestCounts
from universal_supplier.state import discovery_health, next_missing_state, source_active_for_availability
from universal_supplier.postgres import (PostgresConfig, PostgresRepository,
                                         decision_data_quality_flags,
                                         should_quarantine_automatic_link)
from universal_supplier.change_detection import card_group_values, card_hashes
from scripts.qa_stage3b import integration_evidence_valid, live_diff_evidence_valid
from scripts.generate_stage3b_reports import resolved_postgres_report
from scripts.run_stage3b_postgres_integration import (INTEGRITY_SQL_FILES,
                                                      main as integration_main,
                                                      read_psycopg_sql, run_one,
                                                      verify_after_reopen)


PRODUCT_HTML = """
<html><head><link rel="canonical" href="https://partner-st.ru/tools/partner-test-100/"/>
<meta property="og:image" content="/images/test.webp"/>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Home","item":"https://partner-st.ru/"},{"@type":"ListItem","position":2,"name":"Tools","item":"https://partner-st.ru/tools/"},{"@type":"ListItem","position":3,"name":"Partner TEST-100","item":"https://partner-st.ru/tools/partner-test-100/"}]}</script>
<script type="application/ld+json">{"@context":"https://schema.org/","@type":"Product","name":"Partner TEST-100 Machine","sku":"SKU-100","description":"Description","image":"https://partner-st.ru/images/test.webp","offers":{"@type":"Offer","price":"1234.50","priceCurrency":"RUB","availability":"https://schema.org/InStock"}}</script>
</head><body><h1>Partner TEST-100 Machine</h1>
<input name="product_data[100][product_id]" value="100"/>
<input name="product_data[100][amount]" value="1" max="7"/>
<div id="old_price_update_100"><span class="ty-list-price">1 500,00 ₽</span></div>
<div class="ty-product-feature"><span class="ty-product-feature__label">Мощность, кВт:</span><span class="ty-product-feature__value">7,5</span></div>
<a class="cm-image-previewer" href="/images/test-2.webp">image</a>
<a href="/files/manual.pdf">Manual</a><iframe src="https://video.example/embed/1" title="Demo"></iframe>
<div class="ut2-pb__options"><div class="ty-product-options__item">
<label class="ty-product-options__item-label">Размер:</label>
<input id="self" data-ca-variant-id="v1" data-ca-product-url="https://partner-st.ru/tools/partner-test-100/" checked value="v1"/>
<label for="self">100</label>
<input id="other" data-ca-variant-id="v2" data-ca-product-url="https://partner-st.ru/tools/partner-test-200/" value="v2"/>
<label for="other">200</label></div></div>
</body></html>
"""


def valid_pass_evidence():
    return {
        "state": "PASS", "select_1": True, "runtime_role_superuser": False,
        "postgres_external": True, "postgres_in_docker": False,
        "migrations_001_010": "PASS", "migration_011_optional_sterbrust_url": "PASS",
        "integrity_tests": "PASS",
        "connection_autocommit": True, "credentials_recorded": False,
        "server_version": "17.1", "integration_timestamp": "2026-01-01T00:00:00Z",
        "db_counts_after_run2": {"crawl_runs": 2},
        "post_close_verification": "PASS", "post_close_select_1": "PASS",
        "post_close_db_counts": {"crawl_runs": 2, "crawl_run_items": 2444,
                                 "source_products": 1222, "offers": 1222,
                                 "entity_change_history": 0, "product_matches": 1336},
        "post_close_assertions": {"DUPLICATE_STABLE_EXTERNAL_IDS": 0},
        "post_close_crawl_runs": 2, "post_close_run2_crawl_run_items": 1222,
        "post_close_partner_source_products": 1222, "post_close_partner_default_offers": 1222,
        "post_close_accepted_catalog_links": 114,
        "post_close_current_matches": 1222,
        "post_close_pp800f_persistent_mapping_stable": "PASS",
        "post_close_pp800f_run1_accepted_match_history": "PASS",
        "post_close_mrx3_mrx4_conflict_preserved": "PASS",
    }


class FakeResponse:
    def __init__(self, status_code=200, text="ok", url="https://example.test/final", history=()):
        self.status_code, self.text, self.url, self.history = status_code, text, url, list(history)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.trust_env = True
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FixtureTransport:
    def __init__(self, values):
        self.values = values

    def get(self, url):
        return FetchRecord(url, url, 200, (), 1, 1, self.values[url])


class RecordingCursor:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []

    def execute(self, query, params=None):
        self.calls.append((" ".join(query.split()), params))

    def fetchall(self):
        return self.rows


class ResultCursor:
    def __init__(self, values):
        self.values = iter(values)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        pass

    def fetchone(self):
        return (next(self.values),)


class CursorSequenceConnection:
    def __init__(self, cursor_values):
        self.cursor_values = iter(cursor_values)

    def cursor(self):
        return ResultCursor(next(self.cursor_values))


class Stage3BTests(unittest.TestCase):
    def setUp(self):
        fetch = FetchRecord("https://partner-st.ru/requested", "https://partner-st.ru/tools/partner-test-100/", 200, (), 1, 10, PRODUCT_HTML)
        self.card = PartnerStAdapter().parse_product(fetch)

    def test_partner_st_product_id_extraction(self):
        self.assertEqual(self.card.external_id, "100")

    def test_stable_external_identity(self):
        self.assertEqual((self.card.supplier_code, self.card.external_id), ("partner_st", "100"))

    def test_repeated_source_product_upsert(self):
        repo = InMemoryRepository()
        repo.ingest([self.card], "2026-01-01T00:00:00Z")
        second = repo.ingest([self.card], "2026-01-02T00:00:00Z")
        self.assertEqual((len(repo.state.products), second.unchanged_products), (1, 1))
        self.assertEqual(repo.state.products[("partner_st", "100")]["first_seen_at"], "2026-01-01T00:00:00Z")

    def test_repeated_default_offer_upsert(self):
        repo = InMemoryRepository()
        repo.ingest([self.card], "1")
        second = repo.ingest([self.card], "2")
        self.assertEqual((len(repo.state.offers), second.unchanged_offers), (1, 1))

    def test_one_card_one_offer(self):
        repo = InMemoryRepository()
        first = repo.ingest([self.card], "1")
        self.assertEqual((first.new_products, first.new_offers), (1, 1))

    def test_option_navigation_does_not_create_variant(self):
        value = self.card.options[0].values[1]
        self.assertEqual(value.classification, "NAVIGATION_TO_PRODUCT")
        self.assertNotEqual(value.classification, "TRUE_VARIANT")

    def test_informational_selected_option(self):
        self.assertEqual(self.card.options[0].values[0].classification, "INFORMATIONAL")

    def test_price_parsing(self):
        self.assertEqual(self.card.price, Decimal("1234.50"))

    def test_old_price_parsing(self):
        self.assertEqual(self.card.old_price, Decimal("1500.00"))

    def test_rub_currency_normalization(self):
        self.assertEqual(normalize_currency("₽"), "RUB")
        self.assertEqual(self.card.currency, "RUB")

    def test_quantity_parsing(self):
        self.assertEqual(self.card.quantity, Decimal("7"))

    def test_availability_normalization(self):
        self.assertEqual(self.card.availability_normalized, "in_stock")

    def test_canonical_url_parsing(self):
        self.assertEqual(self.card.canonical_url, "https://partner-st.ru/tools/partner-test-100/")

    def test_product_properties_parsing(self):
        self.assertEqual((self.card.properties[0].name, self.card.properties[0].value), ("Мощность, кВт", "7,5"))

    def test_image_extraction(self):
        self.assertEqual(sum(item.media_type == "image" for item in self.card.media), 2)

    def test_document_extraction(self):
        self.assertTrue(any(item.media_type == "document" and item.url.endswith("manual.pdf") for item in self.card.media))

    def test_dynamic_sitemap_discovery(self):
        values = {
            "https://partner-st.ru/sitemap.xml": "<sitemapindex><sitemap><loc>https://partner-st.ru/products_1.xml</loc></sitemap><sitemap><loc>https://partner-st.ru/products_2.xml</loc></sitemap></sitemapindex>",
            "https://partner-st.ru/products_1.xml": "<urlset><url><loc>https://partner-st.ru/p1/</loc></url></urlset>",
            "https://partner-st.ru/products_2.xml": "<urlset><url><loc>https://partner-st.ru/p2/</loc></url><url><loc>https://partner-st.ru/p1/</loc></url></urlset>",
        }
        result = PartnerStAdapter().discover(FixtureTransport(values))
        self.assertEqual(result.product_urls, ("https://partner-st.ru/p1/", "https://partner-st.ru/p2/"))

    def test_redirect_tracking(self):
        history = FakeResponse(url="https://example.test/old")
        session = FakeSession([FakeResponse(history=[history])])
        result = HttpTransport(session=session, min_interval_seconds=0).get("https://example.test/old")
        self.assertEqual(result.redirects, ("https://example.test/old",))

    def test_crawl_retry_behavior(self):
        session = FakeSession([FakeResponse(503), FakeResponse(200)])
        result = HttpTransport(session=session, min_interval_seconds=0, retry_policy=RetryPolicy(2, 0)).get("https://example.test")
        self.assertEqual((result.status_code, result.attempts, session.calls), (200, 2, 2))

    def test_healthy_crawl_deactivation(self):
        self.assertEqual(next_missing_state(missed_crawls=2, active=True, healthy_full_crawl=True), (3, False))

    def test_anomalous_discovery_blocks_deactivation(self):
        health = discovery_health(100, 1222)
        self.assertTrue(health.anomalous_discovery)
        self.assertFalse(health.deactivation_allowed)
        self.assertEqual(next_missing_state(missed_crawls=2, active=True, healthy_full_crawl=False), (2, True))

    def test_out_of_stock_does_not_deactivate_source_product(self):
        self.assertEqual(normalize_availability("https://schema.org/OutOfStock"), "out_of_stock")
        self.assertTrue(source_active_for_availability("out_of_stock"))

    def test_run1_run2_idempotency(self):
        repo = InMemoryRepository()
        run1 = repo.ingest([self.card], "1")
        run2 = repo.ingest([self.card], "2")
        self.assertEqual((run1.new_products, run1.new_offers), (1, 1))
        self.assertEqual((run2.new_products, run2.new_offers, run2.changed_products, run2.changed_offers), (0, 0, 0, 0))

    def test_external_postgres_config_requires_all_db_fields(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DB_HOST"):
                PostgresConfig.from_env()

    def test_options_have_independent_change_hash(self):
        changed_value = replace(self.card.options[0].values[0], value="CHANGED")
        changed_option = replace(self.card.options[0], values=(changed_value,) + self.card.options[0].values[1:])
        changed = replace(self.card, options=(changed_option,))
        before, after = card_hashes(self.card), card_hashes(changed)
        self.assertNotEqual(before["options"], after["options"])
        self.assertEqual({k: v for k, v in before.items() if k != "options"},
                         {k: v for k, v in after.items() if k != "options"})

    def test_group_values_map_options_to_other_history_group(self):
        values = card_group_values(self.card)
        self.assertIn("options", values["other"])
        self.assertNotIn("options", values)

    def test_source_urls_deduplicate_requested_equals_canonical(self):
        card = replace(self.card, requested_url=self.card.canonical_url,
                       final_url=self.card.canonical_url, redirect_urls=())
        cursor = RecordingCursor()
        PostgresRepository._upsert_product_urls(cursor, 7, card, "2026-01-01T00:00:00Z")
        inserts = [params for query, params in cursor.calls if query.startswith("INSERT INTO source_product_urls")]
        self.assertEqual(len(inserts), 1)
        self.assertEqual((inserts[0][2], inserts[0][3]), ("source", True))

    def test_source_urls_persist_redirect_and_canonical(self):
        card = replace(self.card, requested_url="https://partner-st.ru/old",
                       final_url="https://partner-st.ru/new",
                       redirect_urls=("https://partner-st.ru/middle",))
        cursor = RecordingCursor()
        PostgresRepository._upsert_product_urls(cursor, 7, card, "2026-01-01T00:00:00Z")
        inserts = [params for query, params in cursor.calls if query.startswith("INSERT INTO source_product_urls")]
        kinds = {params[1]: (params[2], params[3]) for params in inserts}
        self.assertEqual(kinds["https://partner-st.ru/old"], ("source", False))
        self.assertEqual(kinds["https://partner-st.ru/middle"], ("redirect", False))
        self.assertEqual(kinds[self.card.canonical_url], ("canonical", True))

    def test_url_upsert_preserves_first_seen(self):
        cursor = RecordingCursor()
        PostgresRepository._upsert_product_urls(cursor, 7, self.card, "2026-01-01T00:00:00Z")
        upsert_sql = next(query for query, _ in cursor.calls if query.startswith("INSERT INTO source_product_urls"))
        self.assertNotIn("first_seen_at=EXCLUDED", upsert_sql)
        self.assertIn("last_seen_at=EXCLUDED", upsert_sql)

    def test_change_history_skips_equal_values(self):
        cursor = RecordingCursor()
        PostgresRepository._record_history(cursor, 1, 2, 3, None, "identity", {"x": 1}, {"x": 1}, "now")
        self.assertEqual(cursor.calls, [])

    def test_change_history_writes_real_change(self):
        cursor = RecordingCursor()
        PostgresRepository._record_history(cursor, 1, 2, 3, None, "identity", {"x": 1}, {"x": 2}, "now")
        self.assertEqual(len(cursor.calls), 1)
        self.assertIn("INSERT INTO entity_change_history", cursor.calls[0][0])

    def test_static_qa_accepts_self_contained_pass_without_db_env(self):
        self.assertEqual(integration_evidence_valid(valid_pass_evidence()), (True, True))

    def test_static_qa_rejects_incomplete_pass_evidence(self):
        self.assertEqual(integration_evidence_valid({"state": "PASS"}), (True, False))

    def test_report_generator_preserves_postgres_pass(self):
        recorded = valid_pass_evidence()
        resolved, passed = resolved_postgres_report(recorded)
        self.assertTrue(passed)
        self.assertIs(resolved, recorded)

    def test_report_generator_blocked_state_is_truthful(self):
        resolved, passed = resolved_postgres_report(None)
        self.assertFalse(passed)
        self.assertFalse(resolved["postgres_external"])
        self.assertEqual(resolved["state"], "BLOCKED_NO_EXTERNAL_POSTGRES")

    def test_old_pass_without_post_close_proof_is_rejected(self):
        old = valid_pass_evidence()
        old.pop("post_close_verification")
        self.assertEqual(integration_evidence_valid(old), (True, False))
        resolved, passed = resolved_postgres_report(old)
        self.assertFalse(passed)
        self.assertEqual(resolved["state"], "BLOCKED_NO_EXTERNAL_POSTGRES")

    def test_postgres_repository_connects_with_autocommit(self):
        connection = Mock()
        connect = Mock(return_value=connection)
        fake_psycopg = SimpleNamespace(connect=connect)
        config = PostgresConfig("db.example", 5432, "integration", "runner", "secret", "require")
        with patch.dict(sys.modules, {"psycopg": fake_psycopg}):
            repository = PostgresRepository(config)
        connect.assert_called_once_with(**config.kwargs(), autocommit=True)
        self.assertIs(repository.connection, connection)

    def test_plain_select_cannot_open_outer_transaction_in_safe_mode(self):
        class ProbeCursor(ResultCursor):
            def execute(inner_self, query, params=None):
                if not probe.autocommit:
                    probe.in_transaction = True

        class ProbeConnection:
            autocommit = True
            in_transaction = False

            def cursor(inner_self):
                return ProbeCursor([(1, "17.1", False)])

        # gate() expects a three-column row, so specialize fetchone.
        class GateCursor(ProbeCursor):
            def fetchone(inner_self):
                return next(inner_self.values)

        probe = ProbeConnection()
        probe.cursor = lambda: GateCursor([(1, "17.1", False)])
        repository = PostgresRepository.__new__(PostgresRepository)
        repository.connection = probe
        self.assertTrue(repository.gate()["select_1"])
        self.assertFalse(probe.in_transaction)

    def test_all_integrity_sql_files_are_pure_postgresql(self):
        root = Path(__file__).resolve().parents[1]
        for name in INTEGRITY_SQL_FILES:
            sql = read_psycopg_sql(root / "tests" / name)
            self.assertIn("BEGIN;", sql)
            self.assertIn("ROLLBACK;", sql)

    def test_stage3b_optional_sterbrust_url_migration_preserves_url_validation(self):
        root = Path(__file__).resolve().parents[1]
        sql = (root / "migrations" / "011_sterbrust_optional_product_url.sql").read_text(encoding="utf-8")
        self.assertIn("ALTER COLUMN product_url DROP NOT NULL", sql)
        self.assertIn("product_url IS NULL OR product_url ~ '^https?://'", sql)
        self.assertIn("BEGIN;", sql)
        self.assertIn("COMMIT;", sql)

    def test_sterbrust_loader_maps_missing_url_to_null_without_inventing_one(self):
        source = inspect.getsource(PostgresRepository.load_sterbrust_snapshot)
        self.assertIn('str(row.get("product_url") or "").strip() or None', source)

    def test_live_diff_accepts_exact_persisted_commercial_change(self):
        diff = [
            {"external_id": "1", "run1_present": "TRUE", "run2_present": "TRUE",
             "state": "UNCHANGED", "changed_groups": ""},
            {"external_id": "2", "run1_present": "TRUE", "run2_present": "TRUE",
             "state": "CHANGED", "changed_groups": "commercial"},
        ]
        postgres = {"state": "PASS", "idempotency_qa": "PASS_WITH_RECORDED_LIVE_SOURCE_CHANGES",
                    "source_diff": {"new": [], "missing": [], "changed": ["2"]},
                    "change_history_run2_delta": 1}
        self.assertTrue(live_diff_evidence_valid(
            diff, postgres, {"fetched_count": 2}, {"fetched_count": 2}))

    def test_live_diff_rejects_unrecorded_change(self):
        diff = [{"external_id": "2", "run1_present": "TRUE", "run2_present": "TRUE",
                 "state": "CHANGED", "changed_groups": "commercial"}]
        self.assertFalse(live_diff_evidence_valid(
            diff, {"state": "PASS", "source_diff": {}},
            {"fetched_count": 1}, {"fetched_count": 1}))

    def test_match_validation_time_cannot_predate_database_event_creation(self):
        source = inspect.getsource(PostgresRepository.persist_match)
        self.assertIn("last_validated_at=GREATEST(%s::timestamptz, now())", source)
        self.assertIn("GREATEST(%s::timestamptz, now()),true", source)

    def test_psycopg_sql_loader_rejects_meta_commands(self):
        path = SimpleNamespace(name="bad.sql", read_text=lambda **_: "\\set ON_ERROR_STOP on\nSELECT 1;")
        with self.assertRaisesRegex(ValueError, "Unsupported psql meta-command"):
            read_psycopg_sql(path)

    def test_post_close_verifier_requires_and_returns_durable_rows(self):
        table_counts = {name: 0 for name in (
            "suppliers", "crawl_runs", "crawl_run_items", "crawl_errors", "source_products",
            "offers", "source_product_urls", "supplier_categories", "source_product_categories",
            "product_category_paths", "product_category_path_nodes", "product_properties",
            "product_media", "source_product_options", "source_product_option_values",
            "source_product_relations", "entity_change_history", "sterbrust_products",
            "sterbrust_product_supplier_identifiers", "catalog_products", "product_matches")}
        table_counts.update({"crawl_runs": 2, "crawl_run_items": 2444,
                             "source_products": 1222, "offers": 1222,
                             "product_matches": 1336})
        repository = SimpleNamespace(
            gate=lambda: {"select_1": True, "server_version": "17.1", "runtime_role_superuser": False},
            table_counts=lambda: dict(table_counts),
            connection=CursorSequenceConnection([
                [0] * 16,
                [2, 1222, 1222, 1222, 1222, 114, 1, 1, 1],
            ]),
        )
        result = verify_after_reopen(repository, table_counts)
        self.assertEqual(result["post_close_verification"], "PASS")
        self.assertEqual(result["post_close_crawl_runs"], 2)
        self.assertEqual(result["post_close_current_matches"], 1222)
        self.assertEqual(result["post_close_partner_source_products"], 1222)
        self.assertEqual(result["post_close_partner_default_offers"], 1222)
        self.assertEqual(result["post_close_entity_change_history"], 0)

    def test_pass_report_is_built_only_after_close_and_reopen(self):
        source = inspect.getsource(integration_main)
        close_position = source.index("repo.close()")
        verify_position = source.index("verify_after_reopen")
        pass_position = source.index('{"state": "PASS"')
        self.assertLess(close_position, verify_position)
        self.assertLess(verify_position, pass_position)

    def test_two_initial_misses_do_not_deactivate(self):
        self.assertEqual(next_missing_state(missed_crawls=0, active=True, healthy_full_crawl=True), (1, True))
        self.assertEqual(next_missing_state(missed_crawls=1, active=True, healthy_full_crawl=True), (2, True))

    def test_automatic_link_is_quarantined_on_real_conflict(self):
        self.assertTrue(should_quarantine_automatic_link(True, "CONFLICT"))
        self.assertFalse(should_quarantine_automatic_link(False, "CONFLICT"))
        self.assertFalse(should_quarantine_automatic_link(True, "EXACT_MATCH"))

    def test_data_quality_flags_are_not_derived_from_warnings(self):
        decision = {"warnings": {"WARNING_ONLY": {}}, "data_quality_flags": ["FLAG_A"]}
        self.assertEqual(decision_data_quality_flags(decision, {}), ["FLAG_A"])
        self.assertNotIn("WARNING_ONLY", decision_data_quality_flags(decision, {}))

    def test_postgres_anomalous_discovery_does_not_touch_missing_state(self):
        cursor, counts = RecordingCursor([(10, "https://x.test/10", 2, True, 3)]), IngestCounts()
        PostgresRepository._apply_missing(cursor, 1, 2, set(), "now", False, counts)
        self.assertEqual(cursor.calls, [])
        self.assertEqual(counts.missing_products, 0)

    def test_postgres_third_miss_deactivates_product_and_default_offer(self):
        cursor, counts = RecordingCursor([(10, "https://x.test/10", 2, True, 3)]), IngestCounts()
        PostgresRepository._apply_missing(cursor, 1, 2, set(), "now", True, counts)
        product_update = next(params for query, params in cursor.calls if query.startswith("UPDATE source_products SET missed_crawls"))
        offer_update = next((query, params) for query, params in cursor.calls if query.startswith("UPDATE offers SET missed_crawls"))
        self.assertEqual(product_update, (3, False, 10))
        self.assertEqual(offer_update[1], (3, 10))
        self.assertEqual((counts.missing_products, counts.deactivated_products,
                          counts.missing_offers, counts.deactivated_offers), (1, 1, 1, 1))

    def test_real_run2_matching_reads_existing_link_from_database(self):
        source = inspect.getsource(run_one)
        self.assertIn("repo.load_existing_links()", source)
        self.assertNotIn("PARTNER_ST_FULL_MATCHING_RUN1.csv", source)

    def test_integration_runner_does_not_replay_packaged_cards(self):
        source = inspect.getsource(run_one)
        self.assertIn("run_partner_st_live", source)
        self.assertNotIn("PARTNER_ST_PARSED_RUN", source)


if __name__ == "__main__":
    unittest.main()
