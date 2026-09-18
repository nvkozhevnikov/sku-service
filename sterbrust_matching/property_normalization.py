"""Typed, lossless property normalization for machine-catalog identity evidence."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Mapping


PROPERTY_IDENTITY_CONFLICT = "PROPERTY_IDENTITY_CONFLICT"
PROPERTY_FORMAT_VARIATION = "PROPERTY_FORMAT_VARIATION"
PROPERTY_UNIT_VARIATION = "PROPERTY_UNIT_VARIATION"
PROPERTY_WITHIN_TOLERANCE = "PROPERTY_WITHIN_TOLERANCE"
PROPERTY_DATA_QUALITY_WARNING = "PROPERTY_DATA_QUALITY_WARNING"
PROPERTY_EQUAL = "PROPERTY_EQUAL"

IDENTITY_CRITICAL = "IDENTITY_CRITICAL"
SUPPORTING = "SUPPORTING"
NON_IDENTITY = "NON_IDENTITY"


@dataclass(frozen=True)
class NormalizedProperty:
    raw_value: str
    numeric_value: Decimal | None
    canonical_unit: str | None
    normalized_text: str | None
    normalization_rule: str
    input_unit: str | None = None

    def evidence(self) -> dict[str, str | None]:
        return {
            "raw_value": self.raw_value,
            "numeric_value": None if self.numeric_value is None else format(self.numeric_value, "f"),
            "canonical_unit": self.canonical_unit,
            "normalized_text": self.normalized_text,
            "normalization_rule": self.normalization_rule,
            "input_unit": self.input_unit,
        }


@dataclass(frozen=True)
class PropertyPolicy:
    property_type: str
    importance: str
    absolute_tolerance: Decimal = Decimal("0")
    relative_tolerance: Decimal = Decimal("0")


@dataclass(frozen=True)
class PropertyComparison:
    compatible: bool
    classification: str
    source: NormalizedProperty
    candidate: NormalizedProperty
    comparison_method: str
    tolerance: str
    property_importance: str

    def evidence(self) -> dict:
        return {
            "source": self.source.evidence(),
            "candidate": self.candidate.evidence(),
            "comparison_method": self.comparison_method,
            "unit_conversion": bool(
                self.source.input_unit and self.candidate.input_unit
                and self.source.input_unit != self.candidate.input_unit
            ),
            "tolerance": self.tolerance,
            "property_importance": self.property_importance,
            "result": self.classification,
            "compatible": self.compatible,
        }


DEFAULT_PROPERTY_POLICIES: dict[str, PropertyPolicy] = {
    "voltage": PropertyPolicy("voltage", IDENTITY_CRITICAL),
    "power": PropertyPolicy("power", IDENTITY_CRITICAL),
    "length": PropertyPolicy("length", IDENTITY_CRITICAL),
    "width": PropertyPolicy("width", IDENTITY_CRITICAL),
    "height": PropertyPolicy("height", IDENTITY_CRITICAL),
    "diameter": PropertyPolicy("diameter", IDENTITY_CRITICAL),
    "weight": PropertyPolicy("weight", SUPPORTING, absolute_tolerance=Decimal("1")),
    "capacity": PropertyPolicy("capacity", IDENTITY_CRITICAL),
    "execution": PropertyPolicy("execution", IDENTITY_CRITICAL),
}

# Extension point: callers may overlay category-specific property policies.
def resolve_policy(property_name: str, overrides: Mapping[str, PropertyPolicy] | None = None) -> PropertyPolicy:
    key = property_name.strip().lower()
    if overrides and key in overrides:
        return overrides[key]
    return DEFAULT_PROPERTY_POLICIES.get(key, PropertyPolicy("string", NON_IDENTITY))


_UNIT_ALIASES = {
    "в": "v", "v": "v",
    "вт": "w", "w": "w", "квт": "kw", "kw": "kw",
    "мм": "mm", "mm": "mm", "см": "cm", "cm": "cm", "м": "m", "m": "m",
    "г": "g", "g": "g", "кг": "kg", "kg": "kg", "т": "t", "t": "t",
    "мл": "ml", "ml": "ml", "л": "l", "l": "l", "м3": "m3", "m3": "m3",
    "об/мин": "rpm", "обмин": "rpm", "rpm": "rpm", "min-1": "rpm",
}

_TYPE_UNITS = {
    "voltage": ({"v": Decimal("1")}, "V"),
    "power": ({"w": Decimal("1"), "kw": Decimal("1000")}, "W"),
    "length": ({"mm": Decimal("1"), "cm": Decimal("10"), "m": Decimal("1000")}, "mm"),
    "width": ({"mm": Decimal("1"), "cm": Decimal("10"), "m": Decimal("1000")}, "mm"),
    "height": ({"mm": Decimal("1"), "cm": Decimal("10"), "m": Decimal("1000")}, "mm"),
    "diameter": ({"mm": Decimal("1"), "cm": Decimal("10"), "m": Decimal("1000")}, "mm"),
    "weight": ({"g": Decimal("0.001"), "kg": Decimal("1"), "t": Decimal("1000")}, "kg"),
    "capacity": ({"ml": Decimal("0.001"), "l": Decimal("1"), "m3": Decimal("1000")}, "L"),
    "rpm": ({"rpm": Decimal("1")}, "rpm"),
}


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", "" if value is None else str(value)).strip().lower())


def normalize_property(value: object, property_type: str) -> NormalizedProperty:
    raw = "" if value is None else str(value)
    text = _text(value).replace("ё", "е")
    kind = property_type.strip().lower()
    if kind == "voltage" and re.search(r"/|\bor\b|\bили\b", text):
        values = re.findall(r"\d+(?:[.,]\d+)?", text)
        if values:
            normalized = "|".join(sorted({format(Decimal(item.replace(",", ".")), "f") for item in values}))
            return NormalizedProperty(raw, None, "V", normalized, "voltage:multi_value", "v")
    if kind == "thread_range":
        compact = text.replace("–", "-").replace("—", "-").replace(" ", "")
        match = re.search(r"m\s*(\d+(?:[.,]\d+)?)\s*-\s*m?\s*(\d+(?:[.,]\d+)?)", compact, re.I)
        if match:
            normalized = f"m{match.group(1).replace(',', '.')}-m{match.group(2).replace(',', '.')}"
            return NormalizedProperty(raw, None, None, normalized, "thread_range:m_min_m_max")
        return NormalizedProperty(raw, None, None, compact or None, "thread_range:unparsed")
    if kind in {"dimension", "range"}:
        compact = text.replace("×", "x").replace("х", "x").replace("–", "-").replace("—", "-")
        numbers = re.findall(r"\d+(?:[.,]\d+)?", compact)
        unit_match = re.search(r"(?:^|\s)(mm|cm|m|мм|см|м)(?:\s|$)", compact, re.I)
        if numbers:
            unit = _UNIT_ALIASES.get((unit_match.group(1).lower() if unit_match else "mm"), "mm")
            factor = {"mm": Decimal("1"), "cm": Decimal("10"), "m": Decimal("1000")}.get(unit)
            if factor:
                converted = [format(Decimal(number.replace(",", ".")) * factor, "f") for number in numbers]
                return NormalizedProperty(raw, None, "mm", "x".join(converted), f"{kind}:{unit}_components_to_mm", unit)
        return NormalizedProperty(raw, None, None, compact or None, f"{kind}:unparsed")
    if kind in _TYPE_UNITS:
        match = re.fullmatch(r"\s*([+-]?\d+(?:[.,]\d+)?)\s*([a-zа-я0-9³]+)\s*", text, re.I)
        if not match:
            return NormalizedProperty(raw, None, None, text or None, f"{kind}:unparsed")
        try:
            number = Decimal(match.group(1).replace(",", "."))
        except InvalidOperation:
            return NormalizedProperty(raw, None, None, text or None, f"{kind}:invalid_decimal")
        raw_unit = match.group(2).lower().replace("³", "3")
        unit = _UNIT_ALIASES.get(raw_unit)
        factors, canonical = _TYPE_UNITS[kind]
        if unit not in factors:
            return NormalizedProperty(raw, number, None, text, f"{kind}:unknown_unit", raw_unit)
        canonical_value = number * factors[unit]
        return NormalizedProperty(raw, canonical_value, canonical, None, f"{kind}:{unit}_to_{canonical.lower()}", raw_unit)
    normalized = "".join(re.findall(r"[a-zа-я0-9]+", text)) or None
    return NormalizedProperty(raw, None, None, normalized, f"{kind or 'string'}:identity_token")


def compare_property(
    source_value: object,
    candidate_value: object,
    property_name: str,
    policy_overrides: Mapping[str, PropertyPolicy] | None = None,
) -> PropertyComparison:
    policy = resolve_policy(property_name, policy_overrides)
    source = normalize_property(source_value, policy.property_type)
    candidate = normalize_property(candidate_value, policy.property_type)
    tolerance = f"abs={policy.absolute_tolerance};rel={policy.relative_tolerance}"
    if policy.property_type == "voltage" and (source.normalized_text or candidate.normalized_text):
        def voltage_values(item: NormalizedProperty) -> set[str]:
            if item.numeric_value is not None:
                return {format(item.numeric_value, "f")}
            if item.normalized_text:
                return set(item.normalized_text.split("|"))
            return set()
        left_values, right_values = voltage_values(source), voltage_values(candidate)
        if left_values and right_values and left_values & right_values:
            classification = PROPERTY_EQUAL if left_values == right_values else PROPERTY_WITHIN_TOLERANCE
            return PropertyComparison(True, classification, source, candidate, "compatible_voltage_set", tolerance, policy.importance)
    if source.numeric_value is not None and candidate.numeric_value is not None and source.canonical_unit == candidate.canonical_unit:
        difference = abs(source.numeric_value - candidate.numeric_value)
        scale = max(abs(source.numeric_value), abs(candidate.numeric_value))
        allowed = max(policy.absolute_tolerance, scale * policy.relative_tolerance)
        if difference == 0:
            unit_changed = source.input_unit != candidate.input_unit
            raw_changed = _text(source.raw_value) != _text(candidate.raw_value)
            classification = PROPERTY_UNIT_VARIATION if unit_changed else PROPERTY_FORMAT_VARIATION if raw_changed else PROPERTY_EQUAL
            return PropertyComparison(True, classification, source, candidate, "canonical_decimal", tolerance, policy.importance)
        if difference <= allowed:
            return PropertyComparison(True, PROPERTY_WITHIN_TOLERANCE, source, candidate, "canonical_decimal_tolerance", tolerance, policy.importance)
        classification = PROPERTY_IDENTITY_CONFLICT if policy.importance == IDENTITY_CRITICAL else PROPERTY_DATA_QUALITY_WARNING
        return PropertyComparison(policy.importance != IDENTITY_CRITICAL, classification, source, candidate, "canonical_decimal", tolerance, policy.importance)
    if source.normalized_text and candidate.normalized_text:
        if source.normalized_text == candidate.normalized_text:
            classification = PROPERTY_FORMAT_VARIATION if source.raw_value != candidate.raw_value else PROPERTY_EQUAL
            return PropertyComparison(True, classification, source, candidate, "normalized_identity_token", tolerance, policy.importance)
        classification = PROPERTY_IDENTITY_CONFLICT if policy.importance == IDENTITY_CRITICAL else PROPERTY_DATA_QUALITY_WARNING
        return PropertyComparison(policy.importance != IDENTITY_CRITICAL, classification, source, candidate, "normalized_identity_token", tolerance, policy.importance)
    return PropertyComparison(True, PROPERTY_DATA_QUALITY_WARNING, source, candidate, "unparsed_raw_preserved", tolerance, policy.importance)
