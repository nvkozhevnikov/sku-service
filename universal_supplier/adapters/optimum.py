from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from universal_supplier.control_plane.rate_control import (
    DomainRatePolicy,
    MaxRunDurationExceeded,
)
from universal_supplier.models import (
    CategoryNode, MediaItem, OptionValue, ProductCard, ProductOption, PropertyValue,
)

from .optimum_source.adapter import OptimumAdapter as SourceAdapter
from .optimum_source.category_tree import build_category_tree, category_tree_qa


@dataclass(frozen=True)
class OptimumLiveCrawl:
    cards: tuple[ProductCard, ...]
    rows: tuple[dict[str, Any], ...]
    aliases: tuple[dict[str, str], ...]
    category_tree: tuple[dict[str, Any], ...]
    started_at: str
    finished_at: str
    metadata: dict[str, Any]


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value not in (None, "") else None


def _brand_from_source(name: str, raw_brand: str | None) -> str:
    if raw_brand:
        return raw_brand
    # optimum.su is a supplier-specific adapter. Most product cards omit an
    # explicit brand field even for Optimum machines, which previously left
    # brand_raw empty and prevented otherwise safe brand+model candidate checks.
    # Preserve any explicit site brand, otherwise use the supplier brand.
    return "Optimum"


def _availability_for_database(value: str | None) -> str:
    """Map source vocabulary while retaining it in raw_data.source_record."""
    return {
        None: "unknown",
        "": "unknown",
        "on_request": "backorder",
    }.get(value, value or "unknown")


def to_product_card(product, *, final_url: str, aliases: tuple[str, ...] = ()) -> ProductCard:
    identity = product.identity
    media = [
        MediaItem("image", item.source_url, item.title or item.alt or "", item.position, item.is_primary)
        for item in product.images
    ]
    media.extend(
        MediaItem("document", item.source_url, item.title or item.document_type or "", item.position, False)
        for item in product.documents
    )
    media.extend(
        MediaItem("video", item.source_url, item.title or item.provider or "", item.position, False)
        for item in product.videos
    )
    related_values = tuple(
        OptionValue(
            value=item.target_name or item.target_sku or item.target_url or "related product",
            target_url=item.target_url or "",
            classification="NAVIGATION_TO_PRODUCT",
            position=item.position,
        )
        for item in product.related_products if item.target_url
    )
    options = (ProductOption("related_products", related_values, 0),) if related_values else ()
    raw_data = dict(product.raw_data)
    raw_data.update({
        "identity_kind": identity.external_id_type,
        "internal_product_id": identity.internal_product_id,
        "source_record": product.model_dump(mode="json"),
        "sellable_model": "one_card_one_default_offer",
        "supplier_article_namespace": "ARTIKUL_OPTIMUM",
        "source_kind": "supplier_html",
    })
    return ProductCard(
        supplier_code="optimum",
        external_id=str(identity.external_id),
        sku=identity.sku or "",
        name=identity.name or "",
        requested_url=identity.source_url or identity.canonical_url,
        final_url=final_url or identity.canonical_url,
        canonical_url=identity.canonical_url,
        http_status=200,
        redirect_urls=(),
        price=_decimal(product.commercial.price),
        old_price=_decimal(product.commercial.old_price),
        currency=product.commercial.currency or "RUB",
        availability_raw=product.commercial.availability_raw or "",
        availability_normalized=_availability_for_database(product.commercial.availability_normalized),
        quantity=_decimal(product.commercial.quantity),
        description_text=product.description.text or "",
        description_html=product.description.html or "",
        brand=_brand_from_source(identity.name or "", identity.brand_raw),
        manufacturer=identity.manufacturer_raw or "",
        properties=tuple(
            PropertyValue(item.raw_name, item.raw_value, item.raw_unit or "", item.position)
            for item in product.properties
        ),
        media=tuple(media),
        options=options,
        categories=tuple(
            CategoryNode(item.name, item.url or "", item.position) for item in product.categories if item.url
        ),
        raw_data=raw_data,
        alias_urls=aliases,
        price_type="unknown",
        price_raw=str((product.raw_data or {}).get("source_price_text") or product.commercial.price or ""),
        price_source="supplier page price block",
    )


async def _collect_product_batch(adapter: SourceAdapter, records, progress_callback=None) -> tuple[list[dict], bool]:
    """Collect completed records and cancel work still pending at the deadline."""
    tasks = [asyncio.create_task(adapter._one(record)) for record in records]
    if not tasks:
        return [], False
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    deadline_exceeded = any(
        isinstance(task.exception(), MaxRunDurationExceeded)
        for task in done if not task.cancelled()
    )
    if deadline_exceeded:
        for task in pending:
            task.cancel()
    settled = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict] = []
    for value in settled:
        if isinstance(value, dict):
            results.append(value)
            if progress_callback:
                progress_callback("PRODUCT_COMPLETED", success=value.get("classification") != "FETCH_ERROR")
        elif isinstance(value, (MaxRunDurationExceeded, asyncio.CancelledError)):
            deadline_exceeded = True
        elif isinstance(value, BaseException):
            raise value
    return results, deadline_exceeded


async def run_optimum_live(*, rate_policy: DomainRatePolicy | None = None,
                           concurrency: int | None = None, delay: float | None = None,
                           clock=None, random_uniform=None, client=None,
                           progress_callback=None) -> OptimumLiveCrawl:
    started_at = datetime.now(timezone.utc).isoformat()
    adapter = SourceAdapter(
        output_dir=None,
        concurrency=concurrency,
        delay=delay,
        rate_policy=rate_policy,
        clock=clock,
        random_uniform=random_uniform,
        client=client,
    )
    effective_policy = adapter.rate_policy
    requested_policy = rate_policy or effective_policy
    discovery = []
    discovered_categories = []
    sitemap_maps = []
    results: list[dict[str, Any]] = []
    deadline_exceeded = False
    try:
        if progress_callback:
            progress_callback("DISCOVERY")
        try:
            discovery, discovered_categories, sitemap_maps = await adapter.discover_products()
        except MaxRunDurationExceeded:
            deadline_exceeded = True
        deadline_exceeded = deadline_exceeded or adapter.fetcher.deadline_exceeded
        if progress_callback:
            progress_callback("FETCHING_PRODUCTS", total=len(discovery))
        if not deadline_exceeded:
            for start in range(0, len(discovery), 24):
                completed, stopped = await _collect_product_batch(
                    adapter, discovery[start:start + 24], progress_callback)
                results.extend(completed)
                deadline_exceeded = (
                    deadline_exceeded or stopped or adapter.fetcher.deadline_exceeded)
                if deadline_exceeded:
                    break
        products, aliases, _ = adapter._deduplicate(
            [result for result in results if result["classification"] == "PRODUCT"]
        )
        aliases_by_id: dict[str, list[str]] = {}
        for row in aliases:
            aliases_by_id.setdefault(str(row["external_id"]), []).append(row["alias_url"])
        final_by_id = {
            str(result["product"].identity.external_id): result["final_url"]
            for result in results if result["classification"] in {"PRODUCT", "PRODUCT_ALIAS"}
        }
        cards = tuple(
            to_product_card(
                product,
                final_url=final_by_id.get(str(product.identity.external_id), product.identity.canonical_url),
                aliases=tuple(sorted(set(aliases_by_id.get(str(product.identity.external_id), [])))),
            ) for product in products
        )
        product_urls = {card.canonical_url for card in cards} | {card.requested_url for card in cards}
        category_evidence = list(discovered_categories)
        category_evidence.extend(
            result["category"] | {"discovery_sources": "sitemap|category_page"}
            for result in results if result["classification"] == "CATEGORY" and result.get("category")
        )
        tree = build_category_tree(category_evidence, products)
        tree_qa = category_tree_qa(tree, products, product_urls)
        rows = tuple({
            "position": position,
            "source_url": result["record"].source_url,
            "final_url": result["final_url"],
            "http_status": result["status"],
            "classification": result["classification"],
            "classification_reason": result["reason"],
            "external_id": getattr(result.get("product"), "identity", None).external_id if result.get("product") else "",
            "external_id_type": getattr(result.get("product"), "identity", None).external_id_type if result.get("product") else "",
            "sku": getattr(result.get("product"), "identity", None).sku if result.get("product") else "",
            "attempt_count": result.get("attempt_count", 0),
            "error_type": result.get("error_type", ""),
            "error": result.get("error", ""),
        } for position, result in enumerate(results, 1))
        finished_at = datetime.now(timezone.utc).isoformat()
        fetch_errors = sum(row["classification"] == "FETCH_ERROR" for row in rows)
        if deadline_exceeded:
            adapter.fetcher.metrics.max_run_duration_exceeded = True
            adapter.fetcher.metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
        metadata = {
            "started_at": started_at, "finished_at": finished_at,
            "sitemap_child_maps": len(sitemap_maps), "discovered": len(discovery), "fetched": len(results),
            "http_200": sum(row["http_status"] == 200 for row in rows),
            "stale_404": sum(row["classification"] == "STALE_404" for row in rows),
            "fetch_errors": fetch_errors, "product_page_urls": sum(row["classification"] in {"PRODUCT", "PRODUCT_ALIAS"} for row in rows),
            "category_page_urls": sum(row["classification"] == "CATEGORY" for row in rows),
            "other_urls": sum(row["classification"] == "OTHER" for row in rows),
            "unique_source_identities": len(cards),
            "bitrix_identity_count": sum(card.raw_data["identity_kind"] == "internal_product_id" for card in cards),
            "canonical_fallback_identity_count": sum(card.raw_data["identity_kind"] == "canonical_url" for card in cards),
            "sku_present": sum(bool(card.sku) for card in cards),
            "sku_missing": sum(not card.sku for card in cards),
            "alias_count": len(aliases),
            "duplicate_stable_external_ids": len(cards) - len({card.external_id for card in cards}),
            "playwright": False, "proxy": False, "sterbrust_write_methods_used": 0, "xml_generated": False,
            "category_tree_qa": tree_qa,
            "request_metrics": adapter.fetcher.metrics.as_metadata(),
            "requested_rate_policy": requested_policy.snapshot(),
            "effective_rate_policy": effective_policy.snapshot(),
            "requested_policy_equals_effective_policy": (
                requested_policy.snapshot() == effective_policy.snapshot()),
            "deadline_exceeded": deadline_exceeded,
            "max_run_duration_exceeded": deadline_exceeded,
            "termination_reason": (
                "MAX_RUN_DURATION_EXCEEDED" if deadline_exceeded else None),
            "recommended_run_status": "partial" if deadline_exceeded else "succeeded",
        }
        return OptimumLiveCrawl(cards, rows, tuple(aliases), tuple(tree), started_at, finished_at, metadata)
    finally:
        await adapter.fetcher.close()


def run_optimum_live_sync(*, rate_policy: DomainRatePolicy | None = None,
                          concurrency: int | None = None, delay: float | None = None,
                          clock=None, random_uniform=None, client=None,
                          progress_callback=None) -> OptimumLiveCrawl:
    return asyncio.run(run_optimum_live(
        rate_policy=rate_policy,
        concurrency=concurrency,
        delay=delay,
        clock=clock,
        random_uniform=random_uniform,
        client=client,
        progress_callback=progress_callback,
    ))
