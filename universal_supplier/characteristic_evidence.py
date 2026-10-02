"""Offline, scoped evidence learned only from confirmed source/canonical pairs.

This module emits diagnostic proposals. It never changes matcher decisions,
canonical IDs, section mappings, database rows or import readiness itself.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Mapping

from sterbrust_matching.matching import Product, classify_identity_quality
from sterbrust_matching.normalization import extract_model, normalize_model, normalize_text
from sterbrust_matching.product_identity import OWN_MODEL, classify_model_role, classify_product_kind


RULE_VERSION = "scoped-characteristic-evidence-1"
IDENTITY_CRITICAL, SUPPORTING, NON_IDENTITY = "IDENTITY_CRITICAL", "SUPPORTING", "NON_IDENTITY"
READINESS_STATES = ("NEW_CANDIDATE", "NEW_GROUPED", "CANONICAL_ABSENCE_VERIFIED",
                    "SECTION_VERIFIED", "IDENTITY_EVIDENCE_COMPLETE", "READY_TO_CREATE")


@dataclass(frozen=True)
class PropertySpec:
    kind: str
    unit: str
    importance: str


SPECS = {
    "blade_dimensions": PropertySpec("dimensions", "mm", IDENTITY_CRITICAL),
    **{f"blade_{part}": PropertySpec("number", "mm", IDENTITY_CRITICAL)
       for part in ("length", "width", "thickness")},
    "main_motor_power": PropertySpec("number_or_range", "W", IDENTITY_CRITICAL),
    "blade_speed": PropertySpec("number_or_range", "m/min", IDENTITY_CRITICAL),
    "construction_type": PropertySpec("text", "", IDENTITY_CRITICAL),
    "automation_type": PropertySpec("text", "", IDENTITY_CRITICAL),
    "cutting_diameter_unspecified": PropertySpec("number", "mm", SUPPORTING),
    "machine_dimensions": PropertySpec("dimensions", "mm", SUPPORTING),
    **{f"machine_{part}": PropertySpec("number", "mm", SUPPORTING)
       for part in ("length", "width", "height")},
    "weight": PropertySpec("number", "kg", SUPPORTING),
    "table_height": PropertySpec("number", "mm", SUPPORTING),
    "coolant_pump_power": PropertySpec("number", "W", SUPPORTING),
    "hydraulic_pump_power": PropertySpec("number", "W", SUPPORTING),
    "manufacturer": PropertySpec("text", "", NON_IDENTITY),
    "country": PropertySpec("text", "", NON_IDENTITY),
    "warranty": PropertySpec("number", "month", NON_IDENTITY),
    "voltage": PropertySpec("number", "V", IDENTITY_CRITICAL),
    "distance_between_centers": PropertySpec("number", "mm", IDENTITY_CRITICAL),
    "spindle_bore": PropertySpec("number", "mm", IDENTITY_CRITICAL),
    "jaw_width": PropertySpec("number", "mm", IDENTITY_CRITICAL),
    "clamping_range": PropertySpec("number_or_range", "mm", IDENTITY_CRITICAL),
    "wheel_dimensions": PropertySpec("dimensions", "mm", IDENTITY_CRITICAL),
}
for angle in ("0", "minus45", "plus45", "plus60", "plus30", "minus30", "minus60", "plus15"):
    for shape in ("round", "square", "rectangle"):
        SPECS[f"cut_{shape}_{angle}"] = PropertySpec("dimensions" if shape == "rectangle" else "number", "mm", IDENTITY_CRITICAL)

REQUIRED_PROPERTIES = {
    "bandsaw": ("blade_length", "blade_width", "blade_thickness", "main_motor_power",
                "cut_round_0", "construction_type", "automation_type"),
    "lathe": ("distance_between_centers", "spindle_bore", "main_motor_power", "voltage"),
    "vise": ("jaw_width", "clamping_range"),
    "grinder": ("wheel_dimensions", "main_motor_power", "voltage"),
}


def equipment_scope(name: str, category: str = "") -> str:
    kind = classify_product_kind(name, category).product_kind
    if kind not in {"machine", "unknown", "lathe", "grinder"}:
        return {"jaw": "accessory_jaw"}.get(kind, kind)
    title, category_text = normalize_text(name), normalize_text(category)
    band = r"ленточ|band.?saw"
    circular = r"дисков|circular"
    if ((re.search(band, title) and re.search(circular, category_text))
            or (re.search(circular, title) and re.search(band, category_text))):
        return "equipment_scope_conflict"
    text = title + " " + category_text
    if re.search(band, text):
        return "bandsaw"
    if re.search(circular, text):
        return "circular_saw"
    # These are explicit equipment labels, not brand/model inference. Existing
    # category-scoped lathe/grinder property policies must actually be reachable.
    typed = [scope for scope, pattern in (
        ('lathe', r'токарн|\blathe\b|\bturning\b'),
        ('grinder', r'шлифовальн|заточн|\bgrinding\b|\bgrinder\b'),
    ) if re.search(pattern, text)]
    if len(typed) > 1: return 'equipment_scope_conflict'
    if typed: return typed[0]
    return kind


def unit_from_label(label: str) -> str:
    match = re.search(r"(?<![a-zа-я])(?:мм/мин|м/мин|квт|вт|mm/min|m/min|kw|mm|kg|кг|мм|см|мес|rpm|об/мин|v|в|m)(?=\s*[),]?\s*$)", normalize_text(label))
    return match.group(0) if match else ""


def property_key(label: str, scope: str) -> str | None:
    """Explicit semantic label rules; observed equal numbers never learn a synonym."""
    text = normalize_text(label)
    common = ((r"^(?:вес|масса)\b", "weight"), (r"^страна\b|^производство$", "country"),
              (r"^производитель$", "manufacturer"), (r"^гарантия[, ]", "warranty"),
              (r"^напряжение\b|^voltage\b", "voltage"))
    for pattern, key in common:
        if re.search(pattern, text):
            return key
    if scope == "bandsaw":
        patterns = (
            (r"^размеры (?:ленточного )?полотна", "blade_dimensions"),
            (r"^длина пильной ленты", "blade_length"), (r"^ширина полотна", "blade_width"),
            (r"^толщина полотна", "blade_thickness"),
            (r"^скорость (?:ленточного полотна|движения ленты|резки)", "blade_speed"),
            (r"^главный привод|^мощность двигателя\s*(?:,|\(|$)|^мощность\s*(?:,|\(|$)", "main_motor_power"),
            (r"^мощность (?:насоса)?\s*сож|^насос подачи сож", "coolant_pump_power"),
            (r"^мощность гидронасоса", "hydraulic_pump_power"),
            (r"^тип конструкции$|^конструкция$|^тип станка$", "construction_type"),
            (r"^тип автоматизации$", "automation_type"),
            (r"^габариты[, ]", "machine_dimensions"),
            (r"^высота (?:рабочего стола|рабочей поверхности)", "table_height"),
            (r"^диаметр сечения|^макс\. ø резания", "cutting_diameter_unspecified"),
        )
        for pattern, key in patterns:
            if re.search(pattern, text):
                return key
        for part, word in (("length", "длина"), ("width", "ширина"), ("height", "высота")):
            if re.search(rf"^(?:габариты: )?{word}\s*[,(]", text):
                return "machine_" + part
        cutting = re.search(r"^распил\s*([+-]?\d+)\s*град\.?.*\((круг|квадрат|прямоугольник)\)", text)
        if cutting:
            angle = cutting.group(1).replace("-", "minus").replace("+", "plus")
            key = "cut_" + {"круг": "round", "квадрат": "square", "прямоугольник": "rectangle"}[cutting.group(2)] + "_" + angle
            return key if key in SPECS else None
        if re.search(r"^max диаметр заготовки 90°", text):
            return "cut_round_0"  # perpendicular cut: source 0 swing = canonical 90 cut
        # Unspecified left/right 45 degrees is not a signed-angle synonym.
    scoped = {
        "lathe": ((r"расстояние.*между центр|distance between centers", "distance_between_centers"),
                  (r"отверсти[ея].*(?<!противо)(?<!контр)шпиндел|(?<!sub )(?<!counter )spindle bore", "spindle_bore")),
        "vise": ((r"ширина.*губ|jaw width", "jaw_width"), (r"раствор|раскрытие|clamping range", "clamping_range")),
        "grinder": ((r"размер.*(?:круга|диска)|wheel dimensions", "wheel_dimensions"),),
    }
    if scope in scoped:
        for pattern, key in scoped[scope]:
            if re.search(pattern, text):
                return key
        if re.search(r"^мощность(?: двигателя)?\s*(?:,|\(|$)|^motor power", text):
            return "main_motor_power"
    return None


def normalize_characteristic(value: Any, key: str, unit: str = "") -> dict[str, Any]:
    spec = SPECS[key]
    raw = str(value if value is not None else "")
    text = normalize_text(raw).replace(",", ".").replace("×", "x").replace("х", "x")
    result = {"raw_value": raw, "raw_unit": unit, "canonical_unit": spec.unit,
              "values": [], "parsed": False, "form": spec.kind}
    if not text:
        return {**result, "reason": "missing_value"}
    if spec.kind == "text":
        return {**result, "parsed": True, "values": [text], "reason": "normalized_text"}
    factors = {"mm": ("mm", "1"), "мм": ("mm", "1"), "cm": ("mm", "10"), "см": ("mm", "10"),
               "m": ("mm", "1000"), "м": ("mm", "1000"), "w": ("W", "1"), "вт": ("W", "1"),
               "kw": ("W", "1000"), "квт": ("W", "1000"), "kg": ("kg", "1"), "кг": ("kg", "1"),
               "g": ("kg", ".001"), "г": ("kg", ".001"), "v": ("V", "1"), "в": ("V", "1"),
               "м/мин": ("m/min", "1"), "m/min": ("m/min", "1"),
               "мм/мин": ("m/min", ".001"), "mm/min": ("m/min", ".001"), "мес": ("month", "1")}
    suffix = re.search(r"\s*([a-zа-я/]+)\s*$", text)
    raw_unit = suffix.group(1) if suffix else normalize_text(unit)
    numeric = text[:suffix.start()].strip() if suffix else text
    if raw_unit not in factors or factors[raw_unit][0] != spec.unit:
        return {**result, "reason": "missing_or_incompatible_unit"}
    factor = Decimal(factors[raw_unit][1])
    if spec.kind == "dimensions":
        parts, form = re.split(r"\s*x\s*", numeric), "dimensions"
        if len(parts) not in (2, 3):
            return {**result, "reason": "dimension_arity_unknown"}
    elif spec.kind == "number_or_range":
        parts, form = re.split(r"\s*[-–]\s*", numeric), "range" if re.search(r"[-–]", numeric) else "number"
        if len(parts) not in (1, 2):
            return {**result, "reason": "ambiguous_range"}
    else:
        parts, form = [numeric], "number"
    if not all(re.fullmatch(r"\d+(?:\.\d+)?", part.strip()) for part in parts):
        return {**result, "reason": "unparsed_numeric_value"}
    try:
        values = [format((Decimal(part.strip()) * factor).normalize(), "f") for part in parts]
    except InvalidOperation:
        return {**result, "reason": "invalid_decimal"}
    return {**result, "values": values, "parsed": True, "form": form, "reason": "typed_unit_conversion"}


def features(properties: Iterable[Mapping[str, Any]], scope: str) -> list[dict[str, Any]]:
    result = []
    for prop in properties:
        label = str(prop.get("name") or "")
        key = property_key(label, scope)
        if key is None:
            continue
        value = normalize_characteristic(prop.get("value"), key, str(prop.get("unit") or unit_from_label(label)))
        item = {"property_name": label, "key": key, "importance": SPECS[key].importance, **value}
        result.append(item)
        if key in {"blade_dimensions", "machine_dimensions"} and value["parsed"] and len(value["values"]) == 3:
            parts = ("length", "width", "thickness") if key == "blade_dimensions" else ("length", "width", "height")
            prefix = "blade_" if key == "blade_dimensions" else "machine_"
            for index, part in enumerate(parts):
                result.append({**item, "key": prefix + part, "form": "number", "values": [value["values"][index]],
                               "importance": SPECS[prefix + part].importance, "derivation": f"dimension_component_{index}"})
    return result


def _same(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return bool(left["parsed"] and right["parsed"] and left["values"] == right["values"]
                and left["canonical_unit"] == right["canonical_unit"] and left["form"] == right["form"])


def build_evidence(pairs: Iterable[Mapping[str, Any]], *, minimum_examples: int = 3) -> dict[str, Any]:
    mappings, categories, seen = defaultdict(list), defaultdict(dict), set()
    inventory = Counter()
    for pair in pairs:
        if pair.get("classification") != "EXISTING_CONFIRMED" or not pair.get("full_model_confirmed"):
            continue
        identity = (str(pair["source"]), str(pair["external_id"]))
        if identity in seen:
            raise ValueError("duplicate confirmed source identity")
        seen.add(identity)
        scope = str(pair["equipment_type"])
        category = str(pair.get("source_category") or "")
        target_id = str(pair["sterbrust_product_id"])
        context = (identity[0], category, scope)
        categories[context][target_id] = pair
        source = features(pair.get("source_properties", []), scope)
        target = features(pair.get("sterbrust_properties", []), scope)
        for side in ("source", "sterbrust"):
            for prop in pair.get(side + "_properties", []):
                label = str(prop.get("name") or "")
                key = property_key(label, scope)
                inventory[(side, scope, label, key or "UNMAPPED")] += 1
        for left in source:
            for right in target:
                if left["key"] != right["key"]:
                    continue
                key = (*context, left["key"], left["property_name"], right["property_name"],
                       left["raw_unit"], right["raw_unit"], left.get("derivation", ""))
                mappings[key].append({"source": identity[0], "external_id": identity[1], "sterbrust_product_id": target_id,
                    "source_url": pair.get("source_url"), "evidence_ref": pair.get("evidence_ref"),
                    "source_value": left, "sterbrust_value": right, "agreement": _same(left, right)})
    synonyms = []
    for key, examples in sorted(mappings.items()):
        source, category, scope, canonical, left_name, right_name, left_unit, right_unit, derivation = key
        total = len({(e["source"], e["external_id"]) for e in examples})
        unique_targets = len({e["sterbrust_product_id"] for e in examples})
        agreements = sum(e["agreement"] for e in examples)
        verdict = "SAFE" if unique_targets >= minimum_examples and agreements == len(examples) else "REVIEW" if agreements != len(examples) else "INSUFFICIENT"
        synonyms.append({"source": source, "source_category": category, "category_scope": scope,
            "supplier_property_name": left_name, "sterbrust_property_name": right_name,
            "canonical_property_key": canonical, "importance": SPECS[canonical].importance,
            "supplier_unit": left_unit, "sterbrust_unit": right_unit, "unit": SPECS[canonical].unit,
            "derivation": derivation, "evidence_count": total, "unique_canonical_examples": unique_targets,
            "agreement_ratio": agreements / len(examples), "verdict": verdict,
            "examples": examples[:3], "contradiction_examples": [e for e in examples if not e["agreement"]][:3]})
    sections = []
    for (source, category, scope), unique in sorted(categories.items()):
        votes = Counter()
        ambiguous = 0
        for pair in unique.values():
            ids = {str(s["id"]) for s in pair.get("sections", [])}
            ambiguous += len(ids) != 1
            votes.update(ids)
        winner = sorted(votes, key=lambda sid: (-votes[sid], int(sid)))[0] if votes else None
        active = all(section.get("active") == "Y" and section.get("path")
                     for pair in unique.values() for section in pair.get("sections", []))
        ratio = votes[winner] / len(unique) if winner else 0
        verdict = ("SAFE" if category and len(unique) >= minimum_examples and ratio == 1 and len(votes) == 1
                   and not ambiguous and active else "REVIEW" if len(votes) > 1 or ambiguous or not active
                   else "INSUFFICIENT")
        sections.append({"source": source, "source_category": category, "category_scope": scope,
            "section_id": int(winner) if winner else None, "confirmed_examples_count": len(unique),
            "agreement_ratio": ratio, "conflicting_sections": [int(sid) for sid in sorted(votes) if sid != winner],
            "section_counts": dict(votes), "ambiguous_examples_count": ambiguous, "verdict": verdict,
            "examples": [{"external_id": p["external_id"], "sterbrust_product_id": p["sterbrust_product_id"],
                          "sections": p.get("sections", [])} for p in list(unique.values())[:3]]})
    return {"rule_version": RULE_VERSION, "minimum_examples": minimum_examples, "confirmed_source_records": len(seen),
        "synonym_mappings": synonyms, "section_mappings": sections,
        "property_inventory": [{"side": s, "category_scope": scope, "name": name, "canonical_property_key": key,
                                "importance": SPECS[key].importance if key in SPECS else "UNCLASSIFIED", "observations": n}
                               for (s, scope, name, key), n in sorted(inventory.items())]}


def _conflicts(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip() not in ("", "{}", "[]", "null")
    return bool(value)


def evaluate_candidate(candidate: Mapping[str, Any], evidence: Mapping[str, Any], *,
                       registry_index: Mapping[str, Any], reference: Mapping[str, Any] | None = None,
                       new_group: Mapping[str, Any] | None = None,
                       enable_ready_proposal: bool = False) -> dict[str, Any]:
    """Read-only evidence/readiness proposal; never overwrites input classification."""
    scope = equipment_scope(str(candidate.get("name") or ""), str(candidate.get("source_category") or ""))
    context = (candidate.get("source"), candidate.get("source_category"), scope)
    mapped = [m for m in evidence.get("synonym_mappings", []) if m["verdict"] == "SAFE"
              and (m["source"], m["source_category"], m["category_scope"]) == context]
    source_features = features(candidate.get("properties", []), scope)
    allowed = {(m["supplier_property_name"], m["canonical_property_key"]) for m in mapped}
    source_features = [f for f in source_features if (f["property_name"], f["key"]) in allowed]
    by_key = defaultdict(list)
    for f in source_features:
        by_key[f["key"]].append(f)
    contradictions, comparisons, supporting = [], [], []
    reasons = []
    if scope == "equipment_scope_conflict":
        reasons.append("title_category_equipment_contradiction")
    for key, values in by_key.items():
        if len({(v["form"], tuple(v["values"])) for v in values if v["parsed"]}) > 1:
            contradictions.append({"key": key, "reason": "conflicting_source_values"})
    product = Product(key="source", name=str(candidate.get("name") or ""), brand=str(candidate.get("brand") or ""),
                      model=str(candidate.get("model") or ""), category=str(candidate.get("source_category") or ""))
    role = classify_model_role(product.name, product.category, product.model)
    kind = classify_product_kind(product.name, product.category).product_kind
    full_model = normalize_model(product.model, product.brand)
    own_proven = role.role == OWN_MODEL and candidate.get("model_role") == OWN_MODEL
    title_model = normalize_model(extract_model(product.name), product.brand)
    if not own_proven or kind in {"jaw", "handle", "controller", "accessory", "unknown"}:
        reasons.append("accessory_or_own_identity_not_proven")
    if not product.brand or not full_model or (title_model and title_model != full_model):
        reasons.append("brand_or_full_model_execution_not_proven")
    execution = normalize_model(candidate.get("execution"))
    if execution and not full_model.endswith(execution):
        reasons.append("execution_contradiction")
    if _conflicts(candidate.get("match_conflicts")) or _conflicts(candidate.get("semantic_conflicts")):
        reasons.append("upstream_guard_blocks_confirmation")
    if reference:
        reference_scope = equipment_scope(str(reference.get("name") or ""), str(reference.get("source_category") or ""))
        if reference_scope != scope:
            contradictions.append({"reason": "equipment_scope_mismatch", "source_scope": scope,
                                   "reference_scope": reference_scope})
        other = Product(key="reference", name=str(reference.get("name") or ""), brand=str(reference.get("brand") or ""),
                        model=str(reference.get("model") or ""), category=str(reference.get("source_category") or ""))
        for left, right in ((product, other), (other, product)):
            quality, _, conflicts, warnings = classify_identity_quality(replace(left, properties={}), replace(right, properties={}))
            if quality or conflicts or warnings:
                contradictions.append({"reason": "semantic_guard", "conflicts": conflicts, "warnings": warnings})
        for left in source_features:
            for right in features(reference.get("properties", []), scope):
                if left["key"] != right["key"]:
                    continue
                equal = _same(left, right)
                comparison = {"key": left["key"], "source": left, "reference": right, "agreement": equal}
                comparisons.append(comparison)
                if not equal and left["importance"] == IDENTITY_CRITICAL:
                    contradictions.append(comparison)
                elif equal and left["importance"] == IDENTITY_CRITICAL:
                    supporting.append(left["key"])
    section = next((m for m in evidence.get("section_mappings", []) if
                    (m["source"], m["source_category"], m["category_scope"]) == context), None)
    section_verified = bool(section and section["verdict"] == "SAFE")
    exact_ids = registry_index.get("models", {}).get(full_model, [])
    family = re.match(r"[a-zа-я]+\d+", full_model)
    plausible_ids = registry_index.get("families", {}).get(family.group(0) if family else full_model, [])
    absence_support = bool(registry_index.get("complete") and full_model and not exact_ids and not plausible_ids)
    absence_verified = bool(absence_support and candidate.get("absence_review_ref")
                            and candidate.get("absence_registry_sha256") == registry_index.get("sha256")
                            and registry_index.get("sha256"))
    required = REQUIRED_PROPERTIES.get(scope, ())
    missing = [key for key in required if not any(f["parsed"] for f in by_key.get(key, []))]
    group_verified = bool(new_group and new_group.get("status") == "PROVEN_EQUIVALENT_NEW_PROPOSAL"
                          and new_group.get("new_group_id") and new_group.get("equivalence_evidence")
                          and any((r.get("source"), r.get("external_id"), r.get("new_candidate_id")) ==
                                  (candidate.get("source"), candidate.get("external_id"), candidate.get("new_candidate_id"))
                                  for r in new_group.get("source_records", [])))
    applicable = candidate.get("classification") in {"NEW_CANDIDATE", "REVIEW"}
    identity_complete = bool(required and not missing and product.brand and own_proven and full_model
                             and candidate.get("evidence_ref") and not reasons and not contradictions)
    gates = (applicable, group_verified, absence_verified, section_verified, identity_complete, enable_ready_proposal)
    state = None
    for stage, passed in zip(READINESS_STATES, gates):
        if not passed:
            break
        state = stage
    return {"diagnostic_only": True, "input_classification": candidate.get("classification"),
        "rule_version": RULE_VERSION, "proposed_readiness": state,
        "CHARACTERISTIC_IDENTITY_SUPPORT": {"keys": sorted(set(supporting)), "identity_evidence_complete": identity_complete,
                                             "missing_required_properties": missing, "comparisons": comparisons},
        "CHARACTERISTIC_CONTRADICTION": {"blocked": bool(contradictions or reasons), "evidence": contradictions, "reasons": reasons},
        "SECTION_EVIDENCE": {"verified": section_verified, "mapping": section},
        "CANONICAL_ABSENCE_SUPPORT": {"support": absence_support, "verified": absence_verified,
                                     "exact_candidate_ids": exact_ids, "plausible_family_ids": plausible_ids,
                                     "registry_sha256": registry_index.get("sha256")},
        "ready_to_create_proposed": state == "READY_TO_CREATE", "input_mutated": False}
