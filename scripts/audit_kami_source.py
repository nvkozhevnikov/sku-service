"""Bounded, sequential public GET audit. No ingestion or implicit crawl.

Raw captures stay in ignored reports; never persist cookies/response headers.
Robots must be successfully read before requesting any other resource.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from universal_supplier.kami_robots import KamiRobots

BASE = "https://www.stanki.ru/"
AGENT = "UniversalSupplier-KamiAudit/1.0"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", action="append", default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": AGENT})
    ledger = []
    interval = 5.0
    last = 0.0

    def fetch(url):
        nonlocal last
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.stanki.ru", "stanki.ru"}:
            raise ValueError("OUT_OF_SCOPE")
        time.sleep(max(0.0, interval - (time.monotonic() - last)))
        stamp = datetime.now(timezone.utc).isoformat()
        record = {"url": url, "observed_at": stamp}
        # Never follow an unvalidated redirect to another host or robots-denied URL.
        try:
            response = session.get(url, timeout=(15, 45), allow_redirects=False)
            last = time.monotonic()
            body = response.content
            digest = hashlib.sha256(body).hexdigest()
            filename = digest + ".capture"
            (args.output / filename).write_bytes(body)
            record.update(status=response.status_code, sha256=digest, bytes=len(body),
                          capture=filename, content_type=response.headers.get("Content-Type", ""))
            if response.is_redirect:
                record["redirect_target"] = urljoin(url, response.headers.get("Location", ""))
            ledger.append(record)
            (args.output / "HTTP_AUDIT.json").write_text(
                json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
            if response.status_code != 200:
                raise RuntimeError(f"STOP_HTTP_{response.status_code}; no retry/bypass")
            text = response.text
            lower = text.lower()
            if any(x in lower for x in ("cf-chl-", "g-recaptcha", "verify you are human", "access denied")):
                raise RuntimeError("STOP_POSSIBLE_ANTI_BOT; manual assessment required")
            return text
        except requests.RequestException as exc:
            record["error_type"] = type(exc).__name__
            ledger.append(record)
            (args.output / "HTTP_AUDIT.json").write_text(
                json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError("STOP_TRANSPORT_ERROR; no bypass") from None

    text = fetch(BASE + "robots.txt")
    robots = KamiRobots(text, AGENT.split("/")[0])
    interval = max(interval, robots.delay)
    for raw in args.url:
        url = urljoin(BASE, raw)
        if not robots.can_fetch(url):
            raise RuntimeError("STOP_ROBOTS_DENIED: " + url)
        fetch(url)
    print(json.dumps({"requests": len(ledger), "statuses": [r.get("status") for r in ledger],
                      "interval": interval}))


if __name__ == "__main__":
    main()
