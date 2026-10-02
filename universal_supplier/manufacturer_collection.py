"""Independent, paced collection of official Bekamak reference cards.

This source is intentionally distinct from both Russian price suppliers. It
never infers a numeric price, stock, canonical identity, or selection.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from .adapters.bekamak_official import parse_bekamak_official_detail
from .commercial_discovery import normalise_model
from .http_capture import CaptureStatus, EvidenceStore, PublicHttpClient, UrllibPublicHttpClient, capture_public_html
from .postgres import PostgresRepository


SITEMAP_URL = "https://www.bekamak.com/sitemap.xml"
SITE_CODE = "beka_mak_tr"
MIN_PAUSE_SECONDS = 20.0
_SLUG_RE = re.compile(r"^(?:bmsy|bmso|bms|bmdo|bmh)-\d+[a-z0-9-]*$", re.I)


def _official_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"bekamak.com", "www.bekamak.com"}:
        raise ValueError("official source must stay on HTTPS bekamak.com")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("official source URL must be query-free and credential-free")
    return url


def _official_product_url(url: str) -> str:
    _official_url(url)
    parts = urlparse(url).path.rstrip("/").split("/")
    if len(parts) != 4 or parts[1] != "urun" or parts[3] != "en" or not _SLUG_RE.fullmatch(parts[2]):
        raise ValueError("not a supported English manufacturer product URL")
    return url


class _OfficialClient:
    """Validate every redirect before network traversal, not only afterward."""

    def __init__(self, client: PublicHttpClient):
        self.client = client

    def get(self, url: str, *, timeout_seconds: float):
        _official_product_url(url)
        getter = getattr(self.client, "get_validated", None)
        if getter is None:
            response = self.client.get(url, timeout_seconds=timeout_seconds)
        else:
            response = getter(url, timeout_seconds=timeout_seconds, validator=_official_product_url)
        _official_product_url(response.final_url)
        for redirected in response.redirects:
            _official_product_url(redirected)
        return response


@dataclass(frozen=True)
class OfficialCandidate:
    url: str
    slug: str
    expected_model: str


@dataclass(frozen=True)
class OfficialManifest:
    candidates: tuple[OfficialCandidate, ...]
    review_urls: tuple[str, ...]
    sha256: str

    def as_jsonable(self) -> dict:
        return {"source": SITE_CODE, "sitemap": SITEMAP_URL, "sha256": self.sha256,
                "candidates": [asdict(item) for item in self.candidates], "review_urls": list(self.review_urls)}


def _manifest_from_xml(body: bytes) -> OfficialManifest:
    if len(body) > 10_000_000:
        raise ValueError("official sitemap exceeds safe size")
    root = ET.fromstring(body)
    urls = [item.text.strip() for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "loc" and item.text]
    if len(urls) > 20_000:
        raise ValueError("official sitemap exceeds safe URL count")
    candidates: dict[str, OfficialCandidate] = {}
    review: set[str] = set()
    for url in urls:
        parsed = urlparse(url)
        if not parsed.path.startswith("/urun/") or not parsed.path.rstrip("/").endswith("/en"):
            continue
        try:
            _official_product_url(url)
        except ValueError:
            # Do not save unsafe query/userinfo URLs even into reports.
            if parsed.query or parsed.username or parsed.password:
                continue
            review.add(url)
            continue
        slug = parsed.path.split("/")[2]
        candidates[url] = OfficialCandidate(url, slug, normalise_model(slug))
    ordered = tuple(sorted(candidates.values(), key=lambda item: item.url))
    encoded = json.dumps([asdict(item) for item in ordered], sort_keys=True, separators=(",", ":")).encode("utf-8")
    return OfficialManifest(ordered, tuple(sorted(review)), hashlib.sha256(encoded).hexdigest())


def discover_official_manifest(*, client: PublicHttpClient | None = None) -> OfficialManifest:
    client = client or UrllibPublicHttpClient()
    getter = getattr(client, "get_validated", None)
    response = (getter(SITEMAP_URL, timeout_seconds=20, validator=_official_url)
                if getter else client.get(SITEMAP_URL, timeout_seconds=20))
    _official_url(response.final_url)
    for redirected in response.redirects:
        _official_url(redirected)
    if response.status_code in {403, 429}:
        raise RuntimeError(f"official sitemap blocked: HTTP {response.status_code}")
    if response.status_code != 200 or not response.content_type or not any(
        marker in response.content_type.lower() for marker in ("xml", "text/plain")
    ):
        raise RuntimeError("official sitemap did not return HTTP 200 XML")
    return _manifest_from_xml(response.body)


def run_official_collection(*, manifest: OfficialManifest, evidence_dir: Path,
                            repository: PostgresRepository | None = None, dry_run: bool = True,
                            limit: int | None = None, pause_seconds: float = MIN_PAUSE_SECONDS,
                            client: PublicHttpClient | None = None,
                            evidence_store: EvidenceStore | None = None,
                            sleep: Callable[[float], None] = time.sleep,
                            on_progress: Callable[[dict, int, int], None] | None = None) -> dict:
    if pause_seconds < MIN_PAUSE_SECONDS and client is None:
        raise ValueError("public official collection requires at least 20 seconds between GETs")
    if not dry_run and repository is None:
        raise ValueError("repository is required for official QA write")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    # Reject a manifest changed after approval, including candidate order.
    rebuilt = _manifest_from_xml(("<urlset>" + "".join(
        f"<url><loc>{item.url}</loc></url>" for item in manifest.candidates
    ) + "</urlset>").encode())
    if rebuilt.sha256 != manifest.sha256 or rebuilt.candidates != manifest.candidates:
        raise ValueError("official candidate manifest checksum mismatch")
    selected = manifest.candidates[:limit]
    evidence_dir.mkdir(parents=True, exist_ok=True)
    client = _OfficialClient(client or UrllibPublicHttpClient())
    rows: list[dict] = []
    for index, candidate in enumerate(selected):
        if index:
            sleep(pause_seconds)
        capture = capture_public_html(candidate.url, evidence_dir=evidence_dir, client=client,
                                      timeout_seconds=20, max_attempts=1, evidence_store=evidence_store)
        row = {"source": SITE_CODE, "url": candidate.url, "expected_model": candidate.expected_model,
               "http_status": capture.http_status, "status": capture.status.value,
               "evidence_ref": capture.evidence_ref, "diagnostics": list(capture.diagnostics)}
        if capture.status is CaptureStatus.BLOCKED:
            rows.append(row)
            if on_progress: on_progress(row, index + 1, len(selected))
            break
        if capture.status is not CaptureStatus.SUCCESS or capture.capture is None or capture.evidence_body is None:
            rows.append(row)
            if on_progress: on_progress(row, index + 1, len(selected))
            continue
        try:
            product = parse_bekamak_official_detail(capture.evidence_body.decode("utf-8", "replace"),
                                                    source_url=capture.final_url)
            if normalise_model(product.supplier_model or "") != candidate.expected_model:
                raise ValueError("sitemap slug and primary product model disagree")
        except ValueError as error:
            row["status"] = "REVIEW"
            row["diagnostics"].append(str(error))
        else:
            row.update({"model": product.supplier_model, "price_state": product.price.state.value,
                        "price": None, "availability": "unknown", "property_count": len(product.technical_properties),
                        "image_count": len(product.source_images)})
            if not dry_run:
                outcome = repository.persist_commercial_observation(product, capture.capture)
                row["observation_created"] = bool(outcome["observation_created"])
                row["exact_noop"] = bool(outcome["exact_noop"])
            row["status"] = "DRY_RUN" if dry_run else "PERSISTED"
        rows.append(row)
        if on_progress: on_progress(row, index + 1, len(selected))
    return {"source": SITE_CODE, "started_at": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": manifest.sha256, "candidate_count": len(manifest.candidates),
            "selected_count": len(selected), "attempted_count": len(rows), "rows": rows}
