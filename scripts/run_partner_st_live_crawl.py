#!/usr/bin/env python3
"""Live Partner-ST HTTP crawl. Does not write Sterbrust or substitute for PostgreSQL."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.adapters.partner_st import PartnerStAdapter
from universal_supplier.http import HttpTransport, RetryPolicy
from universal_supplier.state import discovery_health


REPORTS = ROOT / "reports"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", choices=("RUN1", "RUN2"), required=True)
    parser.add_argument("--baseline", type=int, default=1222)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--interval", type=float, default=0.20)
    args = parser.parse_args()
    adapter = PartnerStAdapter()
    transport = HttpTransport(min_interval_seconds=args.interval, retry_policy=RetryPolicy(max_attempts=3))
    started = datetime.now(timezone.utc)
    discovery = adapter.discover(transport)
    health = discovery_health(len(discovery.product_urls), args.baseline)
    rows: list[dict] = []
    cards: list[dict] = []

    def process(position_url: tuple[int, str]) -> tuple[int, dict, dict | None]:
        position, url = position_url
        try:
            fetched = transport.get(url)
            if fetched.status_code != 200:
                return position, {
                    "position": position, "requested_url": url, "final_url": fetched.final_url,
                    "http_status": fetched.status_code, "redirect_count": len(fetched.redirects),
                    "attempt_count": fetched.attempts, "external_id": "", "sku": "", "name": "",
                    "result": "ERROR", "error": f"HTTP_{fetched.status_code}",
                }, None
            card = adapter.parse_product(fetched)
            return position, {
                "position": position, "requested_url": url, "final_url": fetched.final_url,
                "http_status": fetched.status_code, "redirect_count": len(fetched.redirects),
                "attempt_count": fetched.attempts, "external_id": card.external_id, "sku": card.sku,
                "name": card.name, "result": "FETCHED", "error": "",
            }, card.as_jsonable()
        except Exception as error:
            return position, {
                "position": position, "requested_url": url, "final_url": "", "http_status": "",
                "redirect_count": 0, "attempt_count": 3, "external_id": "", "sku": "", "name": "",
                "result": "ERROR", "error": f"{type(error).__name__}: {str(error)[:300]}",
            }, None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(process, item) for item in enumerate(discovery.product_urls, 1)]
        for completed, future in enumerate(as_completed(futures), 1):
            _, row, card = future.result()
            rows.append(row)
            if card is not None:
                cards.append(card)
            if completed % 100 == 0:
                print(f"{args.run}: {completed}/{len(futures)}", flush=True)
    rows.sort(key=lambda row: int(row["position"]))
    order = {url: index for index, url in enumerate(discovery.product_urls)}
    cards.sort(key=lambda card: order.get(card["requested_url"], 10**9))
    csv_path = REPORTS / f"PARTNER_ST_FULL_CRAWL_{args.run}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "position", "requested_url", "final_url", "http_status", "redirect_count", "attempt_count",
            "external_id", "sku", "name", "result", "error",
        ])
        writer.writeheader()
        writer.writerows(rows)
    parsed_path = REPORTS / f"PARTNER_ST_PARSED_{args.run}.jsonl"
    with parsed_path.open("w", encoding="utf-8") as handle:
        for card in cards:
            handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True) + "\n")
    product_ids = [card["external_id"] for card in cards]
    metadata = {
        "run": args.run, "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "transport": "direct_http", "playwright_used": False, "proxy_used": False,
        "sitemap_index": discovery.sitemap_index_url, "sitemaps": list(discovery.sitemap_urls),
        "product_sitemaps": list(discovery.product_sitemap_urls), "discovered_count": len(discovery.product_urls),
        "baseline_discovered_count": args.baseline, "discovery_ratio": health.discovery_ratio,
        "anomalous_discovery": health.anomalous_discovery, "deactivation_allowed": health.deactivation_allowed,
        "fetched_count": len(cards), "http_200": sum(row["http_status"] == 200 for row in rows),
        "redirects": sum(int(row["redirect_count"]) for row in rows),
        "errors": sum(row["result"] == "ERROR" for row in rows),
        "unique_product_ids": len(set(product_ids)), "duplicate_product_ids": len(product_ids) - len(set(product_ids)),
        "sterbrust_write_methods_used": 0,
    }
    (REPORTS / f"partner_st_{args.run.lower()}_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    if metadata["errors"] or metadata["duplicate_product_ids"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
