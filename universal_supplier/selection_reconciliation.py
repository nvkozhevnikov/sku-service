"""Historical read-only diff diagnostics, NOT the ordinary selection writer.

Legacy DELETE labels remain readable for preserved receipts only. Operational
lifecycle is exclusively PostgresOfferSelectionStore.evaluate_all()/apply().
The obsolete mutation entry point is structurally inert/fail-closed.
"""
from collections import Counter


def selection_diff(current, expected, scope):
    old = {r['catalog_product_id']: r for r in current}
    if len(old) != len(current):
        raise ValueError('Duplicate canonical selection')
    if not set(expected) <= set(scope):
        raise ValueError('Expected winner outside approved scope')
    result = []
    for canonical in sorted(set(old) | set(expected)):
        prior = old.get(canonical)
        desired = expected.get(canonical)
        selected = prior is not None and prior['selection_status'] == 'selected'
        if canonical not in scope:
            action, reason = 'KEEP', 'OUTSIDE_RECONCILIATION_SCOPE'
        elif desired is None:
            action, reason = ('DELETE', 'NO_CURRENT_ELIGIBLE_CONFIRMED_WINNER') if selected else ('KEEP', 'NON_WINNER_UNCHANGED')
        elif prior is None:
            action, reason = 'INSERT', 'NEW_ELIGIBLE_CONFIRMED_WINNER'
        elif selected and prior['selected_offer_id'] == desired['selected_offer_id']:
            action, reason = 'KEEP', 'CURRENT_WINNER_EQUALS_SHARED_POLICY'
        else:
            action, reason = 'UPDATE', 'SHARED_POLICY_WINNER_CHANGED'
        result.append({'catalog_product_id': canonical,
                       'persisted_offer_id': prior['selected_offer_id'] if prior else None,
                       'expected_offer_id': desired['selected_offer_id'] if desired else None,
                       'action': action, 'reason': reason})
    return result


def delta_counts(diff):
    counts = Counter(r['action'] for r in diff)
    return {key: counts[key] for key in ('KEEP', 'DELETE', 'INSERT', 'UPDATE')}


def apply_diff(conn, diff, expected):
    # Validate before even inspecting the connection. A caller cannot restore
    # the obsolete deletion lifecycle by rewiring its disabled CLI runner.
    raise ValueError('Legacy selection mutation API removed; use canonical evaluate_all()/apply() UPSERT lifecycle')
