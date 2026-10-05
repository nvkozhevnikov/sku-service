"""KAMI neutrality guards for the generic offer-selection policy.

Audit branch: feature/universal-supplier-kami-glm-audit-rc1 (2026-10-05).
config/offer_selection.json deliberately has no 'kami' entry yet: the sixth
supplier must neither gain nor lose from the default priority, no winner may
be invented from a missing policy row, and unknown availability states must
stay excluded rather than guessed. Pure offline evaluation of in-memory
OfferCandidate values; no DB, no HTTP.
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from universal_supplier.offer_selection import (
    OfferCandidate, OfferSelectionPolicy, evaluate_offers)

POLICY = OfferSelectionPolicy.load(
    Path(__file__).resolve().parents[1] / 'config/offer_selection.json')
TS = datetime(2026, 10, 5, 12, 0, 0)


def offer(**over):
    row = dict(catalog_product_id=1, offer_id=1, source_product_id=1, supplier_id=50,
               offer_supplier_id=50, supplier_code='kami', supplier_enabled=True,
               source_catalog_product_id=1, source_active=True, source_missed_crawls=0,
               source_last_success_at=TS, offer_active=True, offer_kind='default',
               offer_missed_crawls=0, offer_last_success_at=TS,
               latest_successful_full_crawl_at=TS, price=Decimal('100000'),
               currency='RUB', availability='in_stock', availability_raw='В наличии',
               quantity=None)
    row.update(over)
    return OfferCandidate(**row)


def test_config_has_no_kami_entry_and_default_priority_is_uniform_neutral():
    assert 'kami' not in POLICY.supplier_priority
    assert POLICY.default_supplier_priority == 0
    assert set(POLICY.supplier_priority.values()) == {POLICY.default_supplier_priority}


def test_missing_kami_priority_cannot_invent_a_price_winner():
    for cheaper, pricier in (('kami', 'partner_st'), ('partner_st', 'kami')):
        low = offer(supplier_code=cheaper, offer_id=2, source_product_id=1, price=Decimal('900000'))
        high = offer(supplier_code=pricier, offer_id=3, source_product_id=2, price=Decimal('1000000'))
        decision = evaluate_offers(1, [low, high], POLICY)
        assert decision.selection_status == 'selected'
        assert decision.selected_offer_id == low.offer_id
        assert decision.evidence['selection_reason'] == 'lowest_valid_price_within_top_business_rank'
        # Priority evidence shows no advantage for either supplier.
        for row in decision.evidence['candidate_offers']:
            assert row['ranking_inputs']['supplier_priority'] == POLICY.default_supplier_priority


def test_equal_business_inputs_break_deterministically_not_randomly():
    kami = offer(supplier_code='kami', offer_id=7, source_product_id=1)
    partner = offer(supplier_code='partner_st', offer_id=3, source_product_id=2)
    first = evaluate_offers(1, [partner, kami], POLICY)
    second = evaluate_offers(1, [kami, partner], POLICY)
    assert first.selected_offer_id == second.selected_offer_id == kami.offer_id
    assert first.evidence['selection_reason'] == 'deterministic_tie_break_after_equal_business_inputs'
    assert first.evidence['selected_supplier_code'] == 'kami'
    winner_row = next(row for row in first.evidence['candidate_offers']
                     if row['offer_id'] == kami.offer_id)
    assert winner_row['ranking_inputs']['tie_break'] == ['kami', 7]


def test_lone_kami_offer_uses_generic_single_selection_without_priority_claim():
    decision = evaluate_offers(1, [offer(offer_id=9)], POLICY)
    assert decision.selection_status == 'selected'
    assert decision.evidence['selection_reason'] == 'single_eligible_current_offer'
    assert decision.evidence['supplier_priority'] == POLICY.default_supplier_priority


def test_unknown_availability_state_is_excluded_fail_closed():
    unconfigured = offer(offer_id=4, availability='warehouse_direct_unmapped')
    decision = evaluate_offers(1, [unconfigured], POLICY)
    assert decision.selection_status == 'no_eligible_offer'
    exclusions = decision.evidence['candidate_offers'][0]['exclusion_reasons']
    assert 'availability_not_configured' in exclusions


def test_disabled_kami_supplier_cannot_win_selection():
    # Mirrors the recorded integration state (supplier_enabled=false, offers
    # inactive): KAMI can never win a selection while disabled.
    disabled = offer(offer_id=5, supplier_enabled=False, offer_active=False)
    decision = evaluate_offers(1, [disabled], POLICY)
    assert decision.selection_status == 'no_eligible_offer'
    exclusions = set(decision.evidence['candidate_offers'][0]['exclusion_reasons'])
    assert {'supplier_disabled', 'offer_inactive'} <= exclusions
