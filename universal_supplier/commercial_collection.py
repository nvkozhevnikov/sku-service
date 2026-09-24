"""Bounded read-only catalog collection for passive commercial suppliers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import time
from typing import Callable, Iterable

from .adapters.bekamak import parse_bekamak_detail
from .adapters.intervesp import parse_intervesp_detail
from .commercial import ReadOnlySupplierProduct
from .commercial_discovery import (
    CatalogDiscoveryPage, DiscoveredCommercialProduct, ReviewCandidate, discover_catalog_page,
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
    evidence_ref: str | None = None


@dataclass(frozen=True)
class SiteCollectionResult:
    site: str
    discovered_urls: int
    catalog_pages_fetched: int
    candidate_offset: int
    candidates_selected: int
    product_attempts: int
    fetched_cards: int
    numeric_prices: int
    prices_on_request: int
    errors: int
    reviews: int
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


@dataclass(frozen=True)
class CandidateManifest:
    """Stable, evidence-free candidate list used to guard resumed batches."""

    sites: dict[str, tuple[DiscoveredCommercialProduct, ...]]

    def as_jsonable(self) -> dict:
        site_data = {}
        for site, candidates in self.sites.items():
            items = [
                {"url": item.product_url, "expected_model": item.expected_model, "execution": item.execution}
                for item in candidates
            ]
            encoded = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            site_data[site] = {"sha256": hashlib.sha256(encoded).hexdigest(), "candidates": items}
        return {"format": "commercial-candidate-manifest-v1", "sites": site_data}

    @classmethod
    def from_jsonable(cls, data: dict) -> "CandidateManifest":
        if data.get("format") != "commercial-candidate-manifest-v1" or not isinstance(data.get("sites"), dict):
            raise ValueError("unsupported candidate manifest")
        sites: dict[str, tuple[DiscoveredCommercialProduct, ...]] = {}
        for site, payload in data["sites"].items():
            items = payload.get("candidates") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ValueError("candidate manifest has no candidate list")
            candidates = tuple(
                DiscoveredCommercialProduct(site, item["url"], item["expected_model"], item.get("execution"), "")
                for item in items
                if isinstance(item, dict) and isinstance(item.get("url"), str) and isinstance(item.get("expected_model"), str)
            )
            if len(candidates) != len(items):
                raise ValueError("candidate manifest has invalid candidate fields")
            rebuilt = cls({site: candidates}).as_jsonable()["sites"][site]["sha256"]
            if payload.get("sha256") != rebuilt:
                raise ValueError("candidate manifest checksum mismatch")
            sites[site] = candidates
        return cls(sites)


class CandidateManifestMismatch(RuntimeError):
    pass


class _RequestPacer:
    """Applies one minimum pause to every public GET across all suppliers."""

    def __init__(self, pause_seconds: float, sleep: Callable[[float], None]) -> None:
        self.pause_seconds = pause_seconds
        self.sleep = sleep
        self.request_count = 0

    def before_request(self) -> None:
        if self.request_count:
            self.sleep(self.pause_seconds)
        self.request_count += 1


def _parser(site: str) -> Callable[..., ReadOnlySupplierProduct]:
    return parse_intervesp_detail if site == "intervesp" else parse_bekamak_detail


def _site_discovery(site: str, *, evidence_dir: Path, client: PublicHttpClient | None,
                    before_request: Callable[[], None], evidence_store: EvidenceStore | None,
                    catalog_page_limit: int = MAX_CATALOG_PAGES_PER_SITE) -> tuple[list[DiscoveredCommercialProduct], list[CollectionRow], bool, int]:
    """Discover a caller-bounded number of catalog pages; stop on blocking."""
    if not 1 <= catalog_page_limit <= 20:
        raise ValueError("catalog_page_limit must be between 1 and 20")
    queue = list(SITE_SEEDS[site])
    seen: set[str] = set()
    candidates: dict[str, DiscoveredCommercialProduct] = {}
    rows: list[CollectionRow] = []
    blocked = False
    page_count = 0
    while queue and page_count < catalog_page_limit and not blocked:
        url = queue.pop(0)
        if url in seen:
            continue
        before_request()
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
        for review in discovery.review_candidates:
            rows.append(CollectionRow(site, review.product_url, None, None, "review", "REVIEW", fetched.http_status,
                                      diagnostics=tuple(f"{origin}:{model}" for origin, model in review.model_signals) + (review.reason,),
                                      evidence_ref=fetched.evidence_ref))
        queue.extend(page for page in discovery.pagination_urls if page not in seen and page not in queue)
        rows.append(CollectionRow(site, fetched.final_url, None, None, "discovery", "SUCCESS", fetched.http_status,
                                  diagnostics=(f"discovered_cards:{len(discovery.products)}", f"pagination:{len(discovery.pagination_urls)}")))
    # The resume offset is a public CLI contract, so candidate order must not
    # depend on incidental HTML anchor order or category traversal order.
    return sorted(candidates.values(), key=lambda item: (item.expected_model, item.product_url)), rows, blocked, page_count


def discover_commercial_candidates(*, sites: Iterable[str], evidence_dir: Path,
                                   pause_seconds: float, client: PublicHttpClient | None = None,
                                   sleep: Callable[[float], None] = time.sleep,
                                   evidence_store: EvidenceStore | None = None,
                                   catalog_page_limit: int = MAX_CATALOG_PAGES_PER_SITE) -> tuple[CandidateManifest, tuple[CollectionRow, ...]]:
    """Fetch only bounded category pages and return a stable candidate manifest."""
    if pause_seconds < MIN_SITE_PAUSE_SECONDS and client is None:
        raise ValueError(f"public discovery requires pause_seconds >= {MIN_SITE_PAUSE_SECONDS:g}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    pacer = _RequestPacer(pause_seconds, sleep)
    result: dict[str, tuple[DiscoveredCommercialProduct, ...]] = {}
    rows: list[CollectionRow] = []
    for site in sites:
        if site not in SITE_SEEDS:
            raise ValueError(f"unsupported commercial site: {site}")
        candidates, discovery_rows, blocked, _ = _site_discovery(
            site, evidence_dir=evidence_dir / "catalog", client=client, before_request=pacer.before_request,
            evidence_store=evidence_store, catalog_page_limit=catalog_page_limit,
        )
        rows.extend(discovery_rows)
        if blocked:
            raise CandidateManifestMismatch(f"candidate discovery blocked for {site}")
        result[site] = tuple(candidates)
    return CandidateManifest(result), tuple(rows)


def run_commercial_collection(*, sites: Iterable[str], limit: int, pause_seconds: float,
                              evidence_dir: Path, dry_run: bool, repository: PostgresRepository | None = None,
                              client: PublicHttpClient | None = None, sleep: Callable[[float], None] = time.sleep,
                              evidence_store: EvidenceStore | None = None,
                              candidate_offset: int = 0,
                              expected_manifest: CandidateManifest | None = None,
                              catalog_page_limit: int = MAX_CATALOG_PAGES_PER_SITE) -> CommercialCollectionResult:
    """Run the bounded collection without matching, selection, or scheduling."""
    if not 1 <= limit <= 30:
        raise ValueError("limit must be between 1 and 30")
    if candidate_offset < 0:
        raise ValueError("candidate_offset must not be negative")
    if pause_seconds < MIN_SITE_PAUSE_SECONDS and client is None:
        raise ValueError(f"public collection requires pause_seconds >= {MIN_SITE_PAUSE_SECONDS:g}")
    if not dry_run and repository is None:
        raise ValueError("repository is required unless dry_run is set")
    started = datetime.now(timezone.utc).isoformat()
    rows: list[CollectionRow] = []
    results: list[SiteCollectionResult] = []
    evidence_dir.mkdir(parents=True, exist_ok=True)
    pacer = _RequestPacer(pause_seconds, sleep)
    for site in sites:
        if site not in SITE_SEEDS:
            raise ValueError(f"unsupported commercial site: {site}")
        candidates, discovery_rows, blocked, page_count = _site_discovery(
            site, evidence_dir=evidence_dir / "catalog", client=client, before_request=pacer.before_request,
            evidence_store=evidence_store, catalog_page_limit=catalog_page_limit,
        )
        rows.extend(discovery_rows)
        if expected_manifest is not None:
            expected = expected_manifest.sites.get(site)
            current = tuple(candidates)
            expected_sha = CandidateManifest({site: expected}).as_jsonable()["sites"][site]["sha256"] if expected is not None else None
            current_sha = CandidateManifest({site: current}).as_jsonable()["sites"][site]["sha256"]
            if expected_sha != current_sha:
                raise CandidateManifestMismatch(f"candidate manifest changed for {site}; no product capture was started")
        fetched_cards = numeric = on_request = errors = reviews = created = repeats = 0
        selected_candidates = candidates[candidate_offset:candidate_offset + limit]
        for position, candidate in enumerate(selected_candidates):
            if blocked:
                break
            pacer.before_request()
            captured = capture_public_html(candidate.product_url, evidence_dir=evidence_dir / "products", client=client,
                                            timeout_seconds=15, max_attempts=2, retry_delay_seconds=pause_seconds,
                                            expected_model=candidate.expected_model, evidence_store=evidence_store)
            if captured.status is not CaptureStatus.SUCCESS or captured.capture is None or captured.final_url is None or captured.evidence_sha256 is None:
                is_review = captured.status is CaptureStatus.AMBIGUOUS
                rows.append(CollectionRow(site, candidate.product_url, candidate.expected_model, candidate.execution,
                                          "product", "REVIEW" if is_review else captured.status.value, captured.http_status,
                                          diagnostics=captured.diagnostics, evidence_ref=captured.evidence_ref))
                if is_review:
                    reviews += 1
                else:
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
                                          "product", "REVIEW", captured.http_status, parsed_model=parsed_model,
                                          diagnostics=tuple(diagnostics + ["site_internal_id_not_found"]), evidence_ref=captured.evidence_ref))
                reviews += 1
                continue
            if parsed_model and parsed_model != candidate.expected_model:
                rows.append(CollectionRow(site, candidate.product_url, candidate.expected_model, candidate.execution,
                                          "product", "REVIEW", captured.http_status, product.site_internal_id, parsed_model,
                                          diagnostics=tuple(diagnostics + ["discovery_model_adapter_model_mismatch"]), evidence_ref=captured.evidence_ref))
                reviews += 1
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
                                      None if persisted is None else persisted["exact_noop"], tuple(diagnostics), captured.evidence_ref))
        results.append(SiteCollectionResult(site, len(candidates), page_count, candidate_offset, len(selected_candidates),
                                            len(selected_candidates), fetched_cards,
                                            numeric, on_request, errors, reviews, blocked, created, repeats))
    return CommercialCollectionResult(started, datetime.now(timezone.utc).isoformat(), dry_run, tuple(results), tuple(rows))


def write_collection_report(result: CommercialCollectionResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.as_jsonable(), ensure_ascii=False, indent=2), encoding="utf-8")


def write_candidate_manifest(manifest: CandidateManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.as_jsonable(), ensure_ascii=False, indent=2), encoding="utf-8")


def write_review_report(rows: Iterable[CollectionRow], path: Path) -> None:
    reviews = [asdict(row) for row in rows if row.result == "REVIEW"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reviews": reviews}, ensure_ascii=False, indent=2), encoding="utf-8")
