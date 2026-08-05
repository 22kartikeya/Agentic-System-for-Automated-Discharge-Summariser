"""Extractor graph state (TypedDict) — SSoT §5.2.

Kept intentionally flat (company style: plain TypedDict, little abstraction).
"""

from __future__ import annotations

from typing import TypedDict


class ExtractorState(TypedDict):
    patient_id: str
    doc_types: list[str]  # which of discharge/lab/bill to look for
    harvested: dict[str, dict]  # doc_type -> Harvester tool result
    extraction: dict | None  # final ExtractionResult, as a plain dict
    errors: list[str]
