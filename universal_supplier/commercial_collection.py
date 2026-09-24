"""Bounded read-only catalog collection for passive commercial suppliers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Callable, Iterable

from .adapters.bekamak import parse_bekamak_detail
from .adapters.intervesp import parse_intervesp_detail
from .commercial import ReadOnlySupplierProduct
from .commercial_discovery import (
    CatalogDiscoveryPage, DiscoveredCommercialProduct, discover_catalog_page,
    fetch_catalog_page, normalise_model,
)
from .http_capture import CaptureStatus, EvidenceStore, PublicHttpClient, capture_public_html
from .postgres import PostgresRepository


SITE_SEEDS: dict[str, tuple[str, ...]] = {
    "intervesp": (
        "https://intervesp.ru/catalog/lentochnopilnye-ruchnye-metall/",
        "https://intervesp.ru/catalog/konsolnye-poluavtomaticheskie-lentochnopilnye-stanki/",
    ),
    "beka_mak": (
        "https://beka-mak.su/catalog/ruchnye-lentochnopilnye-stanki/",
        "https://beka-mak.su/catalog/poluavtomaticheskie-lentochnopilnye-stanki/",
    ),
}
MAX_CATALOG_PAGES_PER_SITE = 2
MIN_SITE_PAUSE_SECONDS = 20.0


@dataclass(frozen=True)
class CollectionRow:
    site: str
    requested_url: str
    expected_model: str | None
    execution: str | None
    phase: str
    result: str
    http_status: int | None = None
    external_id: str | None = None
    parsed_model: str | None = None
    price_state: str | None = None
    price: str | None = None
    currency: str | None = None
    availability: str | None = None
    observation_created: bool | None = None
    exact_noop: bool | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class SiteCollectionResult:
    site: str
    discovered_urls: int
    catalog_pages_fetched: int
    product_attempts: int
    fetched_cards: int
    numeric_prices: int
    prices_on_request: int
    errors: int
    blocked: bool
    observations_created: int
    exact_repeats: int


@dataclass(frozen=True)
class CommercialCollectionResult:
    started_at: str
    finished_at: str
    dry_run: bool
    site_results: tuple[SiteCollectionResult, ...]
    rows: tuple[CollectionRow, ...]

    def as_jsonable(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "sites": [asdict(item) for item in self.site_results],
            "rows": [asdict(item) for item in self.rows],
        }


def _parser(site: str) -> Callable[..., ReadOnlySupplierProduct]:
    return parse_intervesp_detail if site == "intervesp" else parse_bekamak_detail


def _site_discovery(site: str, *, evidence_dir: Path, client: PublicHttpClient | None,
                    pause_seconds: float, sleep: Callable[[float], None], evidence_store: EvidenceStore | None) -> tuple[list[DiscoveredCommercialProduct], list[CollectionRow], bool, int]:
    """Discover at most two catalog pages and stop a site on access blocking."""
    queue = list(SITE_SEEDS[site])
    seen: set[str] = set()
    candidates: dict[str, DiscoveredCommercialProduct] = {}
    rows: list[CollectionRow] = []
    blocked = False
    page_count = 0
    while queue and page_count < MAX_CATALOG_PAGES_PER_SITE and not blocked:
        url = queue.pop(0)
        if url in seen:
            continue
        if page_count:
            sleep(pause_seconds)
        seen.add(url)
        fetched = fetch_catalog_page(site, url, evidence_dir=evidence_dir, client=client, evidence_store=evidence_store)
        page_count += 1
        if fetched.status is not CaptureStatus.SUCCESS or fetched.body is None or fetched.final_url is None:
            rows.append(CollectionRow(site, url, None, None, "discovery", fetched.status.value, fetched.http_status,
                                      diagnostics=fetched.diagnostics))
            blocked = fetched.status is CaptureStatus.BLOCKED
            continue
        discovery: CatalogDiscoveryPage = discover_catalog_page(site, fetched.final_url, fetched.body)
        for item in discovery.products:
            candidates.setdefault(item.product_url, item)
        queue.extend(page for page in discovery.pagination_urls if page not in seen and page not in queue)
        rows.append(CollectionRow(site, fetched.final_url, None, None, "discovery", "SUCCESS", fetched.http_status,
                                  diagnostics=(f"discovered_cards:{len(discovery.products)}", f"pagination:{len(discovery.pagination_urls)}")))
    return list(candidates.values()), rows, blocked, page_count


def run_commercial_collection(*, sites: Iterable[str], limit: int, pause_seconds: float,
                              evidence_dir: Path, dry_run: bool, repository: PostgresRepository | None = None,
                              client: PublicHttpClient | None = None, sleep: Callable[[float], None] = time.sleep,
                              evidence_store: EvidenceStore | None = None) -> CommercialCollectionResult:
    """Run the bounded collection without matching, selection, or scheduling."""
    if not 1 <= limit <= 30:
        raise ValueError("limit must be between 1 and 30")
    if pause_seconds < MIN_SITE_PAUSE_SECONDS and client is None:
        raise ValueError(f"public collection requires pause_seconds >= {MIN_SITE_PAUSE_SECONDS:g}")
    if not dry_run and repository is None:
        raise ValueError("repository is required unless dry_run is set")
    started = datetime.now(timezone.utc).isoformat()
    rows: list[CollectionRow] = []
    results: list[SiteCollectionResult] = []
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for site in sites:
        if site not in SITE_SEEDS:
            raise ValueError(f"unsupported commercial site: {site}")
        candidates, discovery_rows, blocked, page_count = _site_discovery(
            site, evidence_dir=evidence_dir / "catalog", client=client, pause_seconds=pause_seconds, sleep=sleep,
            evidence_store=evidence_store,
        )
        rows.extend(discovery_rows)
        fetched_cards = numeric = on_request = errors = created = repeats = 0
        for position, candidate in enumerate(candidates[:limit]):
            if blocked:
                break
            if page_count or position:
                sleep(pause_seconds)
            captured = capture_public_html(candidate.product_url, evidence_dir=evidence_dir / "products", client=client,
                                            timeout_seconds=15, max_attempts=2, retry_delay_seconds=pause_seconds,
                                            expected_model=candidate.expected_model, evidence_store=evidence_store)
            if captured.status is not CaptureStatus.SUCCESS or captured.capture is None or captured.final_url is None or captured.evidence_sha256 is None:
                rows.append(CollectionRow(site, candidate.product_url, candidate.expected_model, candidate.execution,
                                          "product", captured.status.value, captured.http_status, diagnostics=captured.diagnostics))
                errors += 1
                blocked = captured.status is CaptureStatus.BLOCKED
                continue
            if captured.evidence_body is None:
                raise RuntimeError("successful capture did not return sanitised evidence")
            product = _parser(site)(captured.evidence_body.decode("utf-8", errors="replace"), source_url=captured.final_url)
            parsed_model = normalise_model(product.supplier_model or "") if product.supplier_model else None
            diagnostics = list(product.diagnostics + product.price.diagnostics)
            if product.site_internal_id is None:
                rows.append(CollectionRow(site, candidate.product_url, candidate.expected_model, candidate.execution,
                                          "product", "AMBIGUOUS", captured.http_status, parsed_model=parsed_model,
                                          diagnostics=tuple(diagnostics + ["site_internal_id_not_found"])))
                errors += 1
                continue
            if parsed_model and parsed_model != candidate.expected_model:
                rows.append(CollectionRow(site, candidate.product_url, candidate.expected_model, candidate.execution,
                                          "product", "AMBIGUOUS", captured.http_status, product.site_internal_id, parsed_model,
                                          diagnostics=tuple(diagnostics + ["discovery_model_adapter_model_mismatch"])))
                errors += 1
                continue
            persisted = None if dry_run else repository.persist_commercial_observation(product, captured.capture)
            fetched_cards += 1
            numeric += product.price.state.value == "numeric_public"
            on_request += product.price.state.value == "price_on_request"
            created += bool(persisted and persisted["observation_created"])
            repeats += bool(persisted and persisted["exact_noop"])
            rows.append(CollectionRow(site, candidate.product_url, candidate.expected_model, candidate.execution,
                                      "product", "DRY_RUN" if dry_run else "PERSISTED", captured.http_status,
                                      product.site_internal_id, parsed_model, product.price.state.value,
                                      None if product.price.current_price is None else str(product.price.current_price),
                                      product.price.currency, product.availability,
                                      None if persisted is None else persisted["observation_created"],
                                      None if persisted is None else persisted["exact_noop"], tuple(diagnostics)))
        results.append(SiteCollectionResult(site, len(candidates), page_count, min(len(candidates), limit), fetched_cards,
                                            numeric, on_request, errors, blocked, created, repeats))
    return CommercialCollectionResult(started, datetime.now(timezone.utc).isoformat(), dry_run, tuple(results), tuple(rows))


def write_collection_report(result: CommercialCollectionResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.as_jsonable(), ensure_ascii=False, indent=2), encoding="utf-8")
