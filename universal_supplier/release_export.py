"""Supplier-neutral v2 export of accepted decisions, never a matcher/importer.

Only supplied frozen decisions are serialized. No SQL, HTTP or candidate retrieval.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from datetime import datetime
import hashlib
import json
from xml.etree import ElementTree as ET

CONTRACT = "universal-supplier-neutral/2.0"
CLASSES = {"EXISTING_CONFIRMED", "READY_TO_CREATE_FULL", "REVIEW", "CONFLICT"}
MANDATORY_NEW = ("name", "brand", "model_execution", "category", "description", "characteristics")
NUMERIC = {"numeric", "numeric_public"}

def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def identity(row):
    return str(row["source"]), str(row["external_id"])

def trace_id(row):
    return "SRC-" + hashlib.sha256(compact(identity(row)).encode()).hexdigest()[:24].upper()

def price_value(row):
    if row.get("price_state") not in NUMERIC:
        return None
    try:
        price = Decimal(str(row.get("price")))
    except InvalidOperation:
        return None
    return str(price) if price.is_finite() and price > 0 else None

def field_value(payload, key):
    return payload["fields"][key].get("value")

def validate(rows, payloads):
    keys = [identity(r) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate supplier-scoped identity")
    by_key = {identity(p): p for p in payloads}
    if len(by_key) != len(payloads):
        raise ValueError("Duplicate full payload identity")
    groups = set()
    for row in rows:
        cls = row["classification"]
        if cls not in CLASSES:
            raise ValueError("Unaccepted export classification")
        if cls == "EXISTING_CONFIRMED":
            target = str(row.get("sterbrust_product_id", ""))
            if not target.isdecimal() or int(target) <= 0:
                raise ValueError("Existing requires a real positive canonical ID")
        elif cls == "READY_TO_CREATE_FULL":
            if row.get("sterbrust_product_id") or row.get("proposed_sterbrust_id"):
                raise ValueError("NEW must not contain a Sterbrust ID")
            payload = by_key.get(identity(row))
            if not payload or payload.get("sterbrust_product_id"):
                raise ValueError("Missing or invalid complete NEW payload")
            group = row.get("new_group_id")
            if not group or group != payload["new_group_id"] or group in groups:
                raise ValueError("Duplicate or unstable NEW group")
            groups.add(group)
            ready = payload["readiness"]
            if not ready.get("identity_ready") or not ready.get("full_ready") or ready.get("blockers"):
                raise ValueError("Incomplete readiness proof")
            for key in MANDATORY_NEW:
                f = payload["fields"][key]
                if f["state"] != "OBSERVED" or f["value"] in (None, "", []):
                    raise ValueError("Mandatory NEW content not observed: " + key)
            if any(f["state"] in {"UNKNOWN", "CONFLICT"} for f in payload["fields"].values()):
                raise ValueError("Unclosed NEW enrichment field")
            category = field_value(payload, "category")
            if not category.get("snapshot_active_verified") or not category.get("id"):
                raise ValueError("NEW section is not verified")
            if field_value(payload, "price_state") == "price_on_request" and field_value(payload, "price") is not None:
                raise ValueError("Request price must be NULL")
    if set(by_key) != {identity(r) for r in rows if r["classification"] == "READY_TO_CREATE_FULL"}:
        raise ValueError("Unexpected payload outside accepted FULL scope")

def summarize(rows):
    counts = Counter(r["classification"] for r in rows)
    return {"total_source_rows":len(rows), "Existing":counts["EXISTING_CONFIRMED"],
            "READY_TO_CREATE_FULL":counts["READY_TO_CREATE_FULL"],
            "Review":counts["REVIEW"], "Conflict":counts["CONFLICT"],
            "unique_sterbrust_product_ids":len({str(r["sterbrust_product_id"]) for r in rows
                                                if r["classification"] == "EXISTING_CONFIRMED"}),
            "namespaces":dict(sorted(Counter(r["source"] for r in rows).items()))}

def blocker(row):
    return compact({k:v for k,v in row.items() if any(t in k for t in
                    ("reason", "block", "conflict", "warning", "readiness", "quarantine", "resolution"))})

def selection_view(rows):
    """Proposed public-offer view, only comparable currency/role, never sale price.

    This does not alter persisted catalog_offer_selection. Unknown availability
    or missing dated provenance stays unselected. Lowest price wins; remaining
    ties use newest observation then supplier-scoped identity.
    """
    buckets = defaultdict(list)
    for row in rows:
        if row["classification"] != "EXISTING_CONFIRMED":
            continue
        if price_value(row) is None or not row.get("currency"):
            continue
        if row.get("availability") != "in_stock" or not row.get("observed_at") or not row.get("evidence_ref"):
            continue
        try:
            observed=datetime.fromisoformat(str(row["observed_at"]))
            if observed.tzinfo is None:continue
        except ValueError:continue
        buckets[(str(row["sterbrust_product_id"]), row["currency"], "supplier_public")].append(row)
    selected = set()
    for candidates in buckets.values():
        cheapest = min(Decimal(price_value(r)) for r in candidates)
        tied = [r for r in candidates if Decimal(price_value(r)) == cheapest]
        newest = max(datetime.fromisoformat(str(r["observed_at"])) for r in tied)
        selected.add(identity(min((r for r in tied if datetime.fromisoformat(str(r["observed_at"])) == newest), key=identity)))
    return selected

def _json(parent, name, value):
    node = ET.SubElement(parent, name, {"encoding":"json", "null":"true" if value is None else "false"})
    node.text = compact(value)
    return node

def xml_bytes(rows, payloads, input_hashes):
    validate(rows, payloads)
    by_key = {identity(p):p for p in payloads}
    root = ET.Element("UniversalSupplier", {"contract":CONTRACT, "import_authorized":"false",
                                              "evidence_level":"PACKAGED-EVIDENCE"})
    _json(root, "Reconciliation", summarize(rows))
    _json(root, "FrozenInputs", input_hashes)
    existing = ET.SubElement(root, "EXISTING")
    new = ET.SubElement(root, "NEW")
    unresolved = ET.SubElement(root, "UNRESOLVED", {"actionable":"false"})
    selected = selection_view(rows)
    for row in sorted(rows, key=identity):
        cls = row["classification"]
        target = existing if cls == "EXISTING_CONFIRMED" else new if cls == "READY_TO_CREATE_FULL" else unresolved
        attrs = {"source_ref":trace_id(row), "namespace":row["source"], "external_id":str(row["external_id"]),
                 "classification":cls, "actionable":"false"}
        if cls == "EXISTING_CONFIRMED":
            attrs["sterbrust_product_id"] = str(row["sterbrust_product_id"])
        elif cls == "READY_TO_CREATE_FULL":
            attrs["new_group_id"] = row["new_group_id"]
            attrs["new_candidate_id"] = row["new_candidate_id"]
        node = ET.SubElement(target, "Product", attrs)
        _json(node, "SourceIdentity", {k:row.get(k) for k in ("name","brand","model","execution","product_kind","source_url")})
        _json(node, "Provenance", {k:row.get(k) for k in ("observed_at","evidence_ref","source_product_id","offer_id",
                                                         "priority_evidence_ref","prior_confirmed_decision_retained")})
        if cls == "READY_TO_CREATE_FULL":
            p = by_key[identity(row)]
            card = ET.SubElement(node,"FullCard", {"readiness":"READY_TO_CREATE_FULL"})
            # Named field envelopes keep statuses, original units/roles and provenance.
            for key, field in p["fields"].items():
                _json(card, key, field)
            _json(card,"description_html",p.get("description_html"))
            _json(card,"optional_configuration",p.get("optional_characteristics",[]))
            _json(card,"readiness_proof",p["readiness"])
        elif cls == "EXISTING_CONFIRMED":
            _json(node,"CommercialObservation", {"numeric_price":price_value(row),"price_state":row.get("price_state"),
                  "currency":row.get("currency"),"price_role":"supplier_public_not_sterbrust_sale",
                  "availability":row.get("availability"),"quantity":None,"quantity_state":"NOT_FOUND",
                  "observed_at":row.get("observed_at"),"evidence_ref":row.get("evidence_ref"),
                  "selected_comparable_public_view":identity(row) in selected, "live_stock_verified":False})
            _json(node,"AcceptedIdentityEvidence", {k:v for k,v in row.items() if any(t in k for t in
                  ("resolution","proof","evidence","confirmed","target_model"))})
        else:
            _json(node,"BlockingEvidence",json.loads(blocker(row)))
            _json(node,"CandidateEvidence",{k:v for k,v in row.items() if any(t in k for t in ("candidate","proposed","target"))})
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8",xml_declaration=True)

def tables(rows, payloads):
    selected = selection_view(rows)
    base_keys = ["source_ref","supplier","external_id","name","brand","model","execution","classification",
                 "sterbrust_product_id","new_group_id","source_url","observed_at","evidence_ref"]
    data = {name:[] for name in ("Existing","Offers","NEW full cards","Review","Conflict","Source trace")}
    for row in sorted(rows,key=identity):
        base = {k:row.get(k) for k in base_keys}
        base.update(source_ref=trace_id(row), supplier=row["source"])
        # Only accepted Existing may expose canonical target as assignment.
        if row["classification"] != "EXISTING_CONFIRMED":
            base["sterbrust_product_id"] = None
        data["Source trace"].append(dict(base,provenance=compact({k:row.get(k) for k in ("evidence_ref","priority_evidence_ref","source_product_id","offer_id")})))
        if row["classification"] == "EXISTING_CONFIRMED":
            data["Existing"].append(dict(base,identity_evidence=blocker(row)))
            data["Offers"].append(dict(base,numeric_price=float(price_value(row)) if price_value(row) is not None else None,
                price_state=row.get("price_state"),currency=row.get("currency"),availability=row.get("availability"),
                quantity=None,price_role="supplier_public_not_sterbrust_sale",selected=identity(row) in selected,
                selection_scope="proposed_comparable_public_view_not_persisted",live_stock_verified=False))
        elif row["classification"] in {"REVIEW","CONFLICT"}:
            name = "Review" if row["classification"] == "REVIEW" else "Conflict"
            data[name].append(dict(base,blocker=blocker(row),candidate_evidence=compact({k:v for k,v in row.items() if "candidate" in k or "proposed" in k}),actionable=False))
    for p in payloads:
        base={"source_ref":trace_id(p),"supplier":p["source"],"external_id":p["external_id"],
              "new_group_id":p["new_group_id"],"new_candidate_id":p["new_candidate_id"],"sterbrust_product_id":None}
        base.update({k:field_value(p,k) for k in ("name","brand","model_execution","description","price","price_state","availability","source_url","capture_time","capture_sha256")})
        base["SECTION_ID"] = field_value(p,"category")["id"]
        base["section_name"] = field_value(p,"category")["name"]
        for key in ("manufacturer","supplier_article","manufacturer_article","characteristics","images","documents"):
            base[key] = compact(p["fields"][key])
        base["optional_configuration"] = compact(p["optional_characteristics"])
        base["full_payload"] = compact(p)
        data["NEW full cards"].append(base)
    return data
