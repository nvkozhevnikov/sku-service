import re
import unicodedata
from decimal import Decimal, InvalidOperation


DEFAULT_BRAND_ALIASES = {
    "stalex": "stalex",
    "сталекс": "stalex",
    "jet tools": "jet",
    "jet": "jet",
    "proma": "proma",
    "proma cz": "proma",
}

# Visual Latin/Cyrillic confusables are canonicalized only in model context.
MODEL_CONFUSABLES = str.maketrans({
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h", "к": "k",
    "м": "m", "о": "o", "р": "p", "т": "t", "х": "x", "у": "y",
})

MODEL_EXECUTION_QUALIFIERS = frozenset({
    "new", "pro", "plus", "max", "mini", "eco", "premium", "classic",
    "digital", "cnc",
})

# A separated, compact execution suffix can be as identity-significant as a
# word qualifier (for example ``TU 2304 V`` versus ``TU2304``).  Restrict the
# generic form to one or two original uppercase Latin letters.  This preserves
# compact catalogue syntax without treating following prose as model identity.
MODEL_SUFFIX_STOP_TOKENS = frozenset({
    "cm", "db", "g", "hz", "kg", "kw", "m", "min", "mm", "nm", "rpm", "w",
})

ACCESSORY_HEAD_TOKENS = frozenset({
    "адаптер", "блок", "вставка", "губка", "губки", "держатель", "контроллер",
    "кронштейн", "кулачки", "кулачок", "ловитель", "люнет", "насадка", "опора",
    "оснастка", "патрон", "педаль", "переходник", "планшайба", "подставка",
    "приспособление", "пульт", "ручка", "стол", "цанга", "цанги",
})

# A supplier code is not a brand. Optimum is evidenced only by an explicit
# Optimum / OPTI* token in the sold-product name or by a filled source field.
_NAME_BRAND_OPTIMUM = re.compile(
    r"\boptimum\b|\bopti(?:turn|drill|mill|saw|grind|sand|press|polish)"
)


def brand_evidenced_by_name(name: object) -> str:
    if _NAME_BRAND_OPTIMUM.search(normalize_text(name)):
        return "Optimum"
    return ""


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower().replace("ё", "е")
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_brand(value: object, aliases: dict[str, str] | None = None) -> str:
    text = normalize_text(value)
    merged = {**DEFAULT_BRAND_ALIASES, **(aliases or {})}
    return merged.get(text, text)


def normalize_model_with_flags(value: object, brand: object = "") -> tuple[str, tuple[str, ...]]:
    """Normalize model context and return deterministic data-quality flags."""
    text = normalize_text(value)
    flags: list[str] = []
    has_latin = bool(re.search(r"[a-z]", text))
    has_cyrillic_confusable = any(ord(char) in MODEL_CONFUSABLES for char in text)
    if has_latin and has_cyrillic_confusable:
        flags.append("CYRILLIC_LATIN_MODEL_VARIATION")
    text = text.replace("×", "x").translate(MODEL_CONFUSABLES)
    compact = "".join(re.findall(r"[a-zа-я0-9]+", text))
    brand_text = normalize_text(brand).translate(MODEL_CONFUSABLES)
    brand_compact = "".join(re.findall(r"[a-zа-я0-9]+", brand_text))
    if brand_compact and compact.startswith(brand_compact):
        remainder = compact[len(brand_compact):]
        if remainder and re.search(r"\d", remainder):
            compact = remainder
            flags.append("MODEL_CONTAINS_BRAND_PREFIX")
    return compact, tuple(flags)


def normalize_model(value: object, brand: object = "") -> str:
    """Preserve significant tokens while applying model-only canonicalization."""
    return normalize_model_with_flags(value, brand)[0]


def normalize_decimal(value: object) -> Decimal | None:
    text = normalize_text(value).replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def name_tokens(value: object) -> tuple[str, ...]:
    text = normalize_text(value).replace("×", "x").replace("х", "x")
    return tuple(re.findall(r"[a-zа-я0-9]+", text))


def _raw_title_tokens(value: object) -> list[str]:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    return re.findall(r"(?iu)[a-zа-я0-9]+(?:[-./×xх][a-zа-я0-9]+)*", text)


def _model_suffix(raw_token: str) -> str:
    normalized = normalize_text(raw_token)
    if normalized in MODEL_EXECUTION_QUALIFIERS:
        return normalize_model(normalized)
    if (normalized not in MODEL_SUFFIX_STOP_TOKENS
            and re.fullmatch(r"[A-Z]{1,2}", raw_token)):
        return normalize_model(raw_token)
    return ""


def model_tokens(value: object) -> tuple[str, ...]:
    """Return model-like tokens from a title without fuzzy interpretation."""
    text = normalize_text(value)
    raw_tokens = _raw_title_tokens(value)
    tokens = [normalize_text(token) for token in raw_tokens]
    result = []
    for position, token in enumerate(tokens):
        compact = normalize_model(token)
        if re.search(r"[a-zа-я]", compact) and re.search(r"\d", compact):
            if position + 1 < len(tokens):
                compact += _model_suffix(raw_tokens[position + 1])
            result.append(compact)
    if not result:
        spaced = re.finditer(r"(?iu)\b([a-zа-я]{1,12})[ -]+(\d{2,})(?:[ -]+([a-zа-я]{1,12}))?\b", text)
        for match in spaced:
            suffix = _model_suffix(match.group(3) or "")
            result.append(normalize_model(match.group(1) + match.group(2) + suffix))
    return tuple(dict.fromkeys(result))


def normalized_name(value: object) -> str:
    return " ".join(name_tokens(value))


def extract_model(value: object) -> str:
    """Conservative model candidate: must contain a letter and a digit.

    Preserve compact catalogue prefixes written as a separate uppercase token,
    e.g. ``DH 35V`` -> ``dh35v`` and ``TH 4615V`` -> ``th4615v``.  Older
    logic picked only ``35V``/``4615V`` because those were the first individual
    tokens containing both a letter and a digit.
    """
    text = normalize_text(value)
    raw_tokens = _raw_title_tokens(value)
    tokens = [normalize_text(token) for token in raw_tokens]
    candidates = []
    for position, token in enumerate(tokens):
        compact = normalize_model(token)
        if re.search(r"[a-zа-я]", compact) and re.search(r"\d", compact) and len(compact) <= 32:
            if position + 1 < len(tokens):
                compact += _model_suffix(raw_tokens[position + 1])
            # A preceding 1-6 letter uppercase Latin token is usually a model
            # family prefix (DH 35V, TH 4615V, BF 20L).  Combine it before
            # accepting the standalone alphanumeric token.  Restricting this to
            # original uppercase Latin avoids joining ordinary Russian prose.
            if position > 0 and re.fullmatch(r"[A-Z]{1,6}", raw_tokens[position - 1] or ""):
                prefix = normalize_model(raw_tokens[position - 1])
                if prefix and prefix not in MODEL_SUFFIX_STOP_TOKENS:
                    compact = prefix + compact
                    position -= 1
            candidates.append((position, compact))
    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    spaced = re.search(r"(?iu)\b([a-zа-я]{1,12})[ -]+(\d{2,})(?:[ -]+([a-zа-я]{1,12}))?\b", text)
    if not spaced:
        return ""
    raw_spaced = re.search(r"(?iu)\b([a-zа-я]{1,12})[ -]+(\d{2,})(?:[ -]+([a-zа-я]{1,12}))?\b",
                           unicodedata.normalize("NFKC", str(value or "")))
    suffix = _model_suffix(raw_spaced.group(3) or "") if raw_spaced else ""
    return normalize_model(spaced.group(1) + spaced.group(2) + suffix)


def model_identity_evidence(value: object) -> dict[str, object]:
    """Explain the first title model, its execution and whether it is a reference."""
    text = normalize_text(value)
    tokens = re.findall(r"(?iu)[a-zа-я0-9]+(?:[-./×xх][a-zа-я0-9]+)*", text)
    # Preserve original capitalization for compact separated suffix detection.
    identity = extract_model(value)
    base = identity
    qualifier = ""
    for item in MODEL_EXECUTION_QUALIFIERS:
        normalized = normalize_model(item)
        if identity.endswith(normalized) and len(identity) > len(normalized):
            base, qualifier = identity[:-len(normalized)], normalized
            break
    model_position = None
    for position, token in enumerate(tokens):
        compact = normalize_model(token)
        if compact and identity.startswith(compact) and re.search(r"[a-zа-я]", compact) and re.search(r"\d", compact):
            model_position = position
            break
    if model_position is None and identity:
        for position in range(len(tokens) - 1):
            compact = normalize_model(tokens[position] + tokens[position + 1])
            if identity.startswith(compact):
                model_position = position
                break
    before = [normalize_text(token) for token in tokens[:model_position]] if model_position is not None else []
    accessory_positions = [i for i, token in enumerate(before) if token in ACCESSORY_HEAD_TOKENS]
    compatibility_positions = [i for i, token in enumerate(before) if token in {"для", "к", "под"}]
    reference = bool(accessory_positions and compatibility_positions
                     and min(accessory_positions) < max(compatibility_positions))
    return {
        "raw_title": str(value or ""), "model_identity": identity, "base_model": base,
        "execution_qualifier": qualifier,
        "model_ownership": "REFERENCE_COMPATIBILITY" if reference else "OWN_PRODUCT",
        "accessory_head": before[min(accessory_positions)] if accessory_positions else "",
        "compatibility_marker": before[max(compatibility_positions)] if compatibility_positions else "",
    }
