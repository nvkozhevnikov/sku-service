"""Deterministic persistence semantics for Stage 3B callers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


UPDATE_LAST_VALIDATED = "UPDATE_LAST_VALIDATED"
INSERT_DECISION_EVENT = "INSERT_DECISION_EVENT"


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def decision_fingerprint(status: str, method: str, candidate_key: str | None, conflict_class: str = "") -> str:
    return _fingerprint({
        "candidate_key": candidate_key, "conflict_class": conflict_class,
        "method": method, "status": status,
    })


def warning_fingerprint(warnings: Mapping[str, Any]) -> str:
    return _fingerprint(warnings)


@dataclass(frozen=True)
class PersistedDecision:
    decision_fingerprint: str
    warning_fingerprint: str


def persistence_action(
    current: PersistedDecision | None,
    new: PersistedDecision,
    *,
    manual_mapping_changed: bool = False,
) -> str:
    """Return the only allowed write action; the caller performs it atomically."""
    if current is None or manual_mapping_changed:
        return INSERT_DECISION_EVENT
    if current.decision_fingerprint != new.decision_fingerprint:
        return INSERT_DECISION_EVENT
    if current.warning_fingerprint != new.warning_fingerprint:
        return INSERT_DECISION_EVENT
    return UPDATE_LAST_VALIDATED
