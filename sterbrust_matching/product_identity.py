"""Structured product semantics used by the Stage 6B identity gate.

The module deliberately separates taxonomy, model ownership and category
profiles.  Fuzzy text similarity is not identity evidence here.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from .normalization import (
    MODEL_EXECUTION_QUALIFIERS,
    extract_model,
    model_tokens,
    normalize_model,
    normalize_text,
)
from .property_normalization import (
    IDENTITY_CRITICAL,
    SUPPORTING,
    PropertyPolicy,
    compare_property,
)


OWN_MODEL = "OWN_MODEL"
REFERENCE_MODEL = "REFERENCE_MODEL"
UNKNOWN_MODEL_ROLE = "UNKNOWN_MODEL_ROLE"


@dataclass(frozen=True)
class Taxon:
    kind: str
    aliases: tuple[str, ...]
    family: str


# Ordered from specific components to broader equipment.  Each taxon remains
# independently extensible; this is intentionally not a monolithic if/else.
PRODUCT_TAXONOMY: tuple[Taxon, ...] = (
    Taxon("steady_rest", (r"\bлюнет\w*\b",), "accessory"),
    Taxon("foot_switch", (r"\bпедал\w*\b",), "accessory"),
    Taxon("parts_catcher", (r"\bловител\w*\b",), "accessory"),
    Taxon("faceplate", (r"\bпланшайб\w*\b",), "tooling"),
    # "кулачки" is the sold jaw set. "четырехкулачковый патрон" stays a chuck:
    # the adjective has no word boundary before "кулачк".
    Taxon("chuck_jaw", (r"\bкулачк\w*\b",), "tooling"),
    Taxon("jaw", (r"\bгубк\w*\b", r"\bjaws?\b"), "accessory"),
    Taxon("handle", (r"\bручк\w*\b", r"\bрукоят\w*\b", r"\bhandles?\b"), "accessory"),
    Taxon("controller", (r"\bконтроллер\w*\b", r"\bпульт\w*\b", r"\bблок\s+управлен\w*\b"), "accessory"),
    Taxon("collet", (r"\bцанг\w*\b", r"\bcollets?\b"), "tooling"),
    Taxon("chuck", (r"\bпатрон\w*\b", r"\bchucks?\b"), "tooling"),
    Taxon("disk", (r"\bдиск\w*\b", r"\bкруг\w*\b", r"\bcbn\b", r"\bsdc\b"), "consumable"),
    Taxon("table", (r"\bстол\w*\b", r"\btables?\b"), "accessory"),
    Taxon("stand", (r"\bподставк\w*\b", r"\bстойк\w*\b", r"\bосновани\w*\b", r"\bstands?\b"), "accessory"),
    Taxon("fixture", (r"\bприспособлен\w*\b", r"\bоснастк\w*\b", r"\bоправк\w*\b", r"\bдержател\w*\b", r"\bfixtures?\b"), "tooling"),
    Taxon("vise", (r"\bтиск\w*\b", r"\bvises?\b", r"\bvices?\b"), "equipment"),
    Taxon("manipulator", (r"\bманипулятор\w*\b", r"\bманипуляцион\w*\b"), "equipment"),
    Taxon("lathe", (r"\bтокарн\w*\b", r"\blathes?\b"), "machine"),
    Taxon("milling_machine", (r"\bфрезерн\w*\s+стан\w*\b", r"\bстан\w*\s+фрезерн\w*\b", r"\bmilling\s+machines?\b"), "machine"),
    Taxon("drill_press", (r"\bсверлил\w*\s+стан\w*\b", r"\bстан\w*\s+сверлил\w*\b", r"\bdrill\s+press\w*\b"), "machine"),
    Taxon("grinder", (r"\bзаточн\w*\s+стан\w*\b", r"\bстан\w*.*\bзаточ\w*\b", r"\bшлифовальн\w*\s+стан\w*\b", r"\bстан\w*\s+шлифовальн\w*\b", r"\bнаждак\w*\b", r"\bgrinders?\b"), "machine"),
    Taxon("beveling_machine", (r"\bфаскоснимател\w*\b", r"\bстан\w*.*сняти\w*\s+фаск\w*\b"), "machine"),
    Taxon("machine", (r"\bстан(?:ок|ки|ка|ком|ке)\b", r"\bmachines?\b"), "machine"),
    Taxon("accessory", (r"\bаксессуар\w*\b", r"\bкомплект\w*\b", r"\bпринадлежност\w*\b"), "accessory"),
    Taxon("tool", (r"\bинструмент\w*\b", r"\bфрез\w*\b", r"\bсверл\w*\b", r"\bрезц\w*\b"), "tooling"),
    Taxon("consumable", (r"\bрасходн\w*\b", r"\bабразив\w*\b"), "consumable"),
)

TAXON_BY_KIND = {taxon.kind: taxon for taxon in PRODUCT_TAXONOMY}
ACCESSORY_FAMILIES = {"accessory", "tooling", "consumable", "component"}

# Additional sold-object concepts do not reorder established taxonomy aliases.
# They resolve a machine-family claim only when the own sold noun proves that
# the machine token describes a host or application instead of the sold object.
SOLD_OBJECT_TAXONOMY = (
    Taxon("cutting_attachment", (), "component"),
    Taxon("reading_lens", (), "component"),
    Taxon("chuck_nut", (), "component"),
    Taxon("controller_interface", (), "component"),
    Taxon("brush_strip", (), "component"),
    Taxon("inductive_sensor", (), "component"),
    Taxon("power_feeder", (), "accessory"),
    Taxon("glue_supply_station", (), "accessory"),
    Taxon("knife", (), "tooling"),
    Taxon("saw_blade", (), "tooling"),
    Taxon("cutting_tool", (), "tooling"),
    Taxon("hand_edge_trimmer", (), "tooling"),
    Taxon("vacuum_pump", (), "equipment"),
    Taxon("chiller", (), "equipment"),
)
TAXON_BY_KIND.update({taxon.kind: taxon for taxon in SOLD_OBJECT_TAXONOMY})

COMPATIBILITY_MARKERS = (
    r"\bсовместим\w*\s+с\b", r"\bподходит\s+для\b", r"\bдля\s+модел\w*\b",
    r"\bдля\s+стан\w*\b", r"\bдля\s+тиск\w*\b", r"\bдля\s+манипулятор\w*\b",
    r"\bкомплект\s+для\b", r"\bоснастк\w*\s+для\b", r"\bгубк\w*\s+для\b",
    r"\bручк\w*\s+для\b", r"\bконтроллер\w*\s+для\b", r"\bдиск\w*\s+для\b",
    r"\bпатрон\w*\s+для\b", r"\bцанг\w*\s+для\b", r"\bдля\b", r"\bпод\b", r"\bк\b",
)


@dataclass(frozen=True)
class ProductKindEvidence:
    product_kind: str
    matched_alias: str
    source: str
    confidence: str


@dataclass(frozen=True)
class ModelRoleEvidence:
    role: str
    own_model: str
    reference_model: str
    reference_parent_kind: str
    compatibility_marker: str
    execution_markers: tuple[str, ...]
    all_models: tuple[str, ...]


def _first_taxon(text: str) -> tuple[Taxon | None, str]:
    for taxon in PRODUCT_TAXONOMY:
        for alias in taxon.aliases:
            match = re.search(alias, text, re.I)
            if match:
                # A woodworking adjective is not a sold table. Preserve the
                # original witness when an actual table/workbench corroborates it.
                if (taxon.kind == "table" and match.group(0).startswith("столяр")
                        and not re.search(r"\b(?:стол(?:а|у|ом|е|ы|ов|ам|ами|ах)?|верстак\w*)\b", text)):
                    continue
                return taxon, match.group(0)
    return None, ""


_MAINS_VOLTAGE = re.compile(r"(?<!\d)(220|230|380|400)\s*(?:v|в)\b", re.I)
_MM_SIZE = re.compile(r"\b(\d{2,4})\s*мм\b", re.I)
_EXECUTION_WORD = re.compile(r"\b(vario|premium|cnc|чпу)\b", re.I)
SIZE_CRITICAL_KINDS = frozenset({"faceplate", "chuck", "chuck_jaw", "disk", "steady_rest"})


def mains_voltage_band(text: object) -> str:
    """Map 220~230 and 380~400 only. Other pairs, including 220 vs 380, stay apart."""
    found: list[str] = []
    for raw in _MAINS_VOLTAGE.findall(normalize_text(text)):
        value = int(raw)
        band = "220" if value in {220, 230} else "380" if value in {380, 400} else str(value)
        if band not in found:
            found.append(band)
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        return "mixed"
    return ""


def primary_millimeters(text: object) -> str:
    found = list(dict.fromkeys(_MM_SIZE.findall(normalize_text(text))))
    return found[0] if len(found) == 1 else ""


def execution_words(text: object) -> frozenset[str]:
    words = set()
    for word in _EXECUTION_WORD.findall(normalize_text(text)):
        words.add("cnc" if word == "чпу" else word)
    return frozenset(words)


_OPTIMUM_TITLE_BRAND = re.compile(
    r"\boptimum\b|\bopti(?:turn|drill|mill|saw|grind|sand|press|polish)"
)
_TITLE_ARTICLE = re.compile(r"(?<!\d)(\d{5,10})(?!\d)")


def optimum_title_articles(name: object) -> frozenset[str]:
    """Article tokens printed on an Optimum card. Not a confirmation by themselves."""
    if not _OPTIMUM_TITLE_BRAND.search(normalize_text(name)):
        return frozenset()
    return frozenset(
        token for token in (normalize_model(raw) for raw in _TITLE_ARTICLE.findall(str(name or ""))) if token
    )


def execution_class_marks(text: object) -> frozenset[str]:
    """Precision and universal are different executions, not spelling variants."""
    normalized = normalize_text(text)
    marks: set[str] = set()
    if re.search(r"\bпрецизион\w*", normalized):
        marks.add("precision")
    if re.search(r"\bуниверсальн\w*", normalized):
        marks.add("universal")
    return frozenset(marks)


def _title_category_kind(name: object, category: object = "") -> ProductKindEvidence:
    title = normalize_text(name)
    category_text = normalize_text(category)
    relation = compatibility_relation(title)
    head = title[: relation[0]] if relation else title
    # Markers such as "цанга для" start at the product noun, so the head before
    # the marker is empty. The sold span includes that noun and stops before
    # the parent name.
    sold = title[: relation[1]] if relation else title
    # A title whose sold-product head is explicitly a machine must not become
    # a disk/tool merely because it describes what that machine processes.
    # Words after a compatibility marker are the parent, not the sold product.
    if re.search(r"\bстан(?:ок|ки|ка|ком|ке)\b", head):
        for preferred in ("lathe", "milling_machine", "drill_press", "grinder", "beveling_machine", "machine"):
            taxon = TAXON_BY_KIND[preferred]
            for alias in taxon.aliases:
                match = re.search(alias, title, re.I)
                if match:
                    return ProductKindEvidence(taxon.kind, match.group(0), "machine_name", "HIGH")
    taxon, alias = _first_taxon(head)
    if taxon:
        return ProductKindEvidence(taxon.kind, alias, "name_head", "HIGH")
    if relation:
        taxon, alias = _first_taxon(sold)
        if taxon:
            return ProductKindEvidence(taxon.kind, alias, "name_sold_span", "HIGH")
    else:
        taxon, alias = _first_taxon(title)
        if taxon:
            return ProductKindEvidence(taxon.kind, alias, "name", "HIGH")
    taxon, alias = _first_taxon(category_text)
    if taxon:
        return ProductKindEvidence(taxon.kind, alias, "category", "MEDIUM")
    return ProductKindEvidence("unknown", "", "none", "UNKNOWN")


def _complete_processing_machine_head(title: str, category: str) -> str:
    """Specific owned processing constructions, never a bare machine-like noun.

    A named execution corroborates these constructions; it is not source identity.
    An earlier part/compatibility head prevents a host from becoming the product.
    """
    rack_press = re.match(r"^ручн\w*\s+реечн\w*\s+пресс\b", title)
    if rack_press and re.search(r"(?:^|/)gibka(?:/|$)|\bгибочн\w*|\bпресс\w*", category):
        return rack_press.group(0)
    execution = re.search(r"\b(?:[a-zа-я][\w/-]*[ -]?\d[\w/-]*|\d+[a-zа-я][\w/-]*)\b", title)
    if not execution:
        return ""
    patterns = (
        r"\b\d+[- ]осев\w*\s+обрабатывающ\w*\s+центр\b",
        r"\b(?:сверлильно-присадочн\w*|фрезерн\w*\s+обрабатывающ\w*|форматно-раскроечн\w*|пильн\w*|торцев\w*\s+обрабатывающ\w*)\s+центр\b",
        r"\bсверлильно-долбежн\w*\b",
        r"\b(?:горяч\w*|холодн\w*|гидравлическ\w*|автоматическ\w*|ручн\w*\s+реечн\w*)\s+пресс\b",
        r"\bпресс\s+для\s+сращиван\w*\b",
        r"\bоптоволоконн\w*\s+установк\w*\s+лазерн\w*\s+раскроя\b",
        r"\bстреппинг\s+машина\b",
        r"\bгильотина\s+(?:для\s+резки\s+шпона|[a-zа-я]+[\w/-]*\d[\w/-]*)\b",
        r"\b(?:ленточн\w*|радиальн\w*|циркулярн\w*|торцовочн\w*(?:[- ]усорезн\w*)?)\s+пила\b",
    )
    for pattern in patterns:
        match = re.search(pattern, title)
        if not match:
            continue
        prefix = title[:match.start()].strip()
        # Permit a leading execution/brand/adjective prefix, but never a sold
        # component or a relation that introduces somebody else's machine.
        if prefix and (compatibility_relation(prefix) or _first_taxon(prefix)[0]):
            continue
        if prefix and not (re.match(r"(?=[\w/-]*\d)[a-zа-я][\w/-]*\b", prefix)
                               or re.fullmatch(r"(?:\d+[- ]осев\w*|вертикальн\w*|горизонтальн\w*|настольн\w*)", prefix)):
            continue
        return match.group(0)
    # A disc blade's dimensional name is not a metal-cutting saw execution.
    # The complete-saw category corroborates the owned, named machine head.
    if (re.search(r"(?:^|/)sagen(?:/|$)|\bдисков\w*\s+пил\w*\s+стан\w*", category)
            and re.match(r"^дисков(?:ая|ые)\s+пил(?:а|ы)\s+(?:[a-zа-я]+\s+)?[a-zа-я]+[ -]?\d", title)):
        return re.match(r"^дисков(?:ая|ые)\s+пил(?:а|ы)\b", title).group(0)
    return ""


def _disk_sold_object_evidence(prior: ProductKindEvidence, name: object) -> ProductKindEvidence:
    """A disk alias must describe the sold object, not shape or its host.

    Preserve established disk/blade, CBN and SDC readings. Only an existing
    disk claim is disambiguated; unrelated taxonomy priorities stay intact.
    """
    if prior.product_kind != "disk":
        return prior
    title = normalize_text(name)
    if re.match(r"^(?:рекомендуем\w*\s+)?(?:режим\w*\s+(?:резан\w*|обработ\w*)|рекомендаци\w*\s+по\s+(?:резан\w*|обработ\w*))\b", title):
        return ProductKindEvidence("unknown", "", "none", "UNKNOWN")
    # Genuine circle nouns and their inflections retain their exact evidence.
    # Productive compounds/adjectives (круглый, круглопильный...) do not.
    alias = normalize_text(prior.matched_alias)
    if not alias.startswith("круг") or re.fullmatch(r"круг(?:а|у|ом|е|и|ов|ам|ами|ах)?", alias):
        return prior
    relation = compatibility_relation(title)
    head = title[:relation[0]] if relation else title
    for kind, pattern in (
        ("spare", r"^(?:тэн\b|нагревательн\w*\s+элемент\w*\b)"),
        ("spare", r"^(?:подающ\w*\s+)?(?:колесо|валец)\b"),
        ("spare", r"^втулк\w*\b"),
        ("accessory", r"^лазерн\w*\s+указател\w*\b"),
        ("knife", r"^нож(?:и|а|ей)?\b"),
    ):
        match = re.search(pattern, head)
        if match:
            return ProductKindEvidence(kind, match.group(0), "sold_object_head", "HIGH")
    # Removing a misleading category alias must not expose the host's machine
    # type as a new positive claim. Unproven sold-object specificity stays open.
    return ProductKindEvidence("unknown", "", "none", "UNKNOWN")


def _owned_functional_kind(prior: ProductKindEvidence, name: object,
                           category: object) -> ProductKindEvidence:
    """Resolve strong own functional heads without a machine-family prior."""
    if prior.source not in {"none", "category"}:
        return prior
    if TAXON_BY_KIND.get(prior.product_kind, Taxon("", (), "")).family == "machine":
        return prior
    title = normalize_text(name)
    relation = compatibility_relation(title)
    head = title[:relation[0]] if relation else title
    for kind, pattern in (("power_feeder", r"\bавтоподатчик(?:и)?\b"),
                          ("chiller", r"\bчиллер(?:ы)?\b")):
        # This chiller repair resolves the contradictory mixed-category claim;
        # it does not promote unrelated unknown/operator-review coverage.
        if kind == "chiller" and prior.product_kind != "consumable":
            continue
        match = re.search(pattern, head)
        if not match:
            continue
        prefix = head[:match.start()]
        prefix = re.sub(r"\b\d+\s*[-xх]?\s*(?:х|ми)?\s*(?:ролик\w*|скоростн\w*)\b", " ", prefix)
        prefix = re.sub(r"\b(?:универсальн\w*|роликов\w*|мод(?:ель)?|[a-zа-я][\w/-]*\d[\w/-]*|[a-z]+)\b", " ", prefix)
        if not re.sub(r"[\s().,:-]+", "", prefix):
            return ProductKindEvidence(kind, match.group(0), "sold_object_head", "HIGH")
    # Do not extend these weaker category repairs to stronger owned kinds.
    if prior.product_kind == "unknown" and re.search(r"\bстоляр\w*\b", normalize_text(category)):
        for kind, pattern in (
            ("knife", r"^(?:сменн\w*\s+)?(?P<own>нож(?:и|а|ей)?)(?:\s|$)"),
            ("fixture", r"^(?:универсальн\w*\s+)?(?:эксцентриков\w*\s+)?(?P<own>прижим\w*)(?:\s|$)"),
        ):
            match = re.search(pattern, head)
            if match:
                return ProductKindEvidence(kind, match.group("own"), "sold_object_head", "HIGH")
        guide = re.match(r"^(?P<own>направляющ\w*)(?:\s|$)", head)
        if (guide and re.search(r"\bдля\s+пил\w*\b", title)
                and re.search(r"\bшариков\w*\s+подшипник\w*\b", title)):
            return ProductKindEvidence("spare", guide.group("own"), "sold_object_head", "HIGH")
    return prior


def classify_product_kind(name: object, category: object = "", *,
                          own_description: object = "") -> ProductKindEvidence:
    """Resolve the sold object without treating host-machine wording as identity.

    Optional own description corroborates ambiguous head nouns; its absence is
    never replaced with model, price, weight or neighboring-product inference.
    Existing non-machine specificity and full-machine head precedence survive.
    """
    prior = _disk_sold_object_evidence(_title_category_kind(name, category), name)
    prior = _owned_functional_kind(prior, name, category)
    if TAXON_BY_KIND.get(prior.product_kind, Taxon("", (), "")).family != "machine":
        complete = _complete_processing_machine_head(normalize_text(name), normalize_text(category))
        if complete:
            return ProductKindEvidence("machine", complete, "owned_complete_processing_head", "HIGH")
        return prior
    title = normalize_text(name)
    description = normalize_text(own_description)
    machine = re.search(r"\bстан(?:ок|ки|ка|ком|ке)\b", title)

    def sold(pattern: str):
        match = re.search(pattern, title)
        # A machine mentioned before the component processes/contains it; do
        # not downgrade a complete machine merely for mentioning its tooling.
        return match if match and (not machine or match.start() < machine.start()) else None

    rules = (
        ("fixture", r"\b(?:пневмобалк\w*|моноприжим\w*|упор\s+флажков\w*|электромагнитн\w*\s+плит\w*|линейк\w*)\b"),
        ("reading_lens", r"\bлинз\w*\b"),
        ("chuck_nut", r"\bгайк\w*\b(?=.*\bпатрон\w*)"),
        ("controller_interface", r"\bинтерфейсн\w*\s+(?:nc|нс)[ -]?карт\w*\b"),
        ("brush_strip", r"\bбахром\w*\b"),
        ("inductive_sensor", r"\bиндуктивн\w*\s+датчик\w*\b"),
        ("power_feeder", r"\bавтоподатчик\w*\b"),
        ("glue_supply_station", r"\bpur\s+станци\w*\b"),
        ("knife", r"\bнож\b"),
        ("saw_blade", r"\bпила\s+узла\b"),
        ("cutting_tool", r"\bрез(?:ец|цы|цов|цами|цах|цу|цом)\b"),
        ("chiller", r"\bчиллер\w*\b"),
    )
    for kind, pattern in rules:
        match = sold(pattern)
        if match:
            return ProductKindEvidence(kind, match.group(0), "sold_object_head", "HIGH")
    pump = sold(r"\bнасос\w*\b")
    if pump and re.search(r"\bвакуум\w*|\b(?:роторно-пластинчат|пластинчато-ротор)\w*", title + " " + description):
        return ProductKindEvidence("vacuum_pump", pump.group(0), "sold_object_head", "HIGH")
    guillotine = sold(r"\bгильотин\w*\b")
    if guillotine and re.search(r"\bна\s+\w*кромкооблицовочн\w*\s+стан\w*", title):
        return ProductKindEvidence("cutting_attachment", guillotine.group(0), "sold_object_host_relation", "HIGH")
    trimmer = sold(r"\bподрезател\w*\s+кромк\w*\b")
    manual = re.search(r"\bручн\w*\s+приспособлен\w*", description)
    snipping_body = re.search(r"\bперекусить\b", description) and re.search(r"\bпластик\w*\b", description)
    if trimmer and (manual or snipping_body):
        return ProductKindEvidence("hand_edge_trimmer", trimmer.group(0), "sold_object_own_description", "HIGH")
    return prior


def _marker_span(text: object) -> tuple[int, int, str] | None:
    raw = "" if text is None else str(text)
    matches = []
    for marker in COMPATIBILITY_MARKERS:
        match = re.search(marker, raw, re.I)
        if match:
            matches.append((match.start(), match.end(), match.group(0)))
    return min(matches, default=None, key=lambda item: item[0])


def compatibility_relation(text: object) -> tuple[int, int, str] | None:
    return _marker_span(normalize_text(text))


def _is_measurement_or_thread_range(model: str) -> bool:
    return bool(re.fullmatch(r"m\d+m\d+", model) or re.fullmatch(r"\d+(?:mm|cm|kw|w|v|rpm)", model))


def _execution_markers(model: str) -> tuple[str, ...]:
    return tuple(sorted(q for q in MODEL_EXECUTION_QUALIFIERS if model.endswith(normalize_model(q))))


def classify_model_role(name: object, category: object = "", explicit_model: object = "") -> ModelRoleEvidence:
    text = normalize_text(name)
    kind = classify_product_kind(name, category)
    relation = compatibility_relation(text)
    models = tuple(model for model in model_tokens(name) if not _is_measurement_or_thread_range(model))
    explicit = normalize_model(explicit_model)
    inferred = normalize_model(extract_model(name))
    if inferred and not _is_measurement_or_thread_range(inferred) and inferred not in models:
        models = (inferred,) + models
    if explicit and not _is_measurement_or_thread_range(explicit) and explicit not in models:
        models = (explicit,) + models

    if relation:
        tail = text[relation[1]:]
        raw_relation = _marker_span(name)
        raw_tail = str(name or "")[raw_relation[1]:] if raw_relation else tail
        tail_models = tuple(model for model in model_tokens(raw_tail) if not _is_measurement_or_thread_range(model))
        extracted_tail = normalize_model(extract_model(raw_tail))
        if (extracted_tail and not _is_measurement_or_thread_range(extracted_tail)
                and extracted_tail not in tail_models):
            tail_models = tail_models + (extracted_tail,)
        parent_kind = classify_product_kind(raw_tail, "").product_kind
        family = TAXON_BY_KIND.get(kind.product_kind, Taxon("unknown", (), "unknown")).family
        # A bare functional phrase such as "manipulator for threading" is not
        # compatibility unless it names a parent kind or the product itself is
        # an accessory/tooling/consumable.
        parent_family = TAXON_BY_KIND.get(parent_kind, Taxon("unknown", (), "unknown")).family
        parent_is_other_equipment = (
            parent_family in {"equipment", "machine"} and parent_kind != kind.product_kind
        )
        is_reference = bool(
            (family in ACCESSORY_FAMILIES and (tail_models or parent_is_other_equipment))
            or (tail_models and parent_is_other_equipment)
        )
        if is_reference:
            reference = tail_models[-1] if tail_models else ""
            blocked = set(tail_models)
            if reference:
                blocked.add(reference)
            noun = normalize_model(kind.matched_alias)
            own_candidates = tuple(
                model for model in models
                if model not in blocked and not (
                    noun and model.startswith(noun) and model[len(noun):].isdigit()
                )
            )
            own = own_candidates[0] if own_candidates else ""
            return ModelRoleEvidence(
                REFERENCE_MODEL, own, reference, parent_kind, relation[2],
                _execution_markers(reference), models,
            )

    own = explicit or inferred or (models[0] if models else "")
    if own:
        return ModelRoleEvidence(OWN_MODEL, own, "", "", "", _execution_markers(own), models)
    return ModelRoleEvidence(UNKNOWN_MODEL_ROLE, "", "", "", "", (), models)


def kinds_equivalent(left: str, right: str) -> bool:
    return bool(left and right and left != "unknown" and left == right)


PROPERTY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("max_turning_diameter", (r"макс\w*.*диаметр.*(?:обработ|точен|над стан)", r"diameter over")),
    ("distance_between_centers", (r"расстояни\w*.*между центр", r"distance between centers")),
    ("spindle_bore", (r"отверсти\w*.*шпиндел", r"spindle bore")),
    ("spindle_taper", (r"конус\w*.*шпиндел", r"spindle taper")),
    ("motor_power", (r"мощност\w*.*(?:двигател|электродвигател)", r"motor power")),
    ("voltage", (r"напряжени\w*", r"voltage")),
    ("machine_mode", (r"(?:тип|управлен).*чпу", r"cnc|manual")),
    ("table_size", (r"размер\w*.*стол", r"table size")),
    ("travel_x", (r"(?:ход|перемещени).*\bx\b",)),
    ("travel_y", (r"(?:ход|перемещени).*\by\b",)),
    ("travel_z", (r"(?:ход|перемещени).*\bz\b",)),
    ("spindle_speed", (r"частот\w*.*вращени", r"число оборот", r"spindle speed")),
    ("max_drilling_diameter", (r"макс\w*.*диаметр.*сверл", r"drilling diameter")),
    ("spindle_travel", (r"ход\w*.*шпиндел", r"spindle travel")),
    ("jaw_width", (r"ширин\w*.*губ", r"jaw width")),
    ("clamping_range", (r"(?:раствор|раскрыти|диапазон зажим)", r"clamping range")),
    ("vise_type", (r"тип\w*.*тиск", r"vise type")),
    ("thread_range", (r"диапазон\w*.*резьб", r"thread range")),
    ("drive_type", (r"электрическ|пневматическ", r"drive type")),
    ("working_radius", (r"рабоч\w*.*радиус", r"working radius")),
    ("tool_range", (r"диапазон\w*.*(?:заточ|обработ|инструмент)", r"tool range")),
    ("wheel_size", (r"размер\w*.*(?:круг|диск)", r"wheel size")),
    ("physical_size", (r"размер\w*", r"диаметр\w*", r"ширин\w*", r"длин\w*")),
)

PROFILE_PROPERTIES: dict[str, tuple[str, ...]] = {
    "lathe": ("max_turning_diameter", "distance_between_centers", "spindle_bore", "spindle_taper", "motor_power", "voltage", "machine_mode"),
    "milling_machine": ("table_size", "travel_x", "travel_y", "travel_z", "spindle_taper", "spindle_speed", "motor_power", "machine_mode"),
    "drill_press": ("max_drilling_diameter", "spindle_taper", "spindle_travel", "motor_power", "voltage"),
    "vise": ("jaw_width", "clamping_range", "vise_type"),
    "manipulator": ("thread_range", "drive_type", "voltage", "working_radius"),
    "grinder": ("tool_range", "wheel_size", "motor_power", "voltage"),
}

PROPERTY_POLICIES: dict[str, PropertyPolicy] = {
    "max_turning_diameter": PropertyPolicy("diameter", IDENTITY_CRITICAL),
    "distance_between_centers": PropertyPolicy("length", IDENTITY_CRITICAL),
    "spindle_bore": PropertyPolicy("diameter", IDENTITY_CRITICAL),
    "spindle_taper": PropertyPolicy("string", IDENTITY_CRITICAL),
    "motor_power": PropertyPolicy("power", IDENTITY_CRITICAL),
    # 220 V and 230 V are the same nominal low-voltage mains execution in
    # supplier catalogues; larger differences remain identity conflicts.
    "voltage": PropertyPolicy("voltage", IDENTITY_CRITICAL, absolute_tolerance=10),
    "machine_mode": PropertyPolicy("string", IDENTITY_CRITICAL),
    "table_size": PropertyPolicy("dimension", IDENTITY_CRITICAL),
    "travel_x": PropertyPolicy("length", IDENTITY_CRITICAL),
    "travel_y": PropertyPolicy("length", IDENTITY_CRITICAL),
    "travel_z": PropertyPolicy("length", IDENTITY_CRITICAL),
    "spindle_speed": PropertyPolicy("rpm", SUPPORTING),
    "max_drilling_diameter": PropertyPolicy("diameter", IDENTITY_CRITICAL),
    "spindle_travel": PropertyPolicy("length", IDENTITY_CRITICAL),
    "jaw_width": PropertyPolicy("length", IDENTITY_CRITICAL),
    "clamping_range": PropertyPolicy("length", IDENTITY_CRITICAL),
    "vise_type": PropertyPolicy("string", IDENTITY_CRITICAL),
    "thread_range": PropertyPolicy("thread_range", IDENTITY_CRITICAL),
    "drive_type": PropertyPolicy("string", IDENTITY_CRITICAL),
    "working_radius": PropertyPolicy("length", IDENTITY_CRITICAL),
    "tool_range": PropertyPolicy("range", IDENTITY_CRITICAL),
    "wheel_size": PropertyPolicy("dimension", IDENTITY_CRITICAL),
    "physical_size": PropertyPolicy("dimension", SUPPORTING),
}


def canonical_property_name(raw_name: object) -> str:
    text = normalize_text(raw_name)
    for canonical, aliases in PROPERTY_ALIASES:
        if any(re.search(alias, text, re.I) for alias in aliases):
            return canonical
    return ""


def canonical_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_name, value in properties.items():
        canonical = canonical_property_name(raw_name)
        if canonical and value not in (None, "") and canonical not in result:
            rendered = str(value)
            raw_text = normalize_text(raw_name)
            if canonical == "voltage" and not re.search(r"(?:^|\s)(?:v|в)(?:\s|$)", normalize_text(rendered)):
                rendered += " V"
            elif canonical == "motor_power" and not re.search(r"(?:^|\s)(?:w|kw|вт|квт)(?:\s|$)", normalize_text(rendered)):
                rendered += " kW" if "квт" in raw_text else " W"
            result[canonical] = rendered
    return result


def profile_for_kind(product_kind: str) -> tuple[str, ...]:
    if product_kind in PROFILE_PROPERTIES:
        return PROFILE_PROPERTIES[product_kind]
    family = TAXON_BY_KIND.get(product_kind, Taxon("unknown", (), "unknown")).family
    return ("physical_size",) if family in ACCESSORY_FAMILIES else ()


def compare_category_identity(
    source_kind: str,
    source_properties: Mapping[str, Any],
    candidate_properties: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = canonical_properties(source_properties)
    candidate = canonical_properties(candidate_properties)
    comparisons: dict[str, Any] = {}
    conflicts: dict[str, Any] = {}
    for key in profile_for_kind(source_kind):
        if key not in source or key not in candidate:
            continue
        comparison = compare_property(source[key], candidate[key], key, PROPERTY_POLICIES)
        comparisons[key] = comparison.evidence()
        if not comparison.compatible:
            conflicts[f"category_property:{key}"] = comparison.evidence()
    return conflicts, comparisons


def safe_new_product_decision(
    *, existing_confirmed: bool, exact_article_match: bool,
    compatible_brand_model_candidates: Iterable[str], normalized_model_candidates: Iterable[str],
    semantic_candidates: Iterable[str], model_role: str, product_kind: str,
) -> tuple[str, str]:
    """Conservative Stage 6B creation-candidate classification."""
    if existing_confirmed or exact_article_match:
        return "REVIEW_EXISTING", "existing mapping or exact article candidate exists"
    plausible = tuple(dict.fromkeys((*compatible_brand_model_candidates, *normalized_model_candidates, *semantic_candidates)))
    if plausible:
        return "REVIEW_EXISTING", "plausible existing candidate requires identity proof"
    if model_role == REFERENCE_MODEL or TAXON_BY_KIND.get(product_kind, Taxon("", (), "")).family in ACCESSORY_FAMILIES:
        return "ACCESSORY_OR_COMPATIBILITY_ITEM", "accessory or parent-reference semantics"
    if product_kind == "unknown" or model_role == UNKNOWN_MODEL_ROLE:
        return "INSUFFICIENT_IDENTITY", "product kind or own model is not proven"
    return "SAFE_NEW_PRODUCT_CANDIDATE", "no existing, article, model, semantic, or compatibility candidate"
