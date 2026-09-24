"""Bounded, read-only product discovery for passive commercial suppliers.

The module intentionally finds only candidate detail URLs.  It creates no
supplier, offer, identity decision, or database record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
from pathlib import Path
import re
from urllib.error import URLError
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from .http_capture import (
    CaptureStatus, EvidenceStore, FilesystemEvidenceStore, HttpResponse,
    PublicHttpClient, UrllibPublicHttpClient, has_blocking_interstitial,
    sanitise_html,
)


_MODEL_RE = re.compile(r"\b(BMS(?:Y|O)?[-\s]?\d+(?:[-\s]?[A-Z0-9]+)+)\b", re.I)
_PAGINATION_KEY_RE = re.compile(r"^(?:PAGEN_\d+|page)$", re.I)
_SECRET_RE = re.compile(r"(?:sessid|bitrix_sessid|cookie|authorization|access_token|api[_-]?key|token)", re.I)
_INTERVESP_HOST = "intervesp.ru"
_BEKAMAK_HOST = "beka-mak.su"


@dataclass(frozen=True)
class DiscoveredCommercialProduct:
    site: str
    product_url: str
    expected_model: str
    execution: str | None
    anchor_text: str


@dataclass(frozen=True)
class CatalogDiscoveryPage:
    site: str
    page_url: str
    products: tuple[DiscoveredCommercialProduct, ...]
    pagination_urls: tuple[str, ...]
    review_candidates: tuple["ReviewCandidate", ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewCandidate:
    """A local source contradiction that is unsafe to persist commercially."""

    site: str
    product_url: str
    model_signals: tuple[tuple[str, str], ...]
    reason: str


@dataclass(frozen=True)
class CatalogFetchResult:
    status: CaptureStatus
    requested_url: str
    final_url: str | None
    http_status: int | None
    content_type: str | None
    observed_at: datetime
    evidence_sha256: str | None
    evidence_ref: str | None
    body: str | None
    diagnostics: tuple[str, ...]


@dataclass
class _Anchor:
    href: str
    attrs: dict[str, str]
    text: list[str]


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._open: list[_Anchor] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "a":
            data = {str(key).lower(): str(value or "") for key, value in attrs}
            anchor = _Anchor(data.get("href", ""), data, [])
            self.anchors.append(anchor)
            self._open.append(anchor)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._open:
            self._open.pop()

    def handle_data(self, data: str) -> None:
        for anchor in self._open:
            anchor.text.append(data)


def normalise_model(value: str) -> str:
    compact = re.sub(r"[\s-]+", "-", value.strip().upper()).strip("-")
    # The Beka-Mak page title uses both ``BMSY-360DGH ECO`` and
    # ``BMSY-360-DGH-ECO`` for the same model token.  A separator between the
    # numeric series and its alphabetic suffix is typographical here; it is
    # not an execution boundary.  Preserve every later suffix (notably WPn)
    # verbatim so this never collapses configured machines into their base.
    match = re.fullmatch(
        r"(BMS(?:Y|O)?)-(\d+)-?([A-Z]+\d*)((?:-[A-Z0-9]+)*)", compact,
    )
    if not match:
        return compact
    family, number, suffix, trailing = match.groups()
    return f"{family}-{number}{suffix}{trailing}"


def model_and_execution(value: str) -> tuple[str, str | None]:
    model = normalise_model(value)
    parts = model.split("-")
    execution = parts[-1] if len(parts) > 1 and re.fullmatch(r"WP\d+[A-Z0-9]*", parts[-1]) else None
    return model, execution


def validate_catalog_url(url: str, *, site: str) -> str:
    """Accept only same-site category/pagination URLs with numeric paging keys."""
    parsed = urlparse(url)
    expected_host = _INTERVESP_HOST if site == "intervesp" else _BEKAMAK_HOST if site == "beka_mak" else ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {expected_host, f"www.{expected_host}"}:
        raise ValueError("catalog URL is outside the configured supplier site")
    if parsed.username or parsed.password or parsed.fragment or _SECRET_RE.search(url):
        raise ValueError("catalog URL contains unsafe credential/session material")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(not _PAGINATION_KEY_RE.fullmatch(key) or not value.isdigit() for key, value in pairs):
        raise ValueError("catalog URL query is not an explicit numeric pagination key")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", parsed.query, ""))


def fetch_catalog_page(site: str, url: str, *, evidence_dir: Path,
                       client: PublicHttpClient | None = None, timeout_seconds: float = 15.0,
                       evidence_store: EvidenceStore | None = None) -> CatalogFetchResult:
    """Fetch one category/page safely; only sanitised evidence may be saved."""
    observed_at = datetime.now(timezone.utc)
    try:
        requested = validate_catalog_url(url, site=site)
    except ValueError as error:
        return CatalogFetchResult(CaptureStatus.UNSAFE_URL, url, None, None, None, observed_at, None, None, None, (str(error),))
    client = client or UrllibPublicHttpClient()
    evidence_store = evidence_store or FilesystemEvidenceStore()
    validate = lambda candidate: validate_catalog_url(candidate, site=site)
    try:
        getter = getattr(client, "get_validated", None)
        response: HttpResponse = (
            getter(requested, timeout_seconds=timeout_seconds, validator=validate)
            if getter else client.get(requested, timeout_seconds=timeout_seconds)
        )
    except (URLError, OSError, TimeoutError) as error:
        return CatalogFetchResult(CaptureStatus.NETWORK_ERROR, requested, None, None, None, observed_at, None, None, None, (f"network_error:{type(error).__name__}",))
    try:
        final_url = validate_catalog_url(response.final_url, site=site)
        for redirect in response.redirects:
            validate_catalog_url(redirect, site=site)
    except ValueError as error:
        return CatalogFetchResult(CaptureStatus.UNSAFE_URL, requested, response.final_url, response.status_code, response.content_type, observed_at, None, None, None, (str(error),))
    if response.status_code in {403, 429}:
        return CatalogFetchResult(CaptureStatus.BLOCKED, requested, final_url, response.status_code, response.content_type, observed_at, None, None, None, (f"http_{response.status_code}",))
    if response.status_code != 200:
        return CatalogFetchResult(CaptureStatus.HTTP_STATUS, requested, final_url, response.status_code, response.content_type, observed_at, None, None, None, (f"http_{response.status_code}",))
    if not response.content_type or not response.content_type.lower().startswith("text/html"):
        return CatalogFetchResult(CaptureStatus.NON_HTML, requested, final_url, response.status_code, response.content_type, observed_at, None, None, None, ("non_html_content_type",))
    if has_blocking_interstitial(response.body):
        return CatalogFetchResult(CaptureStatus.BLOCKED, requested, final_url, response.status_code, response.content_type, observed_at, None, None, None, ("blocking_interstitial_detected",))
    evidence = sanitise_html(response.body)
    evidence_sha256 = hashlib.sha256(evidence).hexdigest()
    evidence_ref = evidence_store.save(evidence_dir=evidence_dir, final_url=final_url, evidence_sha256=evidence_sha256, body=evidence)
    return CatalogFetchResult(CaptureStatus.SUCCESS, requested, final_url, response.status_code, response.content_type,
                              observed_at, evidence_sha256, evidence_ref, evidence.decode("utf-8", errors="replace"), ())


def _same_site(url: str, site: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    expected = _INTERVESP_HOST if site == "intervesp" else _BEKAMAK_HOST
    return host in {expected, f"www.{expected}"}


def _is_product_path(url: str, site: str) -> bool:
    path = urlparse(url).path.lower()
    if site == "beka_mak":
        return path.startswith("/product/")
    # Intervesp category and detail pages share /catalog/.  Model proof in the
    # anchor text below is therefore mandatory before accepting a detail URL.
    return path.startswith("/catalog/") and path.count("/") >= 3


def _model_signals(*, text: str, title: str, url: str) -> list[tuple[str, str]]:
    signals: list[tuple[str, str]] = []
    for origin, value in (("visible_name", text), ("title", title), ("url", urlparse(url).path)):
        # Product slugs use underscores; they are only a separator, unlike a
        # model suffix, so normalize them before looking for a model token.
        model_match = _MODEL_RE.search(value.replace("_", "-"))
        if model_match:
            signals.append((origin, model_and_execution(model_match.group(1))[0]))
    return signals


def _model_base(model: str) -> str:
    return re.sub(r"-WP\d+[A-Z0-9]*$", "", model)


def _signals_are_compatible(signals: list[tuple[str, str]]) -> bool:
    return len({_model_base(model) for _, model in signals}) == 1


def discover_catalog_page(site: str, page_url: str, html: str) -> CatalogDiscoveryPage:
    """Extract only model-proven detail links and safe next-page candidates."""
    current = validate_catalog_url(page_url, site=site)
    parser = _AnchorParser()
    parser.feed(html)
    parser.close()
    entries: list[tuple[_Anchor, str, str, list[tuple[str, str]], str]] = []
    signals_by_slug: dict[str, list[tuple[str, str]]] = {}
    pages: set[str] = set()
    for anchor in parser.anchors:
        if not anchor.href:
            continue
        absolute = urljoin(current, anchor.href)
        if not _same_site(absolute, site):
            continue
        text = " ".join(anchor.text).strip()
        signals = _model_signals(text=text, title=anchor.attrs.get("title", ""), url=absolute)
        slug = urlparse(absolute).path.rstrip("/").rsplit("/", 1)[-1].lower()
        if slug and signals:
            signals_by_slug.setdefault(slug, []).extend(signals)
        entries.append((anchor, absolute, text, signals, slug))
        try:
            candidate = validate_catalog_url(absolute, site=site)
        except ValueError:
            continue
        if urlparse(candidate).query and candidate != current:
            pages.add(candidate)

    products: dict[str, DiscoveredCommercialProduct] = {}
    reviews: dict[str, ReviewCandidate] = {}
    for anchor, absolute, text, own_signals, slug in entries:
        signal_values = own_signals + signals_by_slug.get(slug, [])
        if _is_product_path(absolute, site) and signal_values:
            try:
                detail = validate_catalog_url(absolute, site=site)
            except ValueError:
                continue
            # Detail pages are query-free; a pagination link is never a card.
            if urlparse(detail).query:
                continue
            if not _signals_are_compatible(signal_values):
                reviews.setdefault(detail, ReviewCandidate(
                    site, detail, tuple(signal_values), "conflicting_url_title_or_visible_model",
                ))
                continue
            model = max((model for _, model in signal_values), key=len)
            _, execution = model_and_execution(model)
            products.setdefault(detail, DiscoveredCommercialProduct(site, detail, model, execution, text))
    return CatalogDiscoveryPage(site, current, tuple(products.values()), tuple(sorted(pages)),
                                tuple(reviews.values()))
