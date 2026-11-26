"""Scoped wrapper refresh: supplier facts may change; catalogue identity may not.

Called under the passive writer's transaction, supplier and source row locks.
No activation, matching application, missing-product logic or canonical writes.
"""
from dataclasses import dataclass, replace

from .automatic_matching import hydrate_card, plan_card
from .change_detection import (card_group_values, commercial_history_value,
                               commercial_values_equal, history_values_equal, history_json)
from .commercial import PriceState
from .models import MediaItem, ProductOption, OptionValue


def content_card(card):
    card = hydrate_card(card)
    content = card.raw_data.get('source_content') or {}
    media = card.media
    if not media:
        items = []
        for kind, key in (('image', 'source_images'), ('document', 'source_documents')):
            for value in content.get(key) or ():
                url = value.get('url') if isinstance(value, dict) else value
                if url:items.append(MediaItem(kind, url, value.get('title', '') if isinstance(value, dict) else '', len(items)))
        media = tuple(items)
    return replace(card, media=media)


def reject_target_payload(value):
    if isinstance(value, dict):
        if {'catalog_product_id', 'sterbrust_product_id'} & value.keys():
            raise RuntimeError('SOURCE_UPDATE_TARGET_CHANGE_CONFLICT')
        for item in value.values():reject_target_payload(item)
    elif isinstance(value, (tuple, list)):
        for item in value:reject_target_payload(item)


def stored_details(q, card, source_id):
    """The matching loader omits media/options; history must read actual rows."""
    q.execute('''SELECT media_type,source_url,coalesce(title,''),position,is_primary
                 FROM product_media WHERE source_product_id=%s AND offer_id IS NULL ORDER BY position,id''', (source_id,))
    media = tuple(MediaItem(*row) for row in q.fetchall())
    q.execute('''SELECT o.id,o.raw_name,o.position,v.raw_value,v.target_url,v.variant_internal_id,
                        v.classification,v.is_selected,v.position
                 FROM source_product_options o LEFT JOIN source_product_option_values v ON v.option_id=o.id
                 WHERE o.source_product_id=%s ORDER BY o.position,o.id,v.position,v.id''', (source_id,))
    options = {}
    for key, name, position, value, url, variant, classification, selected, value_position in q.fetchall():
        options.setdefault(key, [name, position, []])
        if value is not None:options[key][2].append(OptionValue(value, url or '', variant or '', classification, selected, value_position))
    return content_card(replace(card, media=media, options=tuple(ProductOption(name,tuple(values),position)
                                                               for name,position,values in options.values())))


@dataclass
class Update:
    card: object
    run_id: int
    catalog_id: object
    old_values: dict
    new_values: dict
    old_raw: dict
    offer_active: bool
    product_changed: bool
    commercial_changed: bool
    commercial_only: bool = False

    def apply(self, repo, q, supplier_id, source_id, offer_id, at, counts):
        # Persist the same effective facts used by history/guards. Unknown price
        # and availability have already retained the last proven projection.
        raw = {**self.old_raw, **self.card.raw_data}
        raw['_commercial_projection_region'] = self.card.raw_data.get('_commercial_projection_region', '')
        q.execute('''UPDATE source_products SET brand_raw=%s,manufacturer_raw=%s,
                     description_text=%s,description_html=%s,raw_data=%s::jsonb
                     WHERE id=%s AND supplier_id=%s AND catalog_product_id IS NOT DISTINCT FROM %s''',
                  (self.card.brand or None, self.card.manufacturer or None, self.card.description_text,
                   self.card.description_html, history_json(raw), source_id, supplier_id, self.catalog_id))
        if q.rowcount != 1:raise RuntimeError('SOURCE_UPDATE_MAPPING_CONFLICT')
        q.execute('''UPDATE offers SET quantity=%s,active=%s
                     WHERE id=%s AND source_product_id=%s AND supplier_id=%s''',
                  (self.card.quantity, self.offer_active, offer_id, source_id, supplier_id))
        if q.rowcount != 1:raise RuntimeError('SOURCE_UPDATE_OFFER_CONFLICT')
        if self.product_changed or not self.old_values:
            repo._replace_details(q, supplier_id, source_id, offer_id, self.card, at)
        # The commercial ledger below the writer holds the actual incoming
        # values. Retained descriptions/properties must not receive fresh dates.
        if not self.commercial_only:repo._record_field_observations(q, source_id, self.card, at)
        repo._record_seen_observation(q, source_id, self.run_id, at)
        if self.old_values:
            for group, value in self.new_values.items():
                old = self.old_values.get(group)
                equal = commercial_values_equal(old, value) if group == 'commercial' else history_values_equal(old, value)
                if not equal:
                    if group == 'commercial':old, value = commercial_history_value(old), commercial_history_value(value)
                    if repo._record_history(q, self.run_id, supplier_id,
                                            None if group == 'commercial' else source_id,
                                            offer_id if group == 'commercial' else None,
                                            group, old, value, at):counts.change_history_events += 1
        # Readback is inside the same transaction as capture + observation.
        q.execute('SELECT catalog_product_id FROM source_products WHERE id=%s AND supplier_id=%s', (source_id, supplier_id))
        if q.fetchone() != (self.catalog_id,):raise RuntimeError('SOURCE_UPDATE_MAPPING_CONFLICT')


def prepare_update(repo, q, supplier_id, run_id, card, observation, capture, existing, *, commercial_only=False):
    reject_target_payload(card.raw_data)
    if capture.capture_scope != 'detail' or card.requested_url != capture.requested_url:
        raise RuntimeError('SOURCE_UPDATE_DETAIL_IDENTITY_REQUIRED')
    q.execute('''SELECT r.id FROM crawl_runs r JOIN crawl_jobs j ON j.crawl_run_id=r.id
                 WHERE r.id=%s AND r.supplier_id=%s AND j.supplier_id=r.supplier_id
                 AND r.status='running' AND j.status='running'
                 AND r.metadata->>'job_id'=j.id::text
                 AND r.metadata->>'source_mode' IN ('REFRESH','DISCOVERY','RESUME')''', (run_id, supplier_id))
    if q.fetchone() != (run_id,):raise RuntimeError('SOURCE_UPDATE_RUNNING_JOB_REQUIRED')
    if commercial_only:
        from .control_plane.commercial_refresh import authorize_card
        expected=authorize_card(card,capture)
        if not existing or (existing[0],existing[1])!=(expected['source_product_id'],expected['catalog_product_id']):
            raise RuntimeError('SOURCE_UPDATE_MAPPING_CONFLICT')
        q.execute("SELECT settings_snapshot->'automation'->>'approval_sha256' FROM crawl_jobs WHERE crawl_run_id=%s AND status='running'",(run_id,))
        from .control_plane.automation_execution import require_active
        if q.fetchone()!=(require_active().sha,):raise RuntimeError('COMMERCIAL_REFRESH_JOB_APPROVAL_CHANGED')
    q.execute('''SELECT id FROM source_products WHERE supplier_id=%s
                 AND (rtrim(coalesce(canonical_url,source_url),'/')=rtrim(%s,'/')
                      OR rtrim(source_url,'/')=rtrim(%s,'/')) FOR UPDATE''',
              (supplier_id, card.canonical_url, capture.requested_url))
    identity_rows = q.fetchall()
    if identity_rows != ([(existing[0],)] if existing else []):
        raise RuntimeError('SOURCE_UPDATE_URL_IDENTITY_CONFLICT')
    card = content_card(card)
    old_values = {}
    catalog_id = existing[1] if existing else None
    active = False  # New passive offers remain excluded from selection.
    if existing:
        from .identity_service import _load_cards
        cards, ids = _load_cards(repo.connection, card.supplier_code, external_ids=[card.external_id], include_inactive=True)
        if len(cards) != 1 or ids.get((card.supplier_code, card.external_id)) != existing[0]:
            raise RuntimeError('SOURCE_UPDATE_EXISTING_IDENTITY_CONFLICT')
        previous = stored_details(q, cards[0], existing[0])
        if previous.canonical_url.rstrip('/') != card.canonical_url.rstrip('/'):
            raise RuntimeError('SOURCE_UPDATE_URL_IDENTITY_CONFLICT')
        q.execute('SELECT id,active FROM offers WHERE source_product_id=%s AND offer_kind=\'default\' FOR UPDATE', (existing[0],))
        offer = q.fetchone()
        if offer is None:raise RuntimeError('SOURCE_UPDATE_EXISTING_OFFER_MISSING')
        active = offer[1]
        if observation.price_state not in {PriceState.NUMERIC_PUBLIC, PriceState.PRICE_ON_REQUEST}:
            card = replace(card, price=previous.price, old_price=previous.old_price, currency=previous.currency)
        if card.availability_normalized == 'unknown':
            card = replace(card, availability_raw=previous.availability_raw,
                           availability_normalized=previous.availability_normalized, quantity=previous.quantity)
        elif card.quantity is None:
            card = replace(card, quantity=previous.quantity)
        old_values = card_group_values(previous)
        if catalog_id is not None:
            from .matching_service import load_registry_from_postgres
            registry = load_registry_from_postgres(repo.connection, catalog_product_id=catalog_id)
            if len(registry.products) != 1:raise RuntimeError('SOURCE_UPDATE_TARGET_NOT_PROVEN')
            target = next(iter(registry.products))
            plan = plan_card(card, registry, {'sterbrust_product_id': target})
            # Refreshing an unchanged already-linked ambiguous identity is not
            # a new AUTO acceptance. Preserve that mapping and refresh only its
            # factual data. New semantic evidence must pass the strict matcher;
            # a hard conflict stops even an otherwise unchanged projection.
            previous_groups, incoming_groups = card_group_values(previous), card_group_values(card)
            semantic_unchanged = all(history_values_equal(previous_groups[k], incoming_groups[k])
                                     for k in ('identity', 'description', 'properties', 'categories', 'other'))
            if plan['bucket'] != 'AUTO' and (plan['bucket'] == 'CONFLICT' or not semantic_unchanged):
                raise RuntimeError('SOURCE_UPDATE_SEMANTIC_CONFLICT:' + plan['reason'])
    if commercial_only:
        # Semantic checks above use the actual incoming identity. Only after
        # they pass may the narrow refresh retain all stored content/details.
        card=commercial_projection(previous,card)
    values = card_group_values(card)
    product_changed = bool(old_values) and any(not history_values_equal(old_values.get(k), v)
                                              for k, v in values.items() if k != 'commercial')
    commercial_changed = bool(old_values) and not commercial_values_equal(old_values.get('commercial'), values['commercial'])
    raw = dict(card.raw_data);raw['_commercial_projection_region'] = capture.region_code or ''
    return Update(replace(card, raw_data=raw), run_id, catalog_id, old_values, values,
                  (existing[3] or {}) if existing else {}, active, product_changed, commercial_changed,commercial_only)


def commercial_projection(previous,incoming):
    raw=dict(previous.raw_data)
    for key in ('price_extraction','price_type_evidence','commercial_capture'):
        if key in incoming.raw_data:raw[key]=incoming.raw_data[key]
    return replace(previous,price=incoming.price,old_price=incoming.old_price,currency=incoming.currency,
        availability_raw=incoming.availability_raw,availability_normalized=incoming.availability_normalized,
        quantity=incoming.quantity,price_type=incoming.price_type,price_raw=incoming.price_raw,
        price_source=incoming.price_source,raw_data=raw,http_status=incoming.http_status)
