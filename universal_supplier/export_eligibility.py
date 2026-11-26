"""Operator-approved negative eligibility at supplier-offer identity, never target ID.

16 Nov 2026: preserve all identities/observations/LKG; prevent fresh commercial
export for the six unresolved Partner-ST contradictions. Removing a hold requires
an explicit reviewed resolution. No matching, database or collection mutation.
"""
from collections.abc import Mapping

VERSION = 'partner-st-six-semantic-holds/2026-11-16'
HOLDS = (
    (15, '309', 'voltage_380_vs_220'),
    (24, '321', 'voltage_380_vs_220'),
    (25, '322', 'voltage_380_vs_220'),
    (35, '337', 'voltage_380_vs_220'),
    (117, '437', 'disk_dimensions_conflict'),
    (193, '518', 'jaw_width_conflict'),
)


def semantic_export_hold(row):
    """Stable source ID or supplier external ID; another supplier stays eligible."""
    def get(key):
        return row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)
    if (get('supplier_code') or get('supplier') or get('source')) != 'partner_st':
        return None
    sid = str(get('source_product_id') or '')
    external = str(get('external_id') or get('supplier_external_id') or '')
    for source_id, external_id, reason in HOLDS:
        if sid == str(source_id) or external == external_id:
            return {'code': 'SEMANTIC_EXPORT_HOLD', 'version': VERSION,
                    'supplier': 'partner_st', 'source_product_id': source_id,
                    'external_id': external_id, 'reason': reason,
                    'release': 'EXPLICIT_REVIEWED_RESOLUTION_REQUIRED'}
    return None
