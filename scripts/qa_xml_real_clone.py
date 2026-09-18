#!/usr/bin/env python3
"""Download and validate both XML exports from the authenticated preview clone."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.control_plane.store import PostgresControlPlaneStore, ProductFilters
from universal_supplier.postgres import PostgresConfig


BASE = os.environ.get("PREVIEW_BASE_URL", "http://127.0.0.1:58097")
USER = os.environ.get("PREVIEW_ADMIN_USER", "qa_stage6e")
PASSWORD = os.environ.get("PREVIEW_ADMIN_PASSWORD")
MODE = os.environ.get("QA_XML_MODE", "both")


def download(client: httpx.Client, mode: str, target: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with client.stream("GET", f"/exports/xml/download?mode={mode}") as response:
        response.raise_for_status()
        with target.open("wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    root = ET.parse(target).getroot()
    return {"path": str(target.relative_to(ROOT)), "bytes": size, "sha256": digest.hexdigest(),
            "root": root.tag, "products": len(root.findall(".//product")),
            "offers": len(root.findall(".//offer"))}


def main() -> None:
    if not PASSWORD:
        raise SystemExit("PREVIEW_ADMIN_PASSWORD is required")
    output = ROOT / "exports" / "qa"
    output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=180,
                      headers={"host": os.environ.get("PREVIEW_HTTP_HOST", "127.0.0.1")}) as client:
        login = client.post("/login", data={"username": USER, "password": PASSWORD})
        if login.status_code != 200 or "Выйти" not in login.text:
            raise SystemExit("preview login failed")
        short = (download(client, "short", output / "stage6e_short.xml") if MODE in {"both", "short"}
                 else {"path": "exports/qa/stage6e_short.xml"})
        if MODE == "short":
            short_root = ET.parse(ROOT / short["path"]).getroot()
            pp800f = short_root.find(".//offer[@id='88585']")
            short["pp800f_id_88585"] = pp800f is not None
            short["pp800f_vendor_code"] = pp800f.findtext("vendorCode") if pp800f is not None else None
            print(json.dumps({"classification": "LIVE-VERIFIED-ISOLATED-CLONE-PRE-RECONCILE",
                              "sterbrust_writes": 0, "esol_imports": 0, "short": short}, ensure_ascii=False))
            return
        full = download(client, "full", output / "stage6e_full.xml")
    if MODE == "full":
        short_root = ET.parse(ROOT / short["path"]).getroot()
        short.update({"root": short_root.tag, "offers": len(short_root.findall(".//offer")),
                      "products": len(short_root.findall(".//product")),
                      "bytes": (ROOT / short["path"]).stat().st_size,
                      "sha256": hashlib.sha256((ROOT / short["path"]).read_bytes()).hexdigest()})
    short_root = ET.parse(ROOT / short["path"]).getroot()
    pp800f = short_root.find(".//offer[@id='88585']")
    short["pp800f_id_88585"] = pp800f is not None
    short["pp800f_vendor_code"] = pp800f.findtext("vendorCode") if pp800f is not None else None
    full_root = ET.parse(ROOT / full["path"]).getroot()
    products = full_root.findall(".//product")
    full["with_stb_alias"] = len(full_root.findall(".//stb_1c_bitrix_id"))
    full["without_stb_alias"] = sum(p.find("identity/stb_1c_bitrix_id") is None for p in products)
    store = PostgresControlPlaneStore(PostgresConfig.from_env())
    rows = {str(row["source_product_id"]): row for row in store.iter_export_rows(ProductFilters(page_size=100))}
    leaks = []
    missing = []
    mismatched_aliases = []
    proposed_review = 0
    decision_alias_leaks = {key: 0 for key in ("REVIEW_EXISTING", "CONFLICT", "SAFE_NEW_PRODUCT_CANDIDATE")}
    sources: dict[str, int] = {}
    for product in products:
        row = rows[str(product.get("source_product_id"))]
        alias = product.findtext("identity/stb_1c_bitrix_id")
        identity_id = product.findtext("identity/sterbrust_product_id")
        confirmed = str(row.get("confirmed_sterbrust_id") or "") or None
        proposed = str(row.get("proposed_sterbrust_id") or "") or None
        if alias != identity_id:
            mismatched_aliases.append(product.get("source_product_id"))
        if alias and alias != confirmed:
            leaks.append({"source_product_id": product.get("source_product_id"), "alias": alias,
                          "confirmed": confirmed, "decision": row.get("decision")})
        if confirmed and alias != confirmed:
            missing.append(product.get("source_product_id"))
        if alias:
            source = str(row.get("confirmed_link_source") or "UNKNOWN")
            sources[source] = sources.get(source, 0) + 1
            if row.get("decision") in decision_alias_leaks and source != "MANUAL_CONFIRMED":
                decision_alias_leaks[row["decision"]] += 1
        if row.get("decision") == "REVIEW_EXISTING" and proposed and not confirmed:
            proposed_review += product.findtext("matching/proposed_sterbrust_id") == proposed
    full["alias_by_confirmation_source"] = sources
    full["unconfirmed_alias_leaks"] = len(leaks)
    full["missing_confirmed_aliases"] = len(missing)
    full["dual_alias_mismatches"] = len(mismatched_aliases)
    full["review_candidates_exposed_only_as_proposed"] = proposed_review
    full["review_stb_alias_leaks"] = decision_alias_leaks["REVIEW_EXISTING"]
    full["conflict_stb_alias_leaks"] = decision_alias_leaks["CONFLICT"]
    full["safe_new_stb_alias_leaks"] = decision_alias_leaks["SAFE_NEW_PRODUCT_CANDIDATE"]
    full["leak_examples"] = leaks[:10]
    if leaks or missing or mismatched_aliases or any(decision_alias_leaks.values()):
        raise SystemExit("full XML identity confirmation contract failed")
    result = {"classification": "LIVE-VERIFIED-ISOLATED-CLONE", "sterbrust_writes": 0,
              "esol_imports": 0, "short": short, "full": full}
    (ROOT / "reports" / "XML_REAL_CLONE_QA.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
