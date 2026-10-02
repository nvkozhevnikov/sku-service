"""Full, sitemap-backed manual QA collection for three independent sources.

No scheduler, matching, selection, canonical writes, or ESOL output. The
repository call is only the narrow Stage 3A capture/observation transaction.
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

from .adapters.bekamak import parse_bekamak_detail
from .adapters.bekamak_official import parse_bekamak_official_detail
from .adapters.intervesp import parse_intervesp_detail
from .commercial_discovery import _MODEL_RE, normalise_model
from .http_capture import CaptureStatus, EvidenceStore, PublicHttpClient, UrllibPublicHttpClient, capture_public_html
from .postgres import PostgresRepository


ROOT_SITEMAPS = {
    "intervesp": "https://intervesp.ru/sitemap.xml",
    "beka_mak": "https://beka-mak.su/sitemap.xml",
    "beka_mak_tr": "https://www.bekamak.com/sitemap.xml",
}
_HOSTS = {site: urlparse(url).hostname for site, url in ROOT_SITEMAPS.items()}
_PARSERS = {"intervesp": parse_intervesp_detail, "beka_mak": parse_bekamak_detail,
            "beka_mak_tr": parse_bekamak_official_detail}
_SITEMAP_PATH = re.compile(r"^/sitemap(?:-iblock-\d+|-files)?\.xml$", re.I)


def _safe_source_url(site: str, url: str, *, sitemap: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {_HOSTS[site], "www." + _HOSTS[site].removeprefix("www.")}:
        raise ValueError("URL leaves the configured source host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL contains credentials, query parameters or fragment")
    if sitemap:
        if not _SITEMAP_PATH.fullmatch(parsed.path):
            raise ValueError("not an approved sitemap path")
    elif site == "intervesp":
        if not parsed.path.startswith("/catalog/") or "beka-mak" not in parsed.path.lower():
            raise ValueError("not a Bekamak Intervesp detail path")
    elif site == "beka_mak":
        if not parsed.path.startswith("/product/"):
            raise ValueError("not a Beka-Mak detail path")
    else:
        parts = parsed.path.rstrip("/").split("/")
        if len(parts) != 4 or parts[1] != "urun" or parts[3] != "en":
            raise ValueError("not an English manufacturer product path")
    return url


@dataclass(frozen=True)
class FullCandidate:
    source: str
    url: str
    expected_model: str


@dataclass(frozen=True)
class FullManifest:
    candidates: tuple[FullCandidate, ...]
    sitemap_counts: dict[str, int]
    review_urls: tuple[dict, ...]
    sha256: str

    def as_jsonable(self) -> dict:
        return {"format": "supplier-full-manifest-v1", "sha256": self.sha256,
                "candidates": [asdict(item) for item in self.candidates],
                "sitemap_counts": self.sitemap_counts, "review_urls": list(self.review_urls)}

    @classmethod
    def from_jsonable(cls, value: dict) -> "FullManifest":
        if value.get("format") != "supplier-full-manifest-v1":
            raise ValueError("unsupported full manifest")
        candidates = tuple(FullCandidate(**item) for item in value["candidates"])
        if _candidate_sha(candidates) != value.get("sha256"):
            raise ValueError("full manifest SHA-256 mismatch")
        return cls(candidates, value.get("sitemap_counts", {}), tuple(value.get("review_urls", [])), value["sha256"])


def _candidate_sha(candidates: tuple[FullCandidate, ...]) -> str:
    body = json.dumps([asdict(item) for item in candidates], ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class PublicPacer:
    def __init__(self, pause_seconds: float = 20.0, sleep: Callable[[float], None] = time.sleep) -> None:
        if pause_seconds < 20.0:
            raise ValueError("public GET pause must be at least 20 seconds")
        self.pause_seconds = pause_seconds
        self.sleep = sleep
        self.last_request: float | None = None

    def before_get(self) -> None:
        if self.last_request is not None:
            # Sleep after the preceding response, not merely between request
            # start times: a slow response must not erase the site pause.
            self.sleep(self.pause_seconds)
        self.last_request = time.monotonic()


class _SourceClient:
    def __init__(self, site: str, client: PublicHttpClient):
        self.site = site
        self.client = client

    def get(self, url: str, *, timeout_seconds: float):
        _safe_source_url(self.site, url)
        getter = getattr(self.client, "get_validated", None)
        validator = lambda candidate: _safe_source_url(self.site, candidate)
        response = (getter(url, timeout_seconds=timeout_seconds, validator=validator)
                    if getter else self.client.get(url, timeout_seconds=timeout_seconds))
        validator(response.final_url)
        for redirect in response.redirects:
            validator(redirect)
        return response


def _sitemap_xml(site: str, url: str, client: PublicHttpClient, pacer: PublicPacer,
                 evidence_log: list[dict] | None = None) -> ET.Element:
    validator = lambda candidate: _safe_source_url(site, candidate, sitemap=True)
    validator(url)
    pacer.before_get()
    getter = getattr(client, "get_validated", None)
    response = (getter(url, timeout_seconds=30, validator=validator)
                if getter else client.get(url, timeout_seconds=30))
    validator(response.final_url)
    for redirect in response.redirects:
        validator(redirect)
    if response.status_code in {403, 429}:
        raise RuntimeError(f"{site} sitemap blocked with HTTP {response.status_code}")
    if response.status_code != 200 or not response.content_type or "xml" not in response.content_type.lower():
        raise RuntimeError(f"{site} sitemap did not return HTTP 200 XML")
    if len(response.body) > 20_000_000:
        raise RuntimeError(f"{site} sitemap exceeds size limit")
    parsed = ET.fromstring(response.body)
    if evidence_log is not None:
        evidence_log.append({"source": site, "requested_url": url,
                             "final_url": response.final_url, "http_status": response.status_code,
                             "content_type": response.content_type,
                             "raw_sha256": hashlib.sha256(response.body).hexdigest(),
                             "xml_root": parsed.tag.rsplit("}", 1)[-1],
                             "loc_count": len(_locs(parsed))})
    return parsed


def _locs(root: ET.Element) -> list[str]:
    return [item.text.strip() for item in root.iter()
            if item.tag.rsplit("}", 1)[-1] == "loc" and item.text]


def discover_full_manifest(*, client: PublicHttpClient | None = None,
                           pacer: PublicPacer | None = None,
                           evidence_log: list[dict] | None = None) -> FullManifest:
    """Read approved sitemaps; product identity remains provisional until detail GET."""
    client = client or UrllibPublicHttpClient()
    pacer = pacer or PublicPacer()
    candidates: dict[tuple[str, str], FullCandidate] = {}
    reviews: list[dict] = []
    counts: dict[str, int] = {}
    for site, root_url in ROOT_SITEMAPS.items():
        root = _sitemap_xml(site, root_url, client, pacer, evidence_log)
        root_kind = root.tag.rsplit("}", 1)[-1]
        if root_kind == "sitemapindex":
            child_urls = [url for url in _locs(root) if not urlparse(url).path.endswith("sitemap-files.xml")]
            if len(child_urls) > 50:
                raise RuntimeError(f"{site} sitemap index exceeds child limit")
        elif root_kind == "urlset":
            child_urls = [root_url]
        else:
            raise RuntimeError(f"{site} sitemap has unexpected root")
        for child_url in child_urls:
            child = root if child_url == root_url else _sitemap_xml(site, child_url, client, pacer, evidence_log)
            if child.tag.rsplit("}", 1)[-1] != "urlset":
                raise RuntimeError(f"{site} nested sitemap is not a urlset")
            urls = _locs(child)
            counts[f"{site}:{urlparse(child_url).path}"] = len(urls)
            for url in urls:
                try:
                    _safe_source_url(site, url)
                except ValueError:
                    continue
                path = urlparse(url).path.rstrip("/")
                raw_slug = path.split("/")[2] if site == "beka_mak_tr" else path.rsplit("/", 1)[-1]
                if site == "beka_mak_tr" and not re.fullmatch(r"[a-z0-9-]+", raw_slug, re.I):
                    reviews.append({"source": site, "url": url, "reason": "unstable_official_model_slug"})
                    continue
                slug = raw_slug.replace("_", "-")
                if site == "beka_mak_tr":
                    model = normalise_model(slug)
                else:
                    found = _MODEL_RE.search(slug)
                    if not found:
                        continue
                    model = normalise_model(found.group(1))
                candidates[(site, url)] = FullCandidate(site, url, model)
    ordered = tuple(sorted(candidates.values(), key=lambda item: (item.source, item.url)))
    return FullManifest(ordered, counts, tuple(reviews), _candidate_sha(ordered))


def verify_qa_identity(repository: PostgresRepository) -> dict:
    """Fail closed before any QA write. No connection string is accepted here."""
    with repository.connection.cursor() as cursor:
        cursor.execute("""SELECT current_database(),current_user,current_setting('server_version'),
                       pg_is_in_recovery(),system_identifier FROM pg_control_system()""")
        identity = cursor.fetchone()
        cursor.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
        privileges = cursor.fetchone()
    expected = ("stage4_commercial_qa", "stage4_runtime", "17.11", False, 7689025282387590508)
    if identity != expected or privileges != (False, False, False):
        raise RuntimeError("QA database identity or runtime privileges changed")
    return {"database": identity[0], "role": identity[1], "version": identity[2],
            "in_recovery": identity[3], "system_identifier": identity[4]}


def run_full_collection(*, manifest: FullManifest, repository: PostgresRepository | None,
                        evidence_dir: Path, report_path: Path, dry_run: bool,
                        sources: tuple[str, ...] = ("intervesp", "beka_mak", "beka_mak_tr"),
                        limit: int | None = None, offset: int = 0,
                        client: PublicHttpClient | None = None, pacer: PublicPacer | None = None,
                        evidence_store: EvidenceStore | None = None,
                        write_identity_validator: Callable[[PostgresRepository], dict] | None = None,
                        on_progress: Callable[[dict, int, int], None] | None = None) -> dict:
    if _candidate_sha(manifest.candidates) != manifest.sha256:
        raise ValueError("full candidate manifest SHA-256 mismatch")
    if offset < 0 or limit is not None and limit < 1:
        raise ValueError("invalid full collection offset or limit")
    if not dry_run:
        if repository is None:
            raise ValueError("QA write requires a repository")
        (write_identity_validator or verify_qa_identity)(repository)
    client = client or UrllibPublicHttpClient()
    pacer = pacer or PublicPacer()
    selected = [item for item in manifest.candidates if item.source in sources][offset:]
    if limit is not None:
        selected = selected[:limit]
    rows: list[dict] = []
    source_blocked: set[str] = set()
    seen_ids: dict[tuple[str, str], str] = {}
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
              "dry_run": dry_run, "manifest_sha256": manifest.sha256,
              "selected_count": len(selected), "offset": offset, "sources": list(sources),
              "rows": rows}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    def flush() -> None:
        pending = report_path.with_suffix(report_path.suffix + ".tmp")
        pending.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(report_path)

    flush()
    for index, item in enumerate(selected):
        if item.source in source_blocked:
            continue
        pacer.before_get()
        capture = capture_public_html(item.url, evidence_dir=evidence_dir / item.source,
                                      client=_SourceClient(item.source, client),
                                      timeout_seconds=20, max_attempts=1,
                                      expected_model=item.expected_model, evidence_store=evidence_store)
        row = {"source": item.source, "url": item.url, "expected_model": item.expected_model,
               "http_status": capture.http_status, "status": capture.status.value,
               "evidence_ref": capture.evidence_ref, "diagnostics": list(capture.diagnostics)}
        if capture.status in {CaptureStatus.BLOCKED, CaptureStatus.UNSAFE_URL}:
            source_blocked.add(item.source)
        if capture.status is CaptureStatus.SUCCESS and capture.capture and capture.evidence_body:
            try:
                product = _PARSERS[item.source](capture.evidence_body.decode("utf-8", "replace"),
                                                source_url=capture.final_url)
                if not product.site_internal_id or not product.supplier_model:
                    raise ValueError("stable site ID or primary model not proven")
                if normalise_model(product.supplier_model) != item.expected_model:
                    raise ValueError("sitemap model and product card disagree")
                identity_key = (item.source, product.site_internal_id)
                if identity_key in seen_ids and seen_ids[identity_key] != item.url:
                    raise ValueError("site internal ID reused by another URL in this run")
                seen_ids[identity_key] = item.url
            except ValueError as error:
                row["status"] = "REVIEW"
                row["diagnostics"].append(str(error))
            else:
                row.update({"external_id": product.site_internal_id, "model": product.supplier_model,
                            "price_state": product.price.state.value,
                            "price": None if product.price.current_price is None else str(product.price.current_price),
                            "currency": product.price.currency, "availability": product.availability or "unknown"})
                if not dry_run:
                    try:
                        result = repository.persist_commercial_observation(product, capture.capture)
                    except Exception as error:
                        row["status"] = "DB_ERROR"
                        row["diagnostics"].append(f"database_error:{type(error).__name__}")
                        source_blocked.add(item.source)
                    else:
                        row.update({"status": "PERSISTED", "observation_created": bool(result["observation_created"]),
                                    "exact_noop": bool(result["exact_noop"])})
                else:
                    row["status"] = "DRY_RUN"
        rows.append(row)
        flush()
        if on_progress:
            on_progress(row, index + 1, len(selected))
        if row["status"] == "DB_ERROR":
            # Database availability/commit state is global, not a local page
            # problem. Stop every source; never continue HTTP GETs blindly.
            report["aborted_reason"] = "database_error"
            break
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["blocked_sources"] = sorted(source_blocked)
    flush()
    return report
