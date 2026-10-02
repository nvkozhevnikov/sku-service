"""Evidence-bearing candidates for technical and editorial product content.

These values are deliberately independent from commercial offers and from
identity decisions.  A later resolver must select a value per field while
retaining alternatives and conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CandidateVerificationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED_SOURCE = "VERIFIED_SOURCE"
    CONFLICT = "CONFLICT"
    MISSING_AT_SOURCE = "MISSING_AT_SOURCE"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class ProductContentCandidate:
    source_url: str
    source_site: str
    model: str
    execution: str
    evidence_ref: str
    verification_status: CandidateVerificationStatus
    field: str
    raw_value: str
    language: str
    normalized_value: str | None = None
    conflict_group: str | None = None


@dataclass(frozen=True)
class ProductMediaCandidate:
    source_url: str
    source_site: str
    model: str
    execution: str
    evidence_ref: str
    verification_status: CandidateVerificationStatus
    media_url: str
    media_role: str
    original_width: int | None = None
    original_height: int | None = None
    visible_machine: bool | None = None
    watermark_present: bool | None = None
    duplicate_of_evidence_ref: str | None = None
    sharpness_assessment: str | None = None


@dataclass(frozen=True)
class ProductDocumentCandidate:
    source_url: str
    source_site: str
    model: str
    execution: str
    evidence_ref: str
    verification_status: CandidateVerificationStatus
    title: str
    document_type: str
    language: str
    applicability: str
    document_url: str | None = None
    issued_on: str | None = None
    valid_until: str | None = None
    confirmed_model_binding: bool = False


def candidate_is_ready_for_field_resolution(candidate: ProductContentCandidate) -> bool:
    """Only source-verified, non-conflicting values may enter a later resolver."""
    return candidate.verification_status is CandidateVerificationStatus.VERIFIED_SOURCE and not candidate.conflict_group
