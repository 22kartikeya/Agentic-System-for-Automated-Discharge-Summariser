"""Direct field-mapping for already-structured JSON intake files.

Why this exists (found while testing live against Bedrock Nova Lite,
SSoT §5.2 "structured and unstructured data"): when a source file is already
valid JSON, sending it through the LLM's structured-output tool-calling adds
no value and Nova Lite sometimes returns empty {} rows for nested lists
(e.g. bill line_items). Direct mapping is faster, free, and deterministic —
so JSON sources skip the LLM entirely here; the LLM path in nodes.py is used
only for genuinely unstructured text (txt / OCR sidecars).

Key names vary across samples (English vs Hindi vs a few schema variants),
so lookups try several known aliases before falling back to positional
mapping for foreign-language-keyed row dicts (e.g. Hindi lab_results).
"""

from __future__ import annotations

from typing import Any

from shared.models.extraction import (
    BillExtraction,
    BillLineItem,
    DischargeExtraction,
    LabExtraction,
    LabTestResult,
    PrescriptionItem,
)

_LAB_ROW_KEYS = ("test_name", "result", "units", "reference_range", "flag")


def _first(data: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "true", "ja", "हाँ", "y"}
    return bool(value)


def map_discharge_json(data: dict) -> DischargeExtraction:
    medications = []
    for item in data.get("medications") or []:
        if not isinstance(item, dict):
            continue
        medications.append(
            PrescriptionItem(
                sl_no=_to_int(_first(item, "sl_no")),
                medicine_name=_first(item, "medicine_name", "name", default="unknown"),
                strength=_first(item, "strength"),
                dosage=_first(item, "dosage"),
                frequency=_first(item, "frequency"),
                route=_first(item, "route"),
                period=_first(item, "period"),
                remarks=_first(item, "remarks"),
                total_quantity=_to_str(_first(item, "total_quantity")),
            )
        )

    return DischargeExtraction(
        patient_id=_first(data, "patient_id"),
        patient_name=_first(data, "patient_name"),
        age=_to_int(_first(data, "age")),
        gender=_first(data, "gender", "sex"),
        address=_first(data, "address"),
        admission_date=_first(data, "admission_date"),
        discharge_date=_first(data, "discharge_date"),
        ward=_first(data, "ward"),
        bed_no=_to_str(_first(data, "bed_no")),
        attending_physician=_first(data, "attending_physician"),
        consulting_doctors=[str(d) for d in _as_list(data.get("consulting_doctors"))],
        discharge_diagnosis=[str(d) for d in _as_list(data.get("discharge_diagnosis"))],
        medications=medications,
        allergies=[str(a) for a in _as_list(data.get("allergies"))],
        follow_up_appointment=_first(data, "follow_up_appointment"),
        discharge_instructions=_first(data, "discharge_instructions"),
        discharge_approved=_to_bool(_first(data, "discharge_approved", "discharge_ok")),
        discharge_approved_by=_first(data, "discharge_approved_by"),
        language=_first(data, "language", default="en"),
    )


def _map_lab_test_row(item: dict) -> LabTestResult:
    if _first(item, "test_name", "test") is not None:
        return LabTestResult(
            test_name=_first(item, "test_name", "test"),
            result=_to_str(_first(item, "result")),
            units=_first(item, "units", "unit"),
            reference_range=_first(item, "reference_range", "ref_range"),
            flag=_first(item, "flag", "indicator"),
        )
    # Foreign-language column names (e.g. Hindi) — the sample corpus keeps a
    # fixed column order, so fall back to positional mapping.
    values = list(item.values())
    padded = (values + [None] * len(_LAB_ROW_KEYS))[: len(_LAB_ROW_KEYS)]
    row = dict(zip(_LAB_ROW_KEYS, padded))
    row["test_name"] = row["test_name"] or "unknown"
    row["result"] = _to_str(row["result"])
    return LabTestResult(**row)


def map_lab_json(data: dict) -> LabExtraction:
    rows = data.get("lab_results") or data.get("tests") or []
    tests = [_map_lab_test_row(item) for item in rows if isinstance(item, dict)]
    return LabExtraction(
        patient_id=_first(data, "patient_id"),
        vendor_name=_first(data, "vendor_name", "performing_lab"),
        lab_name=_first(data, "lab_name", "performing_lab"),
        report_date=_first(data, "report_date", "reported"),
        tests=tests,
        language=_first(data, "language", default="en"),
    )


def map_bill_json(data: dict) -> BillExtraction:
    line_items = []
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        line_items.append(
            BillLineItem(
                description=_first(item, "description", default="item"),
                item_code=_first(item, "item_code"),
                qty=_to_float(_first(item, "qty")),
                unit_price=_to_float(_first(item, "unit_price")),
                total=_to_float(_first(item, "total")),
            )
        )

    return BillExtraction(
        patient_id=_first(data, "patient_id"),
        hospital_name=_first(data, "hospital_name", "vendor_name"),
        billing_date=_first(data, "billing_date", "issue_date"),
        line_items=line_items,
        total_amount=_to_float(_first(data, "total_amount")),
        payment_status=_first(data, "payment_status"),
        language=_first(data, "language", default="en"),
    )


MAPPERS = {
    "discharge": map_discharge_json,
    "lab": map_lab_json,
    "bill": map_bill_json,
}
