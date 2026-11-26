"""Generate the supplier XML and catalog-link review bundle from captures."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universal_supplier.adapters.ivstankomaster import CODE, parse_detail
from universal_supplier.commercial_persistence import normalize_availability
from universal_supplier.http_capture import sanitise_html
from universal_supplier.xml_export import supplier_xml_bytes


def model_key(value):
    # Collapse only typography. Keep the entire model, including PLUS.
    return re.sub(r"\s+", "", (value or "").upper())


def links_for(product, registry):
    key = model_key(product.supplier_model)
    if not key or not key.startswith(("СЗТП", "СЗПН", "СЗМ")):
        return "REVIEW_NO_UNIQUE_IDENTITY", [], []
    candidates = [r for r in registry if model_key(r["model_raw"]) == key]
    if not candidates:
        # A spare part with a compatible machine in its title is not a model
        # match. A same-category title without a model property is review only.
        candidates = [r for r in registry if not r["model_raw"]
                      and re.search(r"\bстанок\b", r["name"], re.I)
                      and not re.search(r"оснастка|модернизация|комплект", r["category_name"], re.I)
                      and model_key(r["name"]).endswith(key)]
    ids = [r["sterbrust_product_id"] for r in candidates]
    if len(candidates) != 1:
        return "REVIEW_NO_UNIQUE_IDENTITY", ids, []
    target = candidates[0]
    props = {p["property_code"]:p["value_flat"] for p in target["properties"]}
    source = dict(product.technical_properties)
    power = source.get("Мощность двигателя, кВт", "").replace(",", ".")
    voltage = source.get("Напряжение питающей сети, В", "").split(" ", 1)[0]
    if target["active"] != "Y":
        return "REVIEW_INACTIVE_TARGET", ids, []
    # For this catalog, 600Ал is offered with the coolant unit as an extra.
    # The registry lists the linked model as having coolant, a package conflict.
    if key == "СЗТП600АЛ" and (props.get("OHLAZHDENIE") or "").casefold() == "есть":
        return "REVIEW_COOLANT_CONFIGURATION", ids, []
    if not (re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", power)
            and re.fullmatch(r"[0-9]+", props.get("MOSH") or "")
            and Decimal(power)*1000 == Decimal(props["MOSH"])
            and voltage == props.get("NAPR") and target["model_raw"]):
        return "REVIEW_TECHNICAL_PROPERTIES", ids, []
    expected_category = "дисковых пил" if key.startswith("СЗТП") else "плоских ножей"
    if expected_category not in target["category_path"].casefold():
        return "REVIEW_CATEGORY", ids, []
    evidence = [f"exact model {target['model_raw']}", f"power {props['MOSH']} W",
                f"voltage {props['NAPR']} V", target["category_path"]]
    # Finding the right catalog card does not provide a durable approved
    # mapping. The normal matcher can still block an ambiguous configuration.
    return "REVIEW_CATALOG_CANDIDATE", ids, evidence


def build(captures, registry_file, output):
    records = [json.loads(line) for line in (captures/"responses.jsonl").read_text(encoding="utf-8").splitlines()]
    coverage = json.loads((captures/"coverage.json").read_text(encoding="utf-8"))
    selected = set(coverage["products"])
    registry = []
    with registry_file.open(encoding="utf-8-sig") as source:
        for line in source:
            if "ивстанкомастер" not in line.casefold():
                continue
            row = json.loads(line)
            if "ивстанкомастер" in (row["brand_raw"].casefold(), row["manufacturer_raw"].casefold()):
                registry.append(row)
    if not registry or len({r["sterbrust_product_id"] for r in registry}) != len(registry):
        raise ValueError("Invalid supplier subset of the Sterbrust registry")
    items, details, links, seen_ids, observed_urls = [], {"properties": [], "images": []}, [], set(), set()
    for record in records:
        if record["url"] not in selected:
            continue
        path = (captures/record["file"]).resolve()
        if not path.is_relative_to(captures.resolve()):
            raise ValueError("Capture file escapes evidence directory")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != record["sha256"] or record["status"] != 200:
            raise ValueError("Invalid captured detail")
        item = parse_detail(sanitise_html(body,record["content_type"]).decode("utf-8"),source_url=record["url"])
        if item.site_internal_id in seen_ids or item.source_url in observed_urls:
            raise ValueError("Duplicate product identity needs reconciliation")
        seen_ids.add(item.site_internal_id)
        observed_urls.add(item.source_url)
        status, ids, evidence = links_for(item, registry)
        target = next((r for r in registry if status=="REVIEW_CATALOG_CANDIDATE" and ids==[r["sterbrust_product_id"]]),None)
        pid = int(item.site_internal_id)
        row = {"source_product_id":pid,"supplier_code":CODE,"external_id":item.site_internal_id,
               "canonical_url":item.source_url,"source_url":item.source_url,"name":item.name,
               "own_model":item.supplier_model,"brand_raw":item.source_brand,"description":item.description_text,
               "description_text":item.description_text,"price":item.price.current_price,"currency":item.price.currency,
               "quantity":item.source_quantity,"availability_normalized":normalize_availability(item.availability),
               "category":item.source_category,"external_id_is_stable":True,
               "price_raw":item.price.raw_text,"price_source":item.price.source_path,
               "last_success_at":record["observed_at"],"last_http_status":200}
        items.append(row)
        details["properties"].extend({"source_product_id":pid,"raw_name":key,"raw_value":value}
                                     for key,value in item.technical_properties)
        details["images"].extend({"source_product_id":pid,"source_url":url,"is_primary":i==0}
                                 for i,url in enumerate(item.source_images))
        links.append({"supplier_product_id":pid,"supplier_url":item.source_url,"name":item.name,
                      "model":item.supplier_model,"price_with_vat":str(item.price.current_price) if item.price.current_price is not None else None,
                      "vat_basis":item.enrichment_evidence["vat_basis"],"vat_source_text":item.price.raw_text,
                      "price_without_vat":item.enrichment_evidence["net_prices"],"quantity":item.source_quantity,
                      "availability":row["availability_normalized"],"observed_at":record["observed_at"],
                      "capture_sha256":record["sha256"],"status":status,"candidate_ids":ids,"identity_evidence":evidence,
                      "proposed_sterbrust_id":target["sterbrust_product_id"] if target else None,
                      "sterbrust_url":target["product_url"] if target else None,
                      "registry_as_of":target["captured_at"] if target else None})
    if observed_urls != selected:
        raise ValueError("Missing captures from the catalog")
    linked = [r for r in links if r["proposed_sterbrust_id"] and r["price_with_vat"] is not None]
    if len({r["proposed_sterbrust_id"] for r in linked}) != len(linked):
        raise ValueError("Duplicate target product in linked export")
    output.mkdir(exist_ok=False, parents=True)
    full=ET.fromstring(supplier_xml_bytes(items,lambda pids:{k:[x for x in group if x["source_product_id"] in pids]
                                                           for k,group in details.items()}))
    byid={str(r["supplier_product_id"]):r for r in links}
    for node in full.findall("./supplier/products/product"):
        proof=byid[node.attrib["source_product_id"]]
        node.set("price_vat", "included" if proof["price_with_vat"] else "unconfirmed")
        node.set("link_status", proof["status"])
        if proof["proposed_sterbrust_id"]:node.set("proposed_sterbrust_id", proof["proposed_sterbrust_id"])
    ET.indent(full)
    ET.ElementTree(full).write(output/"ivstankomaster_catalog.xml",encoding="utf-8",xml_declaration=True)
    root=ET.Element("supplier_linked_prices", supplier=CODE, currency="RUB", tax="VAT_INCLUDED",
                    import_allowed="false", purpose="catalog-link-review")
    fields=("supplier_product_id","proposed_sterbrust_id","model","price_with_vat","quantity","availability",
            "supplier_url","sterbrust_url","observed_at")
    for row in linked:
        node=ET.SubElement(root,"product", status="REVIEW", identity_confirmed="false")
        for key in fields:
            if row[key] is not None:ET.SubElement(node,key).text=str(row[key])
    ET.indent(root)
    ET.ElementTree(root).write(output/"ivstankomaster_linked_vat.xml",encoding="utf-8",xml_declaration=True)
    (output/"matching_and_prices.json").write_text(json.dumps(links,ensure_ascii=False,indent=2),encoding="utf-8")
    summary={"cards":len(items),"vat_prices":sum(r["price_with_vat"] is not None for r in links),
             "link_candidates":len(linked),"confirmed_links":0,"status_counts":dict(Counter(r["status"] for r in links)),
             "registry_brand_records":len(registry),"import_allowed":False}
    for path in output.glob("*.xml"):ET.parse(path)
    (output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    for name in ("captures","registry","output"):cli.add_argument(name,type=Path)
    args=cli.parse_args()
    print(json.dumps(build(args.captures,args.registry,args.output),ensure_ascii=False))
