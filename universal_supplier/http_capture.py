"""Bounded, cookie-free capture of public product-detail HTML.

The module is intentionally independent from PostgreSQL, the scheduler and
supplier registration.  It stores a sanitised evidence copy only; response
bytes and response headers are never persisted verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
from pathlib import Path
import re
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, HTTPHandler, ProxyHandler, Request, build_opener

from .commercial_persistence import HttpCapture


_SECRET_NAME_RE = re.compile(r"(?:sessid|bitrix_sessid|cookie|authorization|access_token|api[_-]?key|token)", re.I)
_CAPTCHA_RE = re.compile(r"(?:captcha|recaptcha|hcaptcha|cf-chl|challenge-platform)", re.I)
_SCRIPT_STYLE_RE = re.compile(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCKING_INTERSTITIAL_RE = re.compile(
    r"(?:checking (?:your|the) browser|verify (?:you are|that you are) human|attention required|unusual traffic|security check)",
    re.I,
)
_PRODUCT_MARKER_RE = re.compile(
    r"(?:\bid=[\"']elPrice[\"']|\bclass=[\"'][^\"']*prices_block|itemprop=[\"'](?:price|availability|additionalProperty)[\"']|\belTabProp\b)",
    re.I,
)
_SENSITIVE_INPUT_RE = re.compile(
    r"(<input\b[^>]*\bname\s*=\s*['\"]?[^'\"\s>]*(?:sessid|token|cookie)[^'\"\s>]*[^>]*\bvalue\s*=\s*)(['\"])[^'\"]*\2",
    re.I,
)
_SENSITIVE_INPUT_VALUE_FIRST_RE = re.compile(
    r"(<input\b[^>]*\bvalue\s*=\s*)(['\"])[^'\"]*\2(?=[^>]*\bname\s*=\s*['\"]?[^'\"\s>]*(?:sessid|token|cookie)[^'\"\s>]*[^>]*>)",
    re.I,
)
_JS_SECRET_RE = re.compile(
    r"((?:['\"](?:sessid|bitrix_sessid|access_token|api[_-]?key|token|cookie)['\"]|(?:sessid|bitrix_sessid|access_token|api[_-]?key|token|cookie))\s*[:=]\s*['\"])[^'\"]*(['\"])",
    re.I,
)
_QUERY_SECRET_RE = re.compile(r"([?&](?:sessid|bitrix_sessid|access_token|api[_-]?key|token|cookie)=)[^&#\"'\s<]+", re.I)


class CaptureStatus(StrEnum):
    SUCCESS = "SUCCESS"
    HTTP_STATUS = "HTTP_STATUS"
    NON_HTML = "NON_HTML"
    BLOCKED = "BLOCKED"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNSAFE_URL = "UNSAFE_URL"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class HttpResponse:
    final_url: str
    status_code: int
    content_type: str | None
    body: bytes
    redirects: tuple[str, ...] = ()


class PublicHttpClient(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse: ...


class EvidenceStore(Protocol):
    def save(self, *, evidence_dir: Path, final_url: str, evidence_sha256: str, body: bytes) -> str: ...


class FilesystemEvidenceStore:
    """Stores only the sanitised byte sequence, never the original response."""

    def save(self, *, evidence_dir: Path, final_url: str, evidence_sha256: str, body: bytes) -> str:
        host = urlparse(final_url).hostname or "unknown"
        path = evidence_dir / host / f"{evidence_sha256}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(body)
        return f"capture://{host}/{evidence_sha256}.html"


class _BoundedRedirectHandler(HTTPRedirectHandler):
    max_redirections = 3

    def __init__(self) -> None:
        super().__init__()
        self.redirects: list[str] = []

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if len(self.redirects) >= self.max_redirections:
            raise URLError("redirect limit exceeded")
        _validate_public_url(newurl)
        self.redirects.append(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class UrllibPublicHttpClient:
    """No cookie jar, no proxy environment and no authentication headers."""

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        _validate_public_url(url)
        redirects = _BoundedRedirectHandler()
        opener = build_opener(ProxyHandler({}), redirects, HTTPHandler(), HTTPSHandler())
        request = Request(url, headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "UniversalSupplierReadOnly/1.0 (+https://sterbrust.com)",
        })
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    final_url=response.geturl(),
                    status_code=response.status,
                    content_type=response.headers.get("Content-Type"),
                    body=response.read(),
                    redirects=tuple(redirects.redirects),
                )
        except HTTPError as error:
            return HttpResponse(
                final_url=error.geturl() or url,
                status_code=error.code,
                content_type=error.headers.get("Content-Type") if error.headers else None,
                body=error.read(),
                redirects=tuple(redirects.redirects),
            )


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("only absolute HTTP(S) URLs are permitted")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL must not contain credentials, query parameters or fragments")
    if _SECRET_NAME_RE.search(url):
        raise ValueError("URL must not contain credential or session material")


def sanitise_html(body: bytes) -> bytes:
    """Redact known session-bearing input/query values without retaining raw bytes."""
    text = body.decode("utf-8", errors="replace")
    text = _SENSITIVE_INPUT_RE.sub(r"\1\2[REDACTED]\2", text)
    text = _SENSITIVE_INPUT_VALUE_FIRST_RE.sub(r"\1\2[REDACTED]\2", text)
    text = _JS_SECRET_RE.sub(r"\1[REDACTED]\2", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    return text.encode("utf-8")


def inspect_html_page(body: bytes, *, expected_model: str | None = None) -> tuple[CaptureStatus, tuple[str, ...]]:
    """Classify a page by visible interstitial and product-card evidence.

    CAPTCHA scripts and UI dictionary keys are normal on product pages.  They
    become blocking evidence only when the document lacks a product card and
    exposes an interstitial message to the user.
    """
    html = body.decode("utf-8", errors="ignore")
    visible = _TAG_RE.sub(" ", _SCRIPT_STYLE_RE.sub(" ", html))
    has_h1 = bool(re.search(r"<h1\b[^>]*>.*?</h1\s*>", html, re.I | re.S))
    has_product_markers = bool(_PRODUCT_MARKER_RE.search(html))
    has_product_card = has_h1 and has_product_markers
    if has_product_card and expected_model:
        normalised_expected = re.sub(r"[\s-]+", "", expected_model).upper()
        title = re.search(r"<h1\b[^>]*>(.*?)</h1\s*>", html, re.I | re.S)
        title_text = _TAG_RE.sub(" ", title.group(1) if title else "")
        title_models = {
            re.sub(r"[\s-]+", "", candidate).upper()
            for candidate in re.findall(r"\bBMSY?[-\s]?\d+(?:[-\s]?[A-Z0-9]+)+\b", title_text, re.I)
        }
        if normalised_expected not in title_models:
            return CaptureStatus.AMBIGUOUS, ("expected_model_not_in_primary_product_title",)
    if has_product_card:
        return CaptureStatus.SUCCESS, ()
    if _BLOCKING_INTERSTITIAL_RE.search(visible):
        return CaptureStatus.BLOCKED, ("blocking_interstitial_detected",)
    if _CAPTCHA_RE.search(html):
        return CaptureStatus.AMBIGUOUS, ("captcha_marker_without_confirmed_product_card",)
    return CaptureStatus.AMBIGUOUS, ("product_card_not_confirmed",)


@dataclass(frozen=True)
class CaptureResult:
    status: CaptureStatus
    requested_url: str
    final_url: str | None
    http_status: int | None
    content_type: str | None
    observed_at: datetime
    redirects: tuple[str, ...]
    response_sha256: str | None
    evidence_sha256: str | None
    evidence_ref: str | None
    diagnostics: tuple[str, ...]
    capture: HttpCapture | None = None


def capture_public_html(
    url: str,
    *,
    evidence_dir: Path,
    client: PublicHttpClient | None = None,
    timeout_seconds: float = 15.0,
    max_attempts: int = 2,
    retry_delay_seconds: float = 0.0,
    evidence_store: EvidenceStore | None = None,
    expected_model: str | None = None,
) -> CaptureResult:
    """Capture one public detail page, retrying only transient network/5xx errors."""
    observed_at = datetime.now(timezone.utc)
    try:
        _validate_public_url(url)
    except ValueError as error:
        return CaptureResult(CaptureStatus.UNSAFE_URL, url, None, None, None, observed_at, (), None, None, None, (str(error),))
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    client = client or UrllibPublicHttpClient()
    evidence_store = evidence_store or FilesystemEvidenceStore()
    errors: list[str] = []
    for attempt in range(max_attempts):
        observed_at = datetime.now(timezone.utc)
        try:
            response = client.get(url, timeout_seconds=timeout_seconds)
        except (URLError, OSError, TimeoutError) as error:
            errors.append(f"network_error:{type(error).__name__}")
            if attempt + 1 < max_attempts:
                if retry_delay_seconds:
                    time.sleep(retry_delay_seconds)
                continue
            return CaptureResult(CaptureStatus.NETWORK_ERROR, url, None, None, None, observed_at, (), None, None, None, tuple(errors))
        try:
            _validate_public_url(response.final_url)
            for redirect in response.redirects:
                _validate_public_url(redirect)
        except ValueError as error:
            return CaptureResult(CaptureStatus.UNSAFE_URL, url, response.final_url, response.status_code, response.content_type, observed_at, response.redirects, None, None, None, (str(error),))
        if response.status_code in {403, 429}:
            return CaptureResult(CaptureStatus.BLOCKED, url, response.final_url, response.status_code, response.content_type, observed_at, response.redirects, None, None, None, (f"http_{response.status_code}",))
        if response.status_code >= 500 and attempt + 1 < max_attempts:
            errors.append(f"http_{response.status_code}")
            if retry_delay_seconds:
                time.sleep(retry_delay_seconds)
            continue
        if response.status_code != 200:
            return CaptureResult(CaptureStatus.HTTP_STATUS, url, response.final_url, response.status_code, response.content_type, observed_at, response.redirects, None, None, None, tuple(errors + [f"http_{response.status_code}"]))
        if not response.content_type or not response.content_type.lower().startswith("text/html"):
            return CaptureResult(CaptureStatus.NON_HTML, url, response.final_url, response.status_code, response.content_type, observed_at, response.redirects, None, None, None, ("non_html_content_type",))
        page_status, page_diagnostics = inspect_html_page(response.body, expected_model=expected_model)
        if page_status is not CaptureStatus.SUCCESS:
            return CaptureResult(page_status, url, response.final_url, response.status_code, response.content_type, observed_at, response.redirects, None, None, None, page_diagnostics)
        response_sha256 = hashlib.sha256(response.body).hexdigest()
        evidence = sanitise_html(response.body)
        evidence_sha256 = hashlib.sha256(evidence).hexdigest()
        evidence_ref = evidence_store.save(
            evidence_dir=evidence_dir, final_url=response.final_url,
            evidence_sha256=evidence_sha256, body=evidence,
        )
        capture = HttpCapture(
            url, response.final_url, response.status_code, response.content_type, observed_at,
            response_sha256, evidence_sha256, evidence_ref, redirects=response.redirects,
        )
        return CaptureResult(CaptureStatus.SUCCESS, url, response.final_url, response.status_code, response.content_type, observed_at, response.redirects, response_sha256, evidence_sha256, evidence_ref, tuple(errors), capture)
    raise AssertionError("unreachable")
