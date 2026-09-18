from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
import ipaddress
import re
import socket
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlparse


QUALITY_FIELDS = (
    "price", "sku", "brand", "description", "property", "image", "category",
)
CONFIRMED_MISSING = frozenset({"HTTP_404", "HTTP_410", "NOT_IN_SITEMAP", "NOT_DISCOVERED"})
NON_MISSING = frozenset({"TIMEOUT", "HTTP_403", "HTTP_429", "NETWORK_ERROR", "PARSER_ERROR"})
FIELD_POLICIES = frozenset({
    "SUPPLIER_ALLOWED", "MANUAL_ONLY", "STERBRUST_OWNED", "REVIEW_REQUIRED", "NEVER_OVERWRITE",
})
FIELD_LABELS_RU = {
    "price": "Цена", "availability": "Наличие", "quantity": "Количество",
    "description": "Описание", "brand": "Бренд", "category": "Категория",
}
FILL_RATE_LABELS_RU = {
    "price": "Цена", "sku": "SKU", "brand": "Бренд", "description": "Описание",
    "property": "Свойства", "image": "Изображения", "category": "Категория",
}


@dataclass(frozen=True)
class QualityProfile:
    products_seen: int
    products_parsed: int
    fill_rates: Mapping[str, float]

    def __post_init__(self) -> None:
        if self.products_seen < 0 or self.products_parsed < 0:
            raise ValueError("quality counts cannot be negative")
        if any(not 0 <= float(value) <= 1 for value in self.fill_rates.values()):
            raise ValueError("fill rates must be between zero and one")


@dataclass(frozen=True)
class AnomalyThresholds:
    count_ratio_min: float = .60
    fill_rate_drop: float = .35
    fill_rate_min: float = .25

    def __post_init__(self) -> None:
        if not 0 < float(self.count_ratio_min) <= 1:
            raise ValueError("Минимальная доля количества должна быть больше 0 и не больше 1")
        if not 0 <= float(self.fill_rate_drop) <= 1:
            raise ValueError("Допустимое падение заполнения должно быть от 0 до 1")
        if not 0 <= float(self.fill_rate_min) <= 1:
            raise ValueError("Критический минимум заполнения должен быть от 0 до 1")


def effective_crawl_settings(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Operator-visible defaults when crawl_settings is missing or empty."""
    source = dict(raw or {}) if isinstance(raw, Mapping) else {}
    quality = dict(source.get("data_quality_policy") or {}) if isinstance(source.get("data_quality_policy"), Mapping) else {}
    missing = dict(source.get("missing_policy") or {}) if isinstance(source.get("missing_policy"), Mapping) else {}
    quality_defaults = AnomalyThresholds()
    missing_defaults = MissingPolicy()
    return {
        **source,
        "data_quality_policy": {
            "count_ratio_min": float(quality.get("count_ratio_min", quality_defaults.count_ratio_min)),
            "fill_rate_drop": float(quality.get("fill_rate_drop", quality_defaults.fill_rate_drop)),
            "fill_rate_min": float(quality.get("fill_rate_min", quality_defaults.fill_rate_min)),
        },
        "missing_policy": {
            "warning_at": int(missing.get("warning_at", missing_defaults.warning_at)),
            "candidate_at": int(missing.get("candidate_at", missing_defaults.candidate_at)),
        },
    }

@dataclass(frozen=True)
class AnomalyResult:
    codes: tuple[str, ...]
    evidence: Mapping[str, Any]
    critical: bool
    allow_missing_consequences: bool
    run_status: str


def detect_anomalies(previous: QualityProfile | None, current: QualityProfile,
                     thresholds: AnomalyThresholds = AnomalyThresholds(),
                     *, schema_signals: Sequence[str] = ()) -> AnomalyResult:
    codes: list[str] = []
    evidence: dict[str, Any] = {}
    if previous and previous.products_seen:
        ratio = current.products_seen / previous.products_seen
        evidence["count_ratio"] = ratio
        if ratio < thresholds.count_ratio_min:
            codes.append("CRAWL_COUNT_COLLAPSE")
    if previous:
        for field_name in QUALITY_FIELDS:
            old = float(previous.fill_rates.get(field_name, 0))
            new = float(current.fill_rates.get(field_name, 0))
            if old - new >= thresholds.fill_rate_drop and new <= thresholds.fill_rate_min:
                codes.append(f"FIELD_COVERAGE_COLLAPSE:{field_name}")
                evidence[field_name] = {"previous": old, "current": new}
    if schema_signals:
        codes.append("PARSER_SCHEMA_DRIFT")
        evidence["schema_signals"] = sorted(set(schema_signals))
    critical = bool(codes)
    return AnomalyResult(tuple(codes), evidence, critical, not critical, "partial" if critical else "succeeded")


@dataclass(frozen=True)
class MissingState:
    consecutive_confirmed: int = 0
    state: str = "ACTIVE"
    last_evidence_type: str = "SEEN"


@dataclass(frozen=True)
class MissingPolicy:
    warning_at: int = 2
    candidate_at: int = 3

    def __post_init__(self) -> None:
        if int(self.warning_at) < 1 or int(self.candidate_at) <= int(self.warning_at):
            raise ValueError("Порог MISSING_CANDIDATE должен быть больше WARNING, а WARNING не меньше 1")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "MissingPolicy":
        if not value:
            return cls()
        return cls(warning_at=int(value.get("warning_at", 2)), candidate_at=int(value.get("candidate_at", 3)))

    def state_for(self, count: int) -> str:
        if count >= self.candidate_at:
            return "MISSING_CANDIDATE"
        if count >= self.warning_at:
            return "WARNING"
        return "WATCH"


def observe_missing(previous: MissingState, evidence_type: str, *, warning_at: int = 2,
                    candidate_at: int = 3, anomaly_guarded: bool = False,
                    policy: MissingPolicy | None = None) -> MissingState:
    evidence_type = evidence_type.upper()
    resolved = policy or MissingPolicy(warning_at=warning_at, candidate_at=candidate_at)
    if evidence_type == "SEEN":
        return MissingState(0, "ACTIVE", evidence_type)
    if anomaly_guarded or evidence_type in NON_MISSING:
        return MissingState(previous.consecutive_confirmed, previous.state, evidence_type)
    if evidence_type not in CONFIRMED_MISSING:
        raise ValueError("unknown missing evidence")
    count = previous.consecutive_confirmed + 1
    return MissingState(count, resolved.state_for(count), evidence_type)


def deactivation_was_suppressed(*, deactivation_allowed: bool, anomaly_critical: bool) -> bool:
    """Single interpretation of whether missing deactivation was withheld."""
    return (not deactivation_allowed) or bool(anomaly_critical)


UNIT_ALIASES = {
    "мм": "mm", "mm": "mm", "см": "cm", "cm": "cm", "м": "m", "m": "m",
    "г": "g", "g": "g", "кг": "kg", "kg": "kg",
    "вт": "W", "w": "W", "квт": "kW", "kw": "kW",
    "в": "V", "v": "V", "об/мин": "rpm", "rpm": "rpm",
    "м/мин": "m/min", "m/min": "m/min", "мм/мин": "mm/min", "mm/min": "mm/min",
    "°": "degrees", "град": "degrees", "degrees": "degrees",
}
DIMENSIONS = {
    "mm": "length", "cm": "length", "m": "length", "g": "mass", "kg": "mass",
    "W": "power", "kW": "power", "V": "voltage", "rpm": "rotation",
    "m/min": "speed", "mm/min": "speed", "degrees": "angle",
}
TO_BASE = {
    "mm": Decimal("1"), "cm": Decimal("10"), "m": Decimal("1000"),
    "g": Decimal("1"), "kg": Decimal("1000"), "W": Decimal("1"), "kW": Decimal("1000"),
    "V": Decimal("1"), "rpm": Decimal("1"), "m/min": Decimal("1000"),
    "mm/min": Decimal("1"), "degrees": Decimal("1"),
}


@dataclass(frozen=True)
class NormalizedProperty:
    raw_value: str
    raw_unit: str
    numeric_value: Decimal | None
    canonical_unit: str | None
    status: str


def normalize_property_value(raw_value: str, raw_unit: str, canonical_unit: str) -> NormalizedProperty:
    source_unit = UNIT_ALIASES.get(raw_unit.strip().lower())
    target_unit = UNIT_ALIASES.get(canonical_unit.strip().lower(), canonical_unit.strip())
    try:
        number = Decimal(raw_value.strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, AttributeError):
        return NormalizedProperty(raw_value, raw_unit, None, target_unit or None, "INVALID_NUMERIC")
    if not source_unit or target_unit not in DIMENSIONS:
        return NormalizedProperty(raw_value, raw_unit, None, target_unit or None, "UNKNOWN_UNIT")
    if DIMENSIONS[source_unit] != DIMENSIONS[target_unit]:
        return NormalizedProperty(raw_value, raw_unit, None, target_unit, "INCOMPATIBLE_DIMENSION")
    value = number * TO_BASE[source_unit] / TO_BASE[target_unit]
    return NormalizedProperty(raw_value, raw_unit, value.normalize(), target_unit, "NORMALIZED")


def mapping_evidence(source_name: str, source_unit: str, category: str,
                     candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    tokens = set(re.findall(r"[a-zа-я0-9]+", source_name.lower()))
    result = []
    source_canonical_unit = UNIT_ALIASES.get(source_unit.strip().lower())
    for candidate in candidates:
        candidate_tokens = set(re.findall(r"[a-zа-я0-9]+", str(candidate.get("display_name_ru", "")).lower()))
        alias_tokens = set(re.findall(r"[a-zа-я0-9]+", " ".join(candidate.get("aliases", ())).lower()))
        name_score = len(tokens & (candidate_tokens | alias_tokens)) / max(1, len(tokens | candidate_tokens))
        unit = candidate.get("canonical_unit")
        unit_score = 1 if source_canonical_unit and unit and DIMENSIONS.get(source_canonical_unit) == DIMENSIONS.get(unit) else 0
        category_score = 1 if category and category in candidate.get("categories", ()) else 0
        score = round(.65 * name_score + .25 * unit_score + .10 * category_score, 5)
        result.append({"canonical_property_id": candidate.get("id"), "confidence": score,
                       "evidence": {"normalized_name": name_score, "unit_compatible": bool(unit_score), "category_context": bool(category_score)}})
    return sorted(result, key=lambda item: (-item["confidence"], str(item["canonical_property_id"])))


def validate_supplier_url(url: str, allowed_hosts: Sequence[str], *, resolve_dns: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL поставщика недопустим")
    host = parsed.hostname.rstrip(".").lower()
    allowed = {item.rstrip(".").lower() for item in allowed_hosts}
    if host not in allowed:
        raise ValueError("Хост не разрешён адаптером поставщика")
    try:
        address = ipaddress.ip_address(host)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValueError("Локальные и служебные адреса запрещены")
    except ValueError as error:
        if "запрещены" in str(error):
            raise
    if resolve_dns:
        for _, _, _, _, sockaddr in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80)):
            address = ipaddress.ip_address(sockaddr[0])
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                raise ValueError("DNS указывает на локальный или служебный адрес")
    return url


def validate_fetch_targets(fetch, allowed_hosts: Sequence[str], *, resolve_dns: bool = False) -> None:
    """Validate every redirect hop and the final target, not only the input URL."""
    for target in (*tuple(getattr(fetch, "redirects", ()) or ()), getattr(fetch, "final_url", "")):
        if target:
            validate_supplier_url(str(target), allowed_hosts, resolve_dns=resolve_dns)


def raw_normalized_diff(current: Mapping[str, Any], proposed: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    keys = sorted(set(current) | set(proposed))
    return {key: {"current": current.get(key), "proposed": proposed.get(key)}
            for key in keys if current.get(key) != proposed.get(key)}


def build_change_preview(current: Mapping[str, Any], proposed: Mapping[str, Any],
                         policies: Mapping[str, str], *, blocked: bool = False,
                         exclusion: bool = False, identity_blocked: bool = False,
                         forbidden_fields: Iterable[str] = ()) -> list[dict[str, Any]]:
    forbidden = set(forbidden_fields)
    result = []
    for field_name, values in raw_normalized_diff(current, proposed).items():
        policy = policies.get(field_name, "REVIEW_REQUIRED")
        if policy not in FIELD_POLICIES:
            raise ValueError("unknown field policy")
        if field_name in forbidden:
            decision = "EXCLUDED_BY_RULE"
            reason = "Поле исключено бизнес-правилом"
        elif blocked:
            decision = "ANOMALY_GUARD_BLOCKED"
            reason = "Активна критическая аномалия данных"
        elif exclusion:
            decision = "EXCLUDED_BY_RULE"
            reason = "Товар исключён бизнес-правилом"
        elif identity_blocked:
            decision = "IDENTITY_BLOCKED"
            reason = "Идентичность товара не подтверждена"
        elif policy == "SUPPLIER_ALLOWED":
            decision = "PROPOSE_UPDATE"
            reason = "Политика разрешает предложение поставщика"
        elif policy == "REVIEW_REQUIRED":
            decision = "NEEDS_REVIEW"
            reason = "Требуется решение оператора"
        else:
            decision = "NO_UPDATE"
            reason = "Поле защищено политикой владения"
        result.append({"field": field_name, "label": FIELD_LABELS_RU.get(field_name, field_name),
                       **values, "policy": policy, "decision": decision, "reason": reason})
    return result


@dataclass(frozen=True)
class ResolvedBusinessRules:
    excluded: bool = False
    force_review: bool = False
    brand_override: str | None = None
    category_override: str | None = None
    forbidden_fields: frozenset[str] = frozenset()
    property_overrides: Mapping[str, int] = field(default_factory=dict)
    matched_rule_ids: tuple[int, ...] = ()


class BusinessRuleResolver:
    """One centralized, deterministic interpretation of active internal rules."""

    def __init__(self, rules: Iterable[Mapping[str, Any]]) -> None:
        self.rules = tuple(rule for rule in rules if rule.get("active", True))

    def resolve(self, *, supplier_id: int, source_product_id: int,
                external_id: str, category: str = "") -> ResolvedBusinessRules:
        excluded = force_review = False
        brand_override = category_override = None
        forbidden: set[str] = set()
        property_overrides: dict[str, int] = {}
        matched: list[int] = []
        for rule in self.rules:
            if rule.get("supplier_id") not in (None, supplier_id):
                continue
            scope = rule.get("scope")
            key = str(rule.get("entity_key") or "")
            applies = (
                scope == "supplier"
                or (scope == "product" and key in {str(source_product_id), external_id})
                or (scope == "category" and key == category)
                or (scope == "property" and bool(key))
            )
            if not applies:
                continue
            matched.append(int(rule.get("id") or 0))
            kind = rule.get("rule_type")
            config = rule.get("config") if isinstance(rule.get("config"), Mapping) else {}
            if kind in {"IGNORE_PRODUCT", "DO_NOT_IMPORT"}:
                excluded = True
            elif kind == "FORCE_REVIEW":
                force_review = True
            elif kind == "BRAND_OVERRIDE":
                brand_override = str(config.get("value") or "") or None
            elif kind == "CATEGORY_OVERRIDE":
                category_override = str(config.get("value") or "") or None
            elif kind == "DO_NOT_USE_SUPPLIER_FIELD":
                field_name = str(config.get("field") or key)
                if field_name:
                    forbidden.add(field_name)
            elif kind == "PROPERTY_MAPPING_OVERRIDE":
                canonical_id = config.get("canonical_property_id")
                if canonical_id not in (None, ""):
                    property_overrides[key] = int(canonical_id)
        return ResolvedBusinessRules(excluded, force_review, brand_override, category_override,
                                     frozenset(forbidden), property_overrides, tuple(matched))


PRODUCT_COLUMNS = (
    ("supplier_name", "Поставщик"), ("external_id", "External ID"), ("article", "Артикул"),
    ("sku", "SKU"), ("brand_raw", "Бренд"), ("model", "Модель"), ("name", "Название"),
    ("category", "Категория"), ("price", "Цена"), ("old_price", "Старая цена"),
    ("currency", "Валюта"), ("price_type", "Тип цены"), ("availability_normalized", "Наличие"),
    ("quantity", "Количество"), ("decision", "Статус сопоставления"),
    ("best_sterbrust_id", "ID товара Sterbrust"), ("source_url", "URL поставщика"),
    ("description_text", "Описание"), ("property_count", "Количество характеристик"),
    ("image_count", "Количество изображений"), ("document_count", "Количество документов"),
    ("relation_count", "Количество связей"), ("last_seen_at", "Последнее обнаружение"),
    ("last_success_at", "Последнее успешное обновление"),
)


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if isinstance(value, date):
        return value
    if isinstance(value, (dict, list, tuple)):
        import json
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return value


def csv_bytes(rows: Iterable[Mapping[str, Any]], columns: Sequence[tuple[str, str]] = PRODUCT_COLUMNS) -> bytes:
    target = StringIO(newline="")
    writer = csv.writer(target, lineterminator="\r\n")
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([_cell(row.get(key)) for key, _ in columns])
    return ("\ufeff" + target.getvalue()).encode("utf-8")


def csv_chunks(rows: Iterable[Mapping[str, Any]], columns: Sequence[tuple[str, str]] = PRODUCT_COLUMNS) -> Iterator[bytes]:
    """Stream one CSV record at a time with a single UTF-8 BOM."""
    target = StringIO(newline="")
    writer = csv.writer(target, lineterminator="\r\n")
    writer.writerow([label for _, label in columns])
    yield ("\ufeff" + target.getvalue()).encode("utf-8")
    for row in rows:
        target.seek(0); target.truncate(0)
        writer.writerow([_cell(row.get(key)) for key, _ in columns])
        yield target.getvalue().encode("utf-8")


def xlsx_bytes(sheets: Mapping[str, tuple[Sequence[tuple[str, str]], Iterable[Mapping[str, Any]]]]) -> bytes:
    from openpyxl import Workbook
    workbook = Workbook(write_only=True)
    for sheet_name, (columns, rows) in sheets.items():
        sheet = workbook.create_sheet(sheet_name[:31])
        sheet.append([label for _, label in columns])
        for row in rows:
            sheet.append([_cell(row.get(key)) for key, _ in columns])
    target = BytesIO()
    workbook.save(target)
    return target.getvalue()


def product_workbook(rows: Iterable[Mapping[str, Any]], details: Mapping[str, Iterable[Mapping[str, Any]]] | None = None) -> bytes:
    details = details or {}
    id_columns = (("supplier_code", "Поставщик code"), ("external_id", "External ID"), ("source_product_id", "Source product ID"))
    sheets = {
        "Товары": (PRODUCT_COLUMNS, rows),
        "Характеристики": (id_columns + (("raw_name", "Свойство"),("raw_value", "Значение"),("raw_unit", "Единица")), details.get("properties", ())),
        "Изображения": (id_columns + (("source_url", "URL"),("is_primary", "Основное")), details.get("images", ())),
        "Документы": (id_columns + (("source_url", "URL"),("document_type", "Тип")), details.get("documents", ())),
        "Связи": (id_columns + (("relation_type", "Тип связи"),("to_source_product_id", "Связанный Source product ID")), details.get("relations", ())),
        "Сопоставление Sterbrust": (id_columns + (("sterbrust_product_id", "Sterbrust Product ID"),("decision", "Решение")), details.get("matches", ())),
        "История решений": (id_columns + (("decision_class", "Решение"),("created_at", "Дата"),("user_id", "User ID")), details.get("decisions", ())),
    }
    return xlsx_bytes(sheets)


def fill_rates_text(metrics: Mapping[str, Any] | None) -> str:
    source = metrics or {}
    rates = source.get("fill_rates") if isinstance(source.get("fill_rates"), Mapping) else {
        key.replace("_fill_rate", ""): value for key, value in source.items() if str(key).endswith("_fill_rate")
    }
    parts = []
    for key in ("price", "sku", "brand", "description", "property", "image", "category"):
        if key in rates:
            parts.append(f"{FILL_RATE_LABELS_RU[key]} {round(float(rates[key]) * 100)}%")
    return ", ".join(parts) if parts else "—"


def safe_export_filename(kind: str, extension: str, supplier: str = "") -> str:
    safe_kind = re.sub(r"[^a-z0-9_-]", "_", kind.lower())[:50] or "export"
    safe_supplier = re.sub(r"[^a-z0-9_-]", "_", supplier.lower())[:50]
    suffix = f"_{safe_supplier}" if safe_supplier else ""
    return f"{safe_kind}{suffix}_{date.today().isoformat()}.{extension}"


@dataclass
class InMemoryStage6EService:
    quality_rows: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    properties: list[dict[str, Any]] = field(default_factory=list)
    mappings: list[dict[str, Any]] = field(default_factory=list)
    profiles: list[dict[str, Any]] = field(default_factory=list)
    preview_rows: list[dict[str, Any]] = field(default_factory=list)
    inspector_runs: int = 0
    reparse_runs: int = 0
    full_crawl_runs: int = 0

    def quality(self) -> list[dict[str, Any]]:
        return self.quality_rows

    def property_dashboard(self) -> dict[str, Any]:
        statuses = {key: 0 for key in ("AUTO_PROPOSED","MANUAL_CONFIRMED","MANUAL_REJECTED","IGNORED","UNMAPPED")}
        for row in self.mappings:
            statuses[row.get("status", "UNMAPPED")] = statuses.get(row.get("status", "UNMAPPED"), 0) + 1
        return {"total": len(self.mappings), "statuses": statuses, "properties": self.properties, "mappings": self.mappings, "profiles": self.profiles}

    def preview(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        rows = self.preview_rows[(page-1)*page_size:page*page_size]
        return {"rows": rows, "page": page, "page_size": page_size, "total": len(self.preview_rows),
                "pages": max(1,(len(self.preview_rows)+page_size-1)//page_size),
                "summary": {key:0 for key in ("price_changes","availability_changes","quantity_changes","no_changes","blocked_by_conflict","needs_review","new_products","accessories","excluded_by_rule","missing_supplier_data","anomaly_guard_blocked")}}

    def add_rule(self, values: Mapping[str, Any], user_id: int) -> dict[str, Any]:
        row = {"id": len(self.rules)+1, **dict(values), "created_by": user_id, "active": True}
        self.rules.append(row)
        return row

    def quality_history(self, supplier_code: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.quality_rows if supplier_code in {None, "", row.get("supplier_code")}]
        for row in rows:
            row["fill_rates_text"] = fill_rates_text(row.get("metrics"))
        return rows[:limit]

    def confirm_mapping(self, mapping_id: int, canonical_property_id: int, user_id: int) -> dict[str, Any]:
        row = next((item for item in self.mappings if int(item["id"]) == mapping_id), None)
        if not row:
            raise KeyError("Сопоставление не найдено")
        if not canonical_property_id:
            raise ValueError("Выберите каноническое свойство")
        row.update(status="MANUAL_CONFIRMED", canonical_property_id=canonical_property_id, confirmed_by=user_id)
        return row

    def decide_mapping(self, mapping_id: int, status: str, user_id: int, comment: str = "") -> dict[str, Any]:
        if status not in {"MANUAL_REJECTED", "IGNORED"}:
            raise ValueError("Недопустимое решение")
        row = next((item for item in self.mappings if int(item["id"]) == mapping_id), None)
        if not row:
            raise KeyError("Сопоставление не найдено")
        row.update(status=status, comment=comment or None, confirmed_by=user_id)
        return row

    def create_canonical_property(self, values: Mapping[str, Any], user_id: int) -> dict[str, Any]:
        row = {"id": len(self.properties) + 1, **dict(values), "created_by": user_id, "active": True}
        self.properties.append(row)
        return row

    def save_profile_property(self, values: Mapping[str, Any], user_id: int) -> dict[str, Any]:
        row = {"id": len(self.profiles) + 1, "profile_id": 1, **dict(values), "created_by": user_id}
        self.profiles.append(row)
        return row

    def remove_profile_property(self, profile_id: int, canonical_property_id: int) -> None:
        self.profiles = [row for row in self.profiles if not (row.get("id") == profile_id and row.get("canonical_property_id") == canonical_property_id)]

    def update_profile_property(self, profile_id: int, canonical_property_id: int, values: Mapping[str, Any]) -> dict[str, Any]:
        row = next((item for item in self.profiles if int(item.get("id") or 0) == profile_id), None)
        if not row:
            raise KeyError("Профиль не найден")
        row.update(dict(values), canonical_property_id=canonical_property_id)
        return row

    def set_rule_active(self, rule_id: int, active: bool, user_id: int, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("Причина обязательна")
        row = next((item for item in self.rules if int(item["id"]) == rule_id), None)
        if not row:
            raise KeyError("Правило не найдено")
        history = list((row.get("config") or {}).get("lifecycle") or [])
        history.append({"active": active, "reason": reason.strip(), "user_id": user_id})
        row["config"] = {**(row.get("config") or {}), "lifecycle": history}
        row["active"] = active
        return row

    def reparse_target(self, supplier_code: str, external_id: str) -> dict[str, Any] | None:
        return {"source_product_id": 1, "source_url": "https://partner-st.ru/catalog/item", "external_id": external_id,
                "sku": "A", "name": "Item", "brand_raw": "Partner", "description_text": "d", "supplier_id": 1,
                "supplier_code": supplier_code, "supplier_name": "Partner-ST", "base_url": "https://partner-st.ru/",
                "rate_settings": {}, "price": "10", "old_price": None, "currency": "RUB",
                "availability_raw": "yes", "availability_normalized": "in_stock", "quantity": None}

    def save_reparse_preview(self, target: Mapping[str, Any], card, user_id: int) -> dict[str, Any]:
        self.reparse_runs += 1
        current = {key: target.get(key) for key in ("external_id", "sku", "name", "brand_raw", "price", "description_text")}
        proposed = card.as_jsonable() if hasattr(card, "as_jsonable") else dict(card)
        return {"id": self.reparse_runs, "current_data": current, "proposed_data": proposed,
                "diff": raw_normalized_diff(current, proposed), "persisted": False}

    def persist_reparse_preview(self, preview_id: int, user_id: int) -> dict[str, Any]:
        return {"id": preview_id, "persisted": True, "requested_by": user_id}

    def inspect(self, *, url: str, allowed_hosts: Sequence[str], fetcher, parser) -> dict[str, Any]:
        validate_supplier_url(url, allowed_hosts)
        self.inspector_runs += 1
        fetched = fetcher(url)
        validate_fetch_targets(fetched, allowed_hosts)
        card = parser(fetched)
        return {"http_status": fetched.status_code, "canonical_url": card.canonical_url,
                "external_id": card.external_id, "sku": card.sku, "name": card.name,
                "brand": card.brand, "price": card.price, "currency": card.currency,
                "availability": card.availability_normalized, "description": card.description_text,
                "properties_count": len(card.properties), "images_count": sum(x.media_type=="image" for x in card.media),
                "documents_count": sum(x.media_type=="document" for x in card.media), "relations_count": 0,
                "raw": card.raw_data, "normalized": card.as_jsonable()}

    def reparse_preview(self, current: Mapping[str, Any], *, url: str, allowed_hosts: Sequence[str], fetcher, parser) -> dict[str, Any]:
        result = self.inspect(url=url, allowed_hosts=allowed_hosts, fetcher=fetcher, parser=parser)
        self.reparse_runs += 1
        proposed = result["normalized"]
        return {"current": dict(current), "proposed": proposed, "diff": raw_normalized_diff(current, proposed), "persisted": False}


class PostgresStage6EService:
    """Small SQL boundary for Stage 6E operator state; all Sterbrust use is SELECT-only."""

    def __init__(self, config) -> None:
        self.config = config

    def _connect(self):
        import psycopg
        return psycopg.connect(**self.config.kwargs(), autocommit=True, row_factory=psycopg.rows.dict_row)

    def quality(self) -> list[dict[str, Any]]:
        query = """SELECT s.code AS supplier_code,s.name AS supplier_name,
          count(sp.id) AS total,count(sp.id) FILTER(WHERE sp.active) AS active,
          count(sp.id) FILTER(WHERE o.price IS NULL) AS without_price,
          count(sp.id) FILTER(WHERE o.price=0) AS zero_price,
          count(sp.id) FILTER(WHERE o.currency IS NULL) AS without_currency,
          count(sp.id) FILTER(WHERE nullif(btrim(sp.sku),'') IS NULL) AS without_sku,
          count(sp.id) FILTER(WHERE nullif(btrim(sp.brand_raw),'') IS NULL) AS without_brand,
          count(sp.id) FILTER(WHERE nullif(btrim(pid.own_model),'') IS NULL) AS without_model,
          count(sp.id) FILTER(WHERE nullif(btrim(sp.description_text),'') IS NULL) AS without_description,
          count(sp.id) FILTER(WHERE coalesce(prop.cnt,0)=0) AS without_properties,
          count(sp.id) FILTER(WHERE coalesce(media.images,0)=0) AS without_images,
          count(sp.id) FILTER(WHERE coalesce(cat.cnt,0)=0) AS without_category,
          count(sp.id) FILTER(WHERE o.availability_normalized IS NULL OR o.availability_normalized='unknown') AS without_availability,
          count(sp.id) FILTER(WHERE pid.decision IN ('REVIEW_EXISTING','INSUFFICIENT_IDENTITY')) AS review,
          count(sp.id) FILTER(WHERE pid.decision='CONFLICT') AS conflicts,
          count(sp.id) FILTER(WHERE pid.decision='SAFE_NEW_PRODUCT_CANDIDATE') AS safe_new,
          (SELECT count(*) FROM crawl_errors ce WHERE ce.supplier_id=s.id AND ce.created_at>now()-interval '30 days') AS parser_errors,
          (SELECT max(finished_at) FROM crawl_runs cr WHERE cr.supplier_id=s.id AND cr.status='succeeded') AS last_successful_crawl,
          latest.critical AS latest_quality_critical,latest.anomaly_codes AS latest_anomaly_codes,
          latest.created_at AS latest_quality_at
        FROM suppliers s LEFT JOIN source_products sp ON sp.supplier_id=s.id
        LEFT JOIN LATERAL(SELECT price,currency,availability_normalized FROM offers WHERE source_product_id=sp.id AND active ORDER BY id LIMIT 1)o ON true
        LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id
        LEFT JOIN LATERAL(SELECT count(*) cnt FROM product_properties WHERE source_product_id=sp.id)prop ON true
        LEFT JOIN LATERAL(SELECT count(*) FILTER(WHERE media_type='image') images FROM product_media WHERE source_product_id=sp.id)media ON true
        LEFT JOIN LATERAL(SELECT count(*) cnt FROM product_category_paths WHERE source_product_id=sp.id AND is_current)cat ON true
        LEFT JOIN LATERAL(SELECT critical,anomaly_codes,created_at FROM data_quality_snapshots
          WHERE supplier_id=s.id ORDER BY created_at DESC,id DESC LIMIT 1)latest ON true
        GROUP BY s.id,s.code,s.name,latest.critical,latest.anomaly_codes,latest.created_at ORDER BY s.name"""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query)
            return list(cursor.fetchall())

    def quality_history(self, supplier_code: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT s.code AS supplier_code,s.name AS supplier_name,d.created_at,
              d.products_seen,d.products_parsed,d.metrics,d.anomaly_codes,d.critical,r.status,r.id AS crawl_run_id
              FROM data_quality_snapshots d JOIN suppliers s ON s.id=d.supplier_id
              LEFT JOIN crawl_runs r ON r.id=d.crawl_run_id
              WHERE (%s::text IS NULL OR s.code=%s) AND (r.status IN ('succeeded','partial') OR r.status IS NULL)
              ORDER BY d.created_at DESC,d.id DESC LIMIT %s""", (supplier_code,supplier_code,limit))
            rows = list(cursor.fetchall())
        for row in rows:
            row["fill_rates_text"] = fill_rates_text(row.get("metrics"))
        return rows

    def property_dashboard(self) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM canonical_properties ORDER BY display_name_ru,id")
            properties = list(cursor.fetchall())
            cursor.execute("""SELECT m.*,s.name AS supplier_name,c.display_name_ru AS canonical_name,c.canonical_unit,
              c.show_in_characteristics,c.usable_as_filter,c.identity_critical,
              sm.sterbrust_property_id,sm.sterbrust_code,sm.sterbrust_name,
              sm.status AS sterbrust_status,sm.filter_enabled_evidence AS sterbrust_evidence
              FROM supplier_property_mappings m JOIN suppliers s ON s.id=m.supplier_id
              LEFT JOIN canonical_properties c ON c.id=m.canonical_property_id
              LEFT JOIN sterbrust_property_mappings sm ON sm.canonical_property_id=c.id AND sm.category_context=m.category_context
              ORDER BY s.name,m.category_context,m.source_name,m.id""")
            mappings = list(cursor.fetchall())
            import json as _json
            for row in mappings:
                if not isinstance(row.get("evidence"), str):
                    row["evidence"] = _json.dumps(row.get("evidence") or {}, ensure_ascii=False)
            cursor.execute("""SELECT p.*,coalesce(jsonb_agg(jsonb_build_object('canonical_property_id',cp.id,'name',cp.display_name_ru,'filter',x.use_as_filter,'characteristic',x.use_as_characteristic,'position',x.position) ORDER BY x.position) FILTER(WHERE cp.id IS NOT NULL),'[]') AS properties
              FROM category_filter_profiles p LEFT JOIN category_filter_profile_properties x ON x.profile_id=p.id
              LEFT JOIN canonical_properties cp ON cp.id=x.canonical_property_id GROUP BY p.id ORDER BY p.display_name_ru""")
            profiles = list(cursor.fetchall())
        import json
        for row in profiles:
            props = row.get("properties")
            if isinstance(props, str):
                props = json.loads(props)
            row["properties"] = list(props or [])
        statuses = {key: 0 for key in ("AUTO_PROPOSED","MANUAL_CONFIRMED","MANUAL_REJECTED","IGNORED","UNMAPPED")}
        for row in mappings:
            statuses[row["status"]] += 1
        return {"total": len(mappings), "statuses": statuses, "properties": properties, "mappings": mappings, "profiles": profiles}

    def list_rules(self) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT r.*,s.name AS supplier_name,u.display_name AS created_by_name
              FROM business_rules r LEFT JOIN suppliers s ON s.id=r.supplier_id JOIN app_users u ON u.id=r.created_by
              ORDER BY r.active DESC,r.updated_at DESC,r.id DESC""")
            return list(cursor.fetchall())

    def add_rule(self, values: Mapping[str, Any], user_id: int) -> dict[str, Any]:
        import json
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO business_rules(supplier_id,scope,entity_key,rule_type,config,reason,created_by)
              VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s) RETURNING *""",
              (values.get("supplier_id") or None, values["scope"], values.get("entity_key") or None,
               values["rule_type"], json.dumps(values.get("config", {})), values["reason"], user_id))
            return cursor.fetchone()

    def confirm_mapping(self, mapping_id: int, canonical_property_id: int, user_id: int) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT m.inferred_type,m.source_unit,c.data_type,c.canonical_unit,c.active
              FROM supplier_property_mappings m CROSS JOIN canonical_properties c
              WHERE m.id=%s AND c.id=%s""", (mapping_id,canonical_property_id))
            compatibility=cursor.fetchone()
            if not compatibility or not compatibility["active"]:
                raise ValueError("Выбранное каноническое свойство недоступно")
            inferred=compatibility["inferred_type"]; source_unit=compatibility["source_unit"]
            target_type=compatibility["data_type"]; target_unit=compatibility["canonical_unit"]
            if inferred == "number" and target_type not in {"number","integer","range"}:
                raise ValueError("Тип значения несовместим с каноническим свойством")
            source=UNIT_ALIASES.get(str(source_unit or "").strip().lower())
            target=UNIT_ALIASES.get(str(target_unit or "").strip().lower(),target_unit)
            if source and target and DIMENSIONS.get(source) != DIMENSIONS.get(target):
                raise ValueError("Размерность единицы несовместима с каноническим свойством")
            cursor.execute("""UPDATE supplier_property_mappings SET canonical_property_id=%s,status='MANUAL_CONFIRMED',confirmed_by=%s,updated_at=now()
              WHERE id=%s RETURNING *""", (canonical_property_id,user_id,mapping_id))
            row=cursor.fetchone()
            if not row: raise KeyError("Сопоставление не найдено")
            return row

    def decide_mapping(self, mapping_id: int, status: str, user_id: int, comment: str = "") -> dict[str, Any]:
        if status not in {"MANUAL_REJECTED","IGNORED"}:
            raise ValueError("Недопустимое решение")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE supplier_property_mappings SET status=%s,comment=%s,
              confirmed_by=%s,updated_at=now() WHERE id=%s RETURNING *""",
              (status,comment or None,user_id,mapping_id))
            row=cursor.fetchone()
            if not row: raise KeyError("Сопоставление не найдено")
            return row

    def create_canonical_property(self, values: Mapping[str, Any], user_id: int) -> dict[str, Any]:
        code=str(values.get("code","")).strip().upper()
        name=str(values.get("display_name_ru","")).strip()
        data_type=str(values.get("data_type","")).strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*",code) or not name or data_type not in {"number","integer","boolean","string","enum","range"}:
            raise ValueError("Проверьте код, название и тип свойства")
        usable=bool(values.get("usable_for_identity")); critical=bool(values.get("identity_critical"))
        if critical and not usable: raise ValueError("Критичное identity-свойство должно участвовать в identity")
        with self._connect() as connection, connection.cursor() as cursor:
            try:
                cursor.execute("""INSERT INTO canonical_properties(code,display_name_ru,data_type,canonical_unit,description,
                  show_in_characteristics,usable_for_identity,identity_critical,usable_as_filter,created_by)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                  (code,name,data_type,values.get("canonical_unit") or None,values.get("description") or None,
                   bool(values.get("show_in_characteristics")),usable,critical,bool(values.get("usable_as_filter")),user_id))
            except Exception as error:
                if "canonical_properties_code_key" in str(error) or "duplicate" in str(error).lower():
                    raise ValueError("Каноническое свойство с таким кодом уже существует") from error
                raise
            return cursor.fetchone()

    def save_profile_property(self, values: Mapping[str, Any], user_id: int) -> dict[str, Any]:
        category_key=str(values.get("category_key","")).strip(); display=str(values.get("display_name_ru","")).strip()
        canonical_id=int(values.get("canonical_property_id") or 0); position=int(values.get("position") or 0)
        if not category_key or not display or canonical_id<=0 or position<0: raise ValueError("Проверьте профиль категории")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id FROM canonical_properties WHERE id=%s AND active",(canonical_id,))
            if not cursor.fetchone(): raise ValueError("Каноническое свойство не найдено")
            cursor.execute("""INSERT INTO category_filter_profiles(category_key,display_name_ru,created_by)
              VALUES(%s,%s,%s) ON CONFLICT(category_key) DO UPDATE SET display_name_ru=EXCLUDED.display_name_ru,updated_at=now()
              RETURNING id""",(category_key,display,user_id)); profile_id=cursor.fetchone()["id"]
            cursor.execute("""INSERT INTO category_filter_profile_properties
              (profile_id,canonical_property_id,use_as_characteristic,use_as_filter,position)
              VALUES(%s,%s,%s,%s,%s) ON CONFLICT(profile_id,canonical_property_id) DO UPDATE SET
              use_as_characteristic=EXCLUDED.use_as_characteristic,use_as_filter=EXCLUDED.use_as_filter,
              position=EXCLUDED.position RETURNING *""",
              (profile_id,canonical_id,bool(values.get("use_as_characteristic")),bool(values.get("use_as_filter")),position))
            return cursor.fetchone()

    def remove_profile_property(self, profile_id: int, canonical_property_id: int) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM category_filter_profile_properties WHERE profile_id=%s AND canonical_property_id=%s",(profile_id,canonical_property_id))

    def update_profile_property(self, profile_id: int, canonical_property_id: int, values: Mapping[str, Any]) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT use_as_characteristic,use_as_filter,position FROM category_filter_profile_properties
              WHERE profile_id=%s AND canonical_property_id=%s""",(profile_id,canonical_property_id))
            current=cursor.fetchone()
            if not current: raise KeyError("Свойство профиля не найдено")
            characteristic=current["use_as_characteristic"] if values.get("use_as_characteristic") is None else bool(values.get("use_as_characteristic"))
            use_filter=current["use_as_filter"] if values.get("use_as_filter") is None else bool(values.get("use_as_filter"))
            position=current["position"] if values.get("position") is None else int(values.get("position") or 0)
            if position < 0: raise ValueError("Позиция не может быть отрицательной")
            cursor.execute("""UPDATE category_filter_profile_properties SET use_as_characteristic=%s,use_as_filter=%s,position=%s
              WHERE profile_id=%s AND canonical_property_id=%s RETURNING *""",
              (characteristic,use_filter,position,profile_id,canonical_property_id))
            return cursor.fetchone()

    def set_rule_active(self, rule_id: int, active: bool, user_id: int, reason: str) -> dict[str, Any]:
        if not reason.strip(): raise ValueError("Причина обязательна")
        import json
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT config FROM business_rules WHERE id=%s",(rule_id,))
            existing=cursor.fetchone()
            if not existing: raise KeyError("Правило не найдено")
            config=dict(existing["config"] or {})
            history=list(config.get("lifecycle") or [])
            history.append({"active":active,"reason":reason.strip(),"user_id":user_id})
            config["lifecycle"]=history
            cursor.execute("""UPDATE business_rules SET active=%s,config=%s::jsonb,updated_at=now()
              WHERE id=%s RETURNING *""",(active,json.dumps(config,ensure_ascii=False),rule_id))
            return cursor.fetchone()

    def reparse_target(self, supplier_code: str, external_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT sp.id AS source_product_id,sp.source_url,sp.raw_data,sp.external_id,
              sp.sku,sp.name,sp.brand_raw,sp.description_text,s.id AS supplier_id,s.code AS supplier_code,
              s.name AS supplier_name,s.base_url,s.rate_settings,
              o.price,o.old_price,o.currency,o.availability_raw,o.availability_normalized,o.quantity
              FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
              LEFT JOIN LATERAL(SELECT * FROM offers WHERE source_product_id=sp.id AND offer_kind='default' ORDER BY id LIMIT 1)o ON true
              WHERE s.code=%s AND sp.external_id=%s""",(supplier_code,external_id))
            return cursor.fetchone()

    def save_reparse_preview(self, target: Mapping[str, Any], card, user_id: int) -> dict[str, Any]:
        current={key:target.get(key) for key in ("external_id","sku","name","brand_raw","price","old_price","currency","availability_raw","availability_normalized","quantity","description_text")}
        proposed=card.as_jsonable()
        diff=raw_normalized_diff(current,{"external_id":card.external_id,"sku":card.sku,"name":card.name,"brand_raw":card.brand,
             "price":proposed.get("price"),"old_price":proposed.get("old_price"),"currency":card.currency,
             "availability_raw":card.availability_raw,"availability_normalized":card.availability_normalized,
             "quantity":proposed.get("quantity"),"description_text":card.description_text,
             "properties":proposed.get("properties"),"media":proposed.get("media"),"relations":proposed.get("options")})
        import json
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO reparse_previews(source_product_id,requested_by,current_data,proposed_data,diff)
              VALUES(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb) RETURNING *""",
              (target["source_product_id"],user_id,json.dumps(current,ensure_ascii=False,default=str),
               json.dumps(proposed,ensure_ascii=False,default=str),json.dumps(diff,ensure_ascii=False,default=str)))
            return cursor.fetchone()

    def persist_reparse_preview(self, preview_id: int, user_id: int) -> dict[str, Any]:
        from universal_supplier.models import ProductCard
        from universal_supplier.postgres import PostgresRepository
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT rp.*,s.code AS supplier_code FROM reparse_previews rp
              JOIN source_products sp ON sp.id=rp.source_product_id JOIN suppliers s ON s.id=sp.supplier_id
              WHERE rp.id=%s FOR UPDATE""",(preview_id,))
            preview=cursor.fetchone()
            if not preview: raise KeyError("Предпросмотр не найден")
            if preview["persisted"]: raise ValueError("Предпросмотр уже сохранён")
        card=ProductCard.from_jsonable(preview["proposed_data"])
        repository=PostgresRepository(self.config)
        try:
            repository.ingest([card],datetime.now(timezone.utc).isoformat(),supplier_code=preview["supplier_code"])
        finally:
            repository.close()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE reparse_previews SET persisted=true WHERE id=%s RETURNING *",(preview_id,))
            return cursor.fetchone()

    def preview(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        # All classes are scanned in bounded chunks; only one requested page is retained.
        page=max(1,int(page)); page_size=max(1,min(500,int(page_size)))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT field_name,policy FROM field_update_policies")
            policies={row["field_name"]:row["policy"] for row in cursor.fetchall()}
            cursor.execute("SELECT * FROM business_rules WHERE active ORDER BY id")
            resolver=BusinessRuleResolver(cursor.fetchall())
            cursor.execute("""SELECT DISTINCT ON(supplier_id) supplier_id,critical
              FROM data_quality_snapshots ORDER BY supplier_id,created_at DESC,id DESC""")
            anomaly={row["supplier_id"]:bool(row["critical"]) for row in cursor.fetchall()}
            cursor.execute("""SELECT pid.source_product_id,pid.best_sterbrust_id,pid.decision,
              src.supplier_id,src.external_id,src.name,src.brand_raw,src.description_text,
              s.name AS supplier_name,coalesce(cat.category_path,'') AS category,
              o.price,o.availability_normalized,o.quantity,
              sb.name AS sterbrust_name,sb.brand_raw AS sterbrust_brand,sb.category_path AS sterbrust_category,
              sb.raw_data AS sterbrust_raw
              FROM product_identity_decisions pid JOIN source_products src ON src.id=pid.source_product_id
              JOIN suppliers s ON s.id=src.supplier_id
              LEFT JOIN sterbrust_products sb ON sb.sterbrust_product_id=pid.best_sterbrust_id
              LEFT JOIN LATERAL(SELECT price,availability_normalized,quantity FROM offers
                WHERE source_product_id=src.id AND active ORDER BY id LIMIT 1)o ON true
              LEFT JOIN LATERAL(SELECT string_agg(node.raw_name,' / ' ORDER BY node.position) AS category_path
                FROM product_category_paths path JOIN product_category_path_nodes node ON node.path_id=path.id
                WHERE path.source_product_id=src.id AND path.is_current)cat ON true
              ORDER BY s.name,src.id""")
            summary={"price_changes":0,"availability_changes":0,"quantity_changes":0,"no_changes":0,
                     "blocked_by_conflict":0,"needs_review":0,"new_products":0,"accessories":0,
                     "excluded_by_rule":0,"missing_supplier_data":0,"anomaly_guard_blocked":0}
            kept=[]; total=0; start=(page-1)*page_size; stop=start+page_size
            while True:
                batch=cursor.fetchmany(500)
                if not batch: break
                for raw in batch:
                    rules=resolver.resolve(supplier_id=raw["supplier_id"],source_product_id=raw["source_product_id"],external_id=str(raw["external_id"]),category=raw["category"])
                    sterbrust=raw.get("sterbrust_raw") or {}
                    current={"price":sterbrust.get("price"),"availability":sterbrust.get("availability"),
                             "quantity":sterbrust.get("quantity"),"description":sterbrust.get("description") or sterbrust.get("DETAIL_TEXT"),
                             "brand":raw.get("sterbrust_brand"),"category":raw.get("sterbrust_category")}
                    proposed={"price":raw.get("price"),"availability":raw.get("availability_normalized"),
                              "quantity":raw.get("quantity"),"description":raw.get("description_text"),
                              "brand":rules.brand_override or raw.get("brand_raw"),
                              "category":rules.category_override or raw.get("category")}
                    identity_blocked=raw["decision"] not in {"EXACT_EXISTING","HIGH_CONFIDENCE_EXISTING"}
                    fields=build_change_preview(current,proposed,policies,blocked=anomaly.get(raw["supplier_id"],False),
                                                exclusion=rules.excluded,identity_blocked=identity_blocked,
                                                forbidden_fields=rules.forbidden_fields)
                    if rules.force_review:
                        for field in fields:
                            if field["decision"] == "PROPOSE_UPDATE":
                                field.update(decision="NEEDS_REVIEW",reason="Бизнес-правило требует проверки")
                    row={**raw,"field_decisions":fields,"matched_rule_ids":rules.matched_rule_ids}
                    decisions={item["field"]:item["decision"] for item in fields}
                    summary["price_changes"] += decisions.get("price")=="PROPOSE_UPDATE"
                    summary["availability_changes"] += decisions.get("availability")=="PROPOSE_UPDATE"
                    summary["quantity_changes"] += decisions.get("quantity")=="PROPOSE_UPDATE"
                    summary["blocked_by_conflict"] += raw["decision"]=="CONFLICT"
                    summary["needs_review"] += rules.force_review or any(x=="NEEDS_REVIEW" for x in decisions.values())
                    summary["new_products"] += raw["decision"]=="SAFE_NEW_PRODUCT_CANDIDATE"
                    summary["accessories"] += raw["decision"]=="ACCESSORY_OR_COMPATIBILITY_ITEM"
                    summary["excluded_by_rule"] += rules.excluded
                    summary["missing_supplier_data"] += raw.get("price") is None or not raw.get("availability_normalized")
                    summary["anomaly_guard_blocked"] += anomaly.get(raw["supplier_id"],False)
                    summary["no_changes"] += not fields
                    if start <= total < stop: kept.append(row)
                    total+=1
            return {"rows":kept,"summary":summary,"total":total,"page":page,"page_size":page_size,
                    "pages":max(1,(total+page_size-1)//page_size)}
