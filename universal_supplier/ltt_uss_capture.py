"""LTT/USS source capture: URL policy, charset-aware sanitisation, card gates.

Extends the shared, cookie-free public capture with the four newly audited
sources.  LTT Moscow/Siberia (MODX miniShop2) and LTT Yug (custom DNage CMS)
serve UTF-8; USS-Stanko serves ``windows-1251`` and therefore needs a decode
step before the shared secret-redaction regexes run.  Product-card success
criteria are source-specific because none of these sites expose the Bitrix
markers used by the generic ``inspect_html_page`` gate.  No DB dependency.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .commercial_persistence import HttpCapture
from .http_capture import (
    CaptureStatus,
    HttpResponse,
    PublicHttpClient,
    UrllibPublicHttpClient,
    _SENSITIVE_INPUT_RE,
    _SENSITIVE_INPUT_VALUE_FIRST_RE,
    _JS_SECRET_RE,
    _QUERY_SECRET_RE,
    has_blocking_interstitial,
)

SOURCE_HOSTS = {
    "ltt_msk": {"ltt-msk.ru"},
    "ltt_sib": {"lttsib.ru"},
    "ltt_yug": {"ltt-yug.ru"},
    "uss_stanko": {"uss-stanko.com"},
}

# Service roots never treated as catalog candidates.
USS_SERVICE_ROOTS = {
    "news", "contact", "company", "service", "agreement", "diplome", "foto",
    "kalendar", "index.htm", "customer", "shop", "shop2",
}
USS_CATALOG_ROOTS = {"mach", "derevo", "dmtg", "unikraft", "mrcm", "compressor"}

_YUG_PRODUCT_RE = re.compile(r"^/catalog/.+\.html$")
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.I | re.S)
_MS2_ID_ANY_RE = re.compile(
    r"<input[^>]*name=[\"']id[\"'][^>]*value=[\"'](\d+)[\"']", re.I)
_YUG_BUTTON_RE = re.compile(
    r"data-label-modal=[\"']([^\"']*)[\"'][^>]*data-product=[\"'](\d+)[\"']|"
    r"data-product=[\"'](\d+)[\"'][^>]*data-label-modal=[\"']([^\"']*)[\"']", re.I)
_USS_SPEC_ROW_RE = re.compile(
    r"<td>\s*(Цена[^<]{0,60}|Наличие)\s*</td>\s*<td[^>]*>", re.I)


def safe_source_url(supplier: str, url: str, *, sitemap: bool = False) -> str:
    """Strict same-host, query-free HTTPS policy for the four new sources."""
    parsed = urlparse(url)
    hosts = SOURCE_HOSTS[supplier]
    allowed = hosts | {"www." + h.removeprefix("www.") for h in hosts}
    if parsed.scheme != "https" or parsed.hostname not in allowed:
        raise ValueError("URL leaves the configured source host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL contains credentials, query parameters or fragment")
    if sitemap:
        path = parsed.path
        ok = (
            path in {"/robots.txt", "/sitemap.xml"}
            or path.startswith("/sitemap/")
            or re.fullmatch(r"/sitemap[-\w.]*\.xml", path, re.I) is not None
        )
        if not ok:
            raise ValueError("not an approved sitemap path")
    return url


def is_product_candidate(supplier: str, url: str) -> tuple[bool, str]:
    """Deterministic sitemap-level product classification with its reason."""
    parsed = urlparse(url)
    path = parsed.path
    if supplier in {"ltt_msk", "ltt_sib"}:
        if not path.startswith("/katalog/"):
            return False, "outside /katalog/"
        if path.endswith("/"):
            return False, "category page (trailing slash)"
        return True, "katalog detail path without trailing slash"
    if supplier == "ltt_yug":
        from .adapters.ltt_yug_role import confirmed_category_url
        if confirmed_category_url(url):
            return False, 'NON_PRODUCT_CATEGORY_PAGE:OPERATOR_CONFIRMED_EMPTY_CATEGORY_PAGE'
        if path.startswith("/tags/"):
            return False, "tag page"
        if not path.startswith("/catalog/"):
            return False, "outside /catalog/"
        if _YUG_PRODUCT_RE.match(path):
            return True, "catalog .html detail path"
        return False, "catalog directory page (no .html)"
    if supplier == "uss_stanko":
        first = path.strip("/").split("/")[0] if path.strip("/") else "index.htm"
        if first in USS_SERVICE_ROOTS:
            return False, "service page"
        if first not in USS_CATALOG_ROOTS:
            return False, "non-catalog root"
        return True, "catalog page (card/category resolved at fetch time)"
    raise ValueError(f"unknown supplier {supplier}")



def _sanitise_text(text: str) -> str:
    text = _SENSITIVE_INPUT_RE.sub(r"\1\2[REDACTED]\2", text)
    text = _SENSITIVE_INPUT_VALUE_FIRST_RE.sub(r"\1[REDACTED]", text)
    text = _JS_SECRET_RE.sub(r"\1[REDACTED]\2", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    # CSRF metadata is used by shared templates but is never source evidence.
    def redact_meta(match):
        tag = match.group(0)
        if re.search(r'\bname\s*=\s*[\"\'][^\"\']*(?:token|sessid|cookie)[^\"\']*[\"\']', tag, re.I):
            tag = re.sub(r'(\bcontent\s*=\s*)([\"\'])[^\"\']*\2', r'\1\2[REDACTED]\2', tag, flags=re.I)
        return tag
    text = re.sub(r'<meta\b[^>]*>', redact_meta, text, flags=re.I)
    return text


def sanitise_for_charset(body: bytes, charset: str | None) -> bytes:
    """Sanitise a response body decoded through its declared charset."""
    encoding = (charset or "utf-8").lower()
    if encoding in {"utf-8", "utf8"}:
        text = body.decode("utf-8", "replace")
    else:
        try:
            text = body.decode(encoding, "replace")
        except LookupError:
            text = body.decode("utf-8", "replace")
    return _sanitise_text(text).encode("utf-8")


def charset_from_response(response: HttpResponse) -> str | None:
    match = re.search(r"charset=([\w-]+)", response.content_type or "", re.I)
    return match.group(1) if match else None


# Section-role vocabulary. These are breadcrumb labels for a non-product
# section, not a title blacklist and not a URL exception list.
_EDITORIAL_SECTION_NAMES = frozenset({
    "статьи", "новости", "обзоры", "справочник", "инструкции", "блог",
})
_EDITORIAL_VOICE_RE = re.compile(
    r"в\s+(?:данн\w+|эт\w+)\s+стать|заголовок\s+данн\w+\s+стать|небольшом\s+рассказе",
    re.IGNORECASE,
)
_OWN_BLOCK_RE = re.compile(
    r"class=[\"']product_content_block[\"'][^>]*>(.*?)<div\s+class=[\"']product_content_youtube[\"']",
    re.IGNORECASE | re.DOTALL,
)
_CRUMB_ITEM_RE = re.compile(
    r"itemprop=[\"']itemListElement[\"'][^>]*>(.*?)(?:</li>|$)",
    re.IGNORECASE | re.DOTALL,
)
_BASE_HREF_RE = re.compile(r"<base\b[^>]*\bhref=[\"']([^\"']+)[\"']", re.IGNORECASE)
# Same title-token shape as source_model. A counted token needs two digits so
# a short mark such as W9D is not treated as a second sold model.
_BODY_MODEL_TOKEN_RE = re.compile(
    r"(?<!\w)[A-ZА-ЯЁ]{1,12}(?:\s+\d|[\d/-])[A-ZА-ЯЁa-zа-яё0-9*+/.\-]*(?:\s+(?:PRO|NEW|PLUS|V))?"
)


def _editorial_section_name(html: str, page_url: str) -> str | None:
    """Parent breadcrumb whose visible role and section index own this URL."""
    from .adapters.ltt import _BREADCRUMB_LINK_RE, _BREADCRUMB_NAME_RE, _BREADCRUMB_ZONE_RE, _text
    zone = _BREADCRUMB_ZONE_RE.search(html)
    if not zone:
        return None
    base_match = _BASE_HREF_RE.search(html)
    base = base_match.group(1) if base_match else page_url
    page = urlparse(page_url)
    page_path = page.path or "/"
    for chunk in _CRUMB_ITEM_RE.findall(zone.group(1)):
        name_match = _BREADCRUMB_NAME_RE.search(chunk)
        if not name_match:
            continue
        name = _text(name_match.group(1))
        if not name or name.casefold() not in _EDITORIAL_SECTION_NAMES:
            continue
        href_match = _BREADCRUMB_LINK_RE.search(chunk)
        href = href_match.group(1).strip() if href_match else ""
        if not href or href == "#":
            continue
        resolved = urlparse(urljoin(base, href))
        if resolved.hostname and page.hostname and resolved.hostname.casefold() != page.hostname.casefold():
            continue
        section_path = resolved.path or ""
        if not section_path.endswith("/"):
            continue
        if page_path.startswith(section_path) and len(page_path) > len(section_path):
            return name
    return None


def _own_content_text(html: str) -> str:
    """Card prose only. Header, footer and the media column stay out."""
    from .adapters.ltt import _text
    block = _OWN_BLOCK_RE.search(html)
    return _text(block.group(1)) if block else ""


def _distinct_body_models(value: str) -> set[str]:
    found = set()
    for raw in _BODY_MODEL_TOKEN_RE.findall(value):
        token = raw.strip("., ")
        if len(token) >= 4 and sum(character.isdigit() for character in token) >= 2:
            found.add(token.casefold())
    return found


def _modx_editorial_diagnostics(html: str, source_url: str | None, product) -> tuple[str, ...] | None:
    """Fail closed only for an editorial section that does not own one product card.

    A missing URL does not guess a role. A technical-property table keeps the
    page eligible. Mentioning another model, marketing prose, or the bare word
    «статья» outside this conjunction does not reject a product.
    """
    if not source_url or _editorial_section_name(html, source_url) is None:
        return None
    if product.technical_properties:
        return None
    own = _own_content_text(html)
    if not own:
        return None
    if _EDITORIAL_VOICE_RE.search(own):
        return ("EDITORIAL_NON_PRODUCT",)
    if product.supplier_model is None and len(_distinct_body_models(own)) >= 2:
        return ("EDITORIAL_NON_PRODUCT",)
    return None


def _card_gate(supplier: str, text: str, source_url: str | None = None) -> tuple[CaptureStatus, tuple[str, ...]]:
    """Source-specific product-card confirmation for already-decoded HTML."""
    from .adapters.ltt import parse_ltt_modx_detail
    from .adapters.ltt_yug import parse_ltt_yug_detail
    from .adapters.uss_stanko import parse_uss_stanko_detail
    from .commercial import PriceState
    if supplier in {'ltt_msk', 'ltt_sib'}:
        product = parse_ltt_modx_detail(text, supplier=supplier)
    elif supplier == 'ltt_yug':
        from .adapters.ltt_yug_role import classify_yug_page, DECISION_REF
        role = classify_yug_page(text, source_url)
        if role == 'CATEGORY_PAGE':
            return CaptureStatus.NON_PRODUCT_CATEGORY_PAGE, ('OPERATOR_CONFIRMED_EMPTY_CATEGORY_PAGE', DECISION_REF)
        if role == 'AMBIGUOUS_PAGE_ROLE':
            return CaptureStatus.AMBIGUOUS, ('AMBIGUOUS_PAGE_ROLE',)
        product = parse_ltt_yug_detail(text)
    elif supplier == 'uss_stanko':
        product = parse_uss_stanko_detail(text, source_url=source_url)
        if 'MULTI_VARIANT' in product.diagnostics:
            return CaptureStatus.AMBIGUOUS, ('MULTI_VARIANT',)
    else:
        return CaptureStatus.AMBIGUOUS, ('unknown_supplier',)
    if not product.name or not product.site_internal_id:
        return CaptureStatus.AMBIGUOUS, ('source_identity_unresolved',)
    if supplier == 'uss_stanko' and 'uss_price_row_not_found' in product.price.diagnostics:
        return CaptureStatus.AMBIGUOUS, ('uss_spec_table_missing',)
    if product.price.state == PriceState.PARSE_ERROR:
        return CaptureStatus.PARSE_ERROR, ('price_parser_error',) + product.price.diagnostics
    if product.price.state == PriceState.AMBIGUOUS:
        return CaptureStatus.AMBIGUOUS, ('commercial_value_ambiguous',)
    if supplier in {'ltt_msk', 'ltt_sib'}:
        editorial = _modx_editorial_diagnostics(text, source_url, product)
        if editorial is not None:
            return CaptureStatus.NON_PRODUCT_EDITORIAL_PAGE, editorial
    return CaptureStatus.SUCCESS, ()
    # Historical gate below is superseded by the same parser used for persistence.
    h1 = _H1_RE.search(text)
    if not h1:
        return CaptureStatus.AMBIGUOUS, ("h1_missing",)
    if supplier in {"ltt_msk", "ltt_sib"}:
        ids = set(_MS2_ID_ANY_RE.findall(text))
        has_price = re.search(r"class=[\"']product_price[\"']", text) is not None
        has_por = re.search(r"class=[\"']product_price_no_price[\"']", text) is not None
        if not ids:
            return CaptureStatus.AMBIGUOUS, ("modx_ms2_product_id_missing",)
        if not (has_price or has_por):
            return CaptureStatus.AMBIGUOUS, ("modx_price_zone_missing",)
        if has_price and has_por:
            return CaptureStatus.AMBIGUOUS, ("modx_numeric_and_por_simultaneously",)
        return CaptureStatus.SUCCESS, ()
    if supplier == "ltt_yug":
        if not _YUG_BUTTON_RE.search(text):
            return CaptureStatus.AMBIGUOUS, ("yug_data_product_button_missing",)
        if not re.search(r"\d\s*руб\.?|\bНет в наличии\b", text):
            return CaptureStatus.AMBIGUOUS, ("yug_price_or_availability_missing",)
        return CaptureStatus.SUCCESS, ()
    if supplier == "uss_stanko":
        if not re.search(r"<h1\b[^>]*class=[\"'][^\"']*zagolovok", text, re.I):
            return CaptureStatus.AMBIGUOUS, ("uss_zagolovok_h1_missing",)
        if not _USS_SPEC_ROW_RE.search(text):
            return CaptureStatus.AMBIGUOUS, ("uss_spec_table_missing",)
        return CaptureStatus.SUCCESS, ()
    return CaptureStatus.AMBIGUOUS, (f"unknown supplier {supplier}",)


@dataclass(frozen=True)
class SourceCaptureResult:
    """Capture outcome plus the charset-decoded evidence body for parsing."""

    status: CaptureStatus
    requested_url: str
    final_url: str | None
    http_status: int | None
    content_type: str | None
    charset: str | None
    observed_at: datetime
    redirects: tuple[str, ...]
    response_sha256: str | None
    evidence_sha256: str | None
    evidence_ref: str | None
    diagnostics: tuple[str, ...]
    capture: HttpCapture | None = None
    evidence_body: bytes | None = None
    transport_url: str | None = None


class _SourceValidatedClient:
    """Applies the strict per-source URL policy to every request/redirect."""

    def __init__(self, supplier: str, inner: PublicHttpClient) -> None:
        self.supplier = supplier
        self.inner = inner

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        safe_source_url(self.supplier, url)
        response = self.inner.get_validated(
            url, timeout_seconds=timeout_seconds,
            validator=lambda candidate: safe_source_url(self.supplier, candidate))
        safe_source_url(self.supplier, response.final_url)
        for redirect in response.redirects:
            safe_source_url(self.supplier, redirect)
        return response



def capture_source_html(
    url: str,
    *,
    supplier: str,
    evidence_dir: Path,
    client: PublicHttpClient | None = None,
    timeout_seconds: float = 20.0,
) -> SourceCaptureResult:
    """One public GET, validated, sanitised and gated for a LTT/USS source."""
    observed_at = datetime.now(timezone.utc)
    try:
        safe_source_url(supplier, url)
    except ValueError as error:
        return SourceCaptureResult(CaptureStatus.UNSAFE_URL, url, None, None, None, None,
                                   observed_at, (), None, None, None, (str(error),))
    wrapped = _SourceValidatedClient(supplier, client or UrllibPublicHttpClient())
    try:
        response = wrapped.get(url, timeout_seconds=timeout_seconds)
    except (ValueError, OSError, TimeoutError) as error:
        return SourceCaptureResult(CaptureStatus.NETWORK_ERROR, url, None, None, None, None,
                                   observed_at, (), None, None, None,
                                   (f"network_error:{type(error).__name__}",))
    if response.status_code in {403, 429}:
        return SourceCaptureResult(CaptureStatus.BLOCKED, url, response.final_url,
                                   response.status_code, response.content_type,
                                   charset_from_response(response), observed_at,
                                   response.redirects, None, None, None,
                                   (f"http_{response.status_code}",))
    if response.status_code != 200:
        return SourceCaptureResult(CaptureStatus.HTTP_STATUS, url, response.final_url,
                                   response.status_code, response.content_type,
                                   charset_from_response(response), observed_at,
                                   response.redirects, None, None, None,
                                   (f"http_{response.status_code}",))
    if not response.content_type or not response.content_type.lower().startswith("text/html"):
        return SourceCaptureResult(CaptureStatus.NON_HTML, url, response.final_url,
                                   response.status_code, response.content_type,
                                   charset_from_response(response), observed_at,
                                   response.redirects, None, None, None,
                                   ("non_html_content_type",))
    charset = charset_from_response(response)
    text = response.body.decode(charset or "utf-8", "replace")
    if has_blocking_interstitial(text.encode('utf-8')):
        page_status, page_diagnostics = CaptureStatus.BLOCKED, ('blocking_interstitial_detected',)
    else:
        try:
            page_status, page_diagnostics = _card_gate(supplier, text, response.final_url)
        except ValueError:
            page_status, page_diagnostics = CaptureStatus.AMBIGUOUS, ('source_canonical_unresolved',)
    response_sha256 = hashlib.sha256(response.body).hexdigest()
    raw_path = evidence_dir / 'PRIVATE_RAW' / supplier / (response_sha256 + '.bin')
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if not raw_path.exists():
        raw_path.write_bytes(response.body)
    evidence = sanitise_for_charset(response.body, charset)
    evidence_sha256 = hashlib.sha256(evidence).hexdigest()
    host = urlparse(response.final_url or url).hostname or supplier
    evidence_path = evidence_dir / supplier / f"{evidence_sha256}.html"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    if not evidence_path.exists():
        evidence_path.write_bytes(evidence)
    evidence_ref = f"capture://{host}/{evidence_sha256}.html"
    status = page_status
    diagnostics = list(page_diagnostics)
    capture = None
    if status is CaptureStatus.SUCCESS:
        capture = HttpCapture(
            url, response.final_url, response.status_code, response.content_type,
            observed_at, response_sha256, evidence_sha256, evidence_ref,
            redirects=response.redirects,
        )
    return SourceCaptureResult(status, url, response.final_url, response.status_code,
                               response.content_type, charset, observed_at,
                               response.redirects, response_sha256, evidence_sha256,
                               evidence_ref, tuple(diagnostics), capture, evidence, response.transport_url)
