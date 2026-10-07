"""Two narrow read-only compatibility guards; no persistence or network code."""
from decimal import Decimal, InvalidOperation
from datetime import datetime
import hashlib
import re
from universal_supplier.kami_persistence import prepare_observation

LEGACY_CANARIES = {'site:9270806':7187, 'site:9807314':7186,
                   'variant:9833201':7188, 'variant:9833203':7189}


class CompatibilityGuardError(RuntimeError):
    pass


def migration_integrity(working_bytes, head_blob, database_sha):
    """Only CRLF/LF representation differs; DB SHA is the exact Git blob SHA."""
    blob_sha = hashlib.sha256(head_blob).hexdigest()
    if not re.fullmatch('[0-9a-f]{64}', database_sha or '') or database_sha != blob_sha:
        raise CompatibilityGuardError('DB migration SHA != exact HEAD Git blob')
    if working_bytes.replace(b'\r\n', b'\n') != head_blob.replace(b'\r\n', b'\n'):
        raise CompatibilityGuardError('Migration content differs beyond CRLF/LF')
    return {'HEAD_blob_sha256':blob_sha, 'working_sha256':hashlib.sha256(working_bytes).hexdigest(),
            'line_endings_only':working_bytes != head_blob}


def decimal_value(value):
    if value is None:
        return None
    if isinstance(value,(bool,float)):
        raise CompatibilityGuardError('Inexact numeric representation in commercial proof')
    try:
        result = Decimal(value)
    except (InvalidOperation,TypeError,ValueError) as exc:
        raise CompatibilityGuardError('Invalid persisted decimal') from exc
    if not result.is_finite():
        raise CompatibilityGuardError('Non-finite decimal')
    return result


def require_equal(actual, expected, label):
    if actual != expected:
        raise CompatibilityGuardError('Legacy commercial evidence drift: '+label)


def commercial_proof(card, capture, previous, source_raw, offer, observations,
                     *, snapshot_verified=False, capture_verified=False):
    """Permit exactly four absent hashes only after complete independent proof.

    Does NOT create/assign a hash. Every hash producer field is compared from
    persisted fields, raw source/offer extraction and the immutable observation.
    """
    projected, expected_observation = prepare_observation(card,capture)
    expected_hash = projected.raw_data['_kami_commercial_fact_hash']
    stored_hash = source_raw.get('_kami_commercial_fact_hash')
    if stored_hash is not None:
        require_equal(stored_hash,expected_hash,'non-null stored commercial hash')
        return {'mode':'STORED_HASH_EXACT'}
    if (card.supplier_code != 'kami' or LEGACY_CANARIES.get(card.external_id) != previous['source_product_id']
            or not snapshot_verified or not capture_verified):
        raise CompatibilityGuardError('Missing hash outside exact snapshot-verified legacy allowlist')
    require_equal(source_raw.get('_kami_content_fact_hash'),projected.raw_data['_kami_content_fact_hash'],'content hash')
    require_equal(offer['source_product_id'],previous['source_product_id'],'offer source PK')
    require_equal(offer['external_offer_id'],card.external_id,'offer source identity')
    require_equal(offer['supplier_id'],5812,'approved KAMI supplier namespace')
    require_equal(offer['offer_kind'],'default','offer kind')
    require_equal(offer['active'],False,'inactive offer')
    extraction = projected.raw_data['price_extraction']
    for name,expected in [('price',card.price),('old_price',card.old_price),('quantity',card.quantity)]:
        require_equal(decimal_value(offer[name]),decimal_value(expected),'offer '+name)
    for name,expected in [('currency',card.currency),('availability_raw',card.availability_raw),
                          ('availability_normalized',card.availability_normalized),('price_type','unknown')]:
        require_equal(offer[name],expected,'offer '+name)
    # These old optional projection columns were not populated by the original
    # writer. Their evidence is raw extraction + observation, never invented.
    for key,expected in [('price_raw',card.price_raw),('price_source',card.price_source)]:
        if offer.get(key) is not None:
            require_equal(offer[key],expected,'offer optional '+key)
    for label,raw in [('source',source_raw),('offer',offer['raw_data'])]:
        require_equal(raw.get('price_extraction'),extraction,label+' price extraction roles/source')
        for key in ('price_state','price_basis','old_price_raw','quantity_state','quantity_raw','quantity_role'):
            if key in raw or key in projected.raw_data:
                require_equal(raw.get(key),projected.raw_data.get(key),label+' raw '+key)
        require_equal(raw.get('commercial_capture'),projected.raw_data['commercial_capture'],label+' capture provenance')
        for key,expected in [('currency',card.currency),('availability_raw',card.availability_raw),
                             ('availability_normalized',card.availability_normalized)]:
            if key in raw:
                require_equal(raw[key],expected,label+' raw '+key)
        for key,expected in [('price',card.price),('old_price',card.old_price),('quantity',card.quantity)]:
            if key in raw:
                require_equal(decimal_value(raw[key]),decimal_value(expected),label+' raw '+key)
    require_equal(source_raw.get('_commercial_projection_region'),capture.region_code or '','source projection region')
    if len(observations) != 1:
        raise CompatibilityGuardError('Exactly one original capture observation required')
    obs = observations[0]
    for key,expected in [('supplier_id',5812),('source_product_id',previous['source_product_id']),
                         ('offer_id',offer['id']),('capture_id',previous['capture_id']),
                         ('price_state',expected_observation.price_state.value),
                         ('currency',expected_observation.currency),('availability_raw',expected_observation.availability_raw),
                         ('availability_normalized',expected_observation.availability_normalized),
                         ('price_type',expected_observation.price_type),('price_raw',expected_observation.price_raw),
                         ('price_source',expected_observation.price_source),
                         ('extraction_evidence',expected_observation.extraction_evidence),
                         ('extraction_fingerprint',expected_observation.extraction_fingerprint),
                         ('unusable_price_reasons',list(expected_observation.unusable_price_reasons))]:
        require_equal(obs[key],expected,'immutable observation '+key)
    for key,expected in [('price',expected_observation.price),('old_price',expected_observation.old_price)]:
        require_equal(decimal_value(obs[key]),decimal_value(expected),'immutable observation '+key)
    require_equal(datetime.fromisoformat(obs['observed_at']),expected_observation.observed_at,'observation time')
    require_equal(obs.get('region_code') or '',expected_observation.region_code or '','observation region')
    # Quantity has no observation column in migration015; its independent proof
    # is typed offer.quantity plus source/offer quantity state/role/raw evidence.
    return {'mode':'EXACT_LEGACY_SEMANTIC_PROOF_NO_WRITE', 'external_id':card.external_id,
            'PK':previous['source_product_id'], 'hash_written':False,
            'fields':['price','old_price','currency','availability_raw','availability_normalized','quantity',
                      'price_extraction/state/basis/raw/source/group','quantity_state/raw/role','old_price_raw',
                      'source/offer capture provenance','immutable observation/extraction/reasons/region']}
