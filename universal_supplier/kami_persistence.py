"""KAMI projection into existing passive observation contract, no DB dependency."""
from dataclasses import replace
import hashlib
from universal_supplier.adapters.kami import canonical_source_url
from universal_supplier.change_detection import stable_hash
from universal_supplier.commercial import PriceState
from universal_supplier.commercial_persistence import CommercialObservation
from sterbrust_matching.normalization import normalize_model


def prepare_observation(card, capture):
    if card.supplier_code != 'kami' or capture.capture_scope != 'detail':
        raise ValueError('KAMI-only detail observation')
    canonical = canonical_source_url(card.canonical_url)
    if canonical_source_url(capture.final_url) != canonical:
        raise ValueError('capture/card URL mismatch')
    if card.http_status != 200 or card.raw_data.get('adapter') != 'kami-v1':
        raise ValueError('KAMI parser provenance missing')
    internal = card.raw_data.get('site_internal_id', '')
    variant = card.raw_data.get('site_variant_id', '')
    expected = 'variant:' + variant if variant else ('site:' + internal if internal else 'url:' + hashlib.sha256(canonical.encode()).hexdigest())
    if card.external_id != expected:
        raise ValueError('KAMI stable source identity mismatch')
    raw = dict(card.raw_data)
    raw['source_identity'] = {'raw_model': raw['full_model'], 'normalized_model': normalize_model(raw['full_model']),
                              'brand': card.brand, 'site_internal_id': internal, 'site_variant_id': variant,
                              'manufacturer_article_explicit': False, 'identity_contract': 'kami-site-id-v1',
                              'model_status': raw['identity_status'], 'card_kind': raw['card_kind']}
    raw['source_content'] = {'description_text': card.description_text,
                             'technical_properties': [[p.name, p.value] for p in card.properties],
                             'source_images': [m.url for m in card.media if m.media_type == 'image'],
                             'source_documents': [m.url for m in card.media if m.media_type == 'document'],
                             'source_category': card.categories[0].name if card.categories else '',
                             'source_category_url': card.categories[0].url if card.categories else None}
    raw['commercial_capture'] = {'response_sha256': capture.response_sha256, 'evidence_sha256': capture.evidence_sha256,
                                 'evidence_ref': capture.evidence_ref, 'observed_at': capture.observed_at.isoformat()}
    extraction = {'state': raw['price_state'], 'price_basis': raw['price_basis'], 'price_raw': card.price_raw,
                  'price_source': card.price_source, 'variant_or_group': raw['card_kind'] != 'SINGLE_PRODUCT'}
    raw['price_extraction'] = extraction
    # Capture timestamps/HTML/session changes are observations, not product changes.
    raw['_kami_content_fact_hash'] = stable_hash({
        'name': card.name, 'brand': card.brand, 'manufacturer': card.manufacturer,
        'sku': card.sku, 'canonical_url': canonical, 'identity': raw['source_identity'],
        'description': card.description_text, 'description_html': card.description_html,
        'properties': [(p.name, p.value, p.unit, p.position) for p in card.properties],
        'media': [(m.media_type, m.url, m.title, m.position) for m in card.media],
        'categories': [(c.name, c.url, c.position) for c in card.categories],
        'variants': raw['variant_evidence'], 'description_state': raw['description_state'],
        'property_evidence': raw.get('property_evidence', []),
        'documents_state': raw.get('documents_state', 'UNKNOWN'),
    })
    raw['_kami_commercial_fact_hash'] = stable_hash({
        'price': str(card.price) if card.price is not None else None,
        'old_price': str(card.old_price) if card.old_price is not None else None,
        'currency': card.currency, 'availability_raw': card.availability_raw,
        'availability': card.availability_normalized, 'quantity': card.quantity,
        'price_extraction': extraction,
    })
    reasons = []
    if raw['price_basis'] == 'from_price':
        reasons.append('FROM_PRICE_NOT_AN_EXACT_EXECUTION_PRICE')
    if raw['card_kind'] != 'SINGLE_PRODUCT':
        reasons.append('GROUP_PRICE_NOT_AN_EXACT_EXECUTION_PRICE')
    observation = CommercialObservation(
        'kami', card.external_id, capture.observed_at, capture.response_sha256,
        stable_hash({'capture': capture.fingerprint, 'extraction': extraction}), PriceState(raw['price_state']),
        card.price, card.old_price, card.currency or None, card.availability_raw,
        card.availability_normalized, 'unknown', card.price_raw, card.price_source,
        extraction, tuple(reasons), capture.region_code)
    return replace(card, raw_data=raw), observation
