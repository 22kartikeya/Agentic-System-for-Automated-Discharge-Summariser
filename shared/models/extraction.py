"""Extraction result models (SSoT §5.2, §6.1 Table 3).

These are the canonical shapes the Clinical Extractor Agent must produce,
regardless of source language or file format (txt/json/ocr text).

Field-naming note (conflict §16 row 1): FA5 Table 3 uses `doctors`,
`adr_allergy_info`, `follow_up_appointments` (plural); `configs/rules.yaml`
uses `attending_physician` + `consulting_doctors`, `allergies`,
`follow_up_appointment` (singular). We store the rules.yaml names below
(more granular) — `doctors` = attending_physician + consulting_doctors,
`adr_allergy_info` = allergies, `follow_up_appointments` = follow_up_appointment.
Both FA5 names and rules.yaml names are therefore represented, just not as
duplicate fields.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PrescriptionItem(BaseModel):
    """One row of FA5 Table 3 'Prescription (per med)'."""

    sl_no: int | None = None
    medicine_name: str
    strength: str | None = None
    dosage: str | None = None
    frequency: str | None = None
    route: str | None = None
    period: str | None = None
    remarks: str | None = None
    total_quantity: str | None = None


class LabTestResult(BaseModel):
    """One row from a lab report's results table."""

    test_name: str
    result: str | None = None
    units: str | None = None
    reference_range: str | None = None
    flag: str | None = Field(default=None, description="e.g. NORMAL, HIGH, LOW")


class BillLineItem(BaseModel):
    """One row of a hospital bill's line items."""

    description: str
    item_code: str | None = None
    qty: float | None = None
    unit_price: float | None = None
    total: float | None = None


class DischargeExtraction(BaseModel):
    """Structured discharge report fields (FA5 Table 3 'Discharge Report')."""

    patient_id: str | None = None
    patient_name: str | None = None
    age: int | None = None
    gender: str | None = None
    address: str | None = None
    admission_date: str | None = None
    discharge_date: str | None = None
    ward: str | None = None
    bed_no: str | None = None
    attending_physician: str | None = None
    consulting_doctors: list[str] = Field(default_factory=list)
    discharge_diagnosis: list[str] = Field(default_factory=list)
    medications: list[PrescriptionItem] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    follow_up_appointment: str | None = None
    discharge_instructions: str | None = None
    discharge_approved: bool | None = None
    discharge_approved_by: str | None = None
    language: str = Field(default="en", description="Source document language code")


class LabExtraction(BaseModel):
    """Structured lab report fields (FA5 Table 3 'Lab Report')."""

    patient_id: str | None = None
    vendor_name: str | None = None
    lab_name: str | None = None
    report_date: str | None = None
    tests: list[LabTestResult] = Field(default_factory=list)
    language: str = Field(default="en", description="Source document language code")


class BillExtraction(BaseModel):
    """Structured bill fields (FA5 Table 3 'Bill')."""

    patient_id: str | None = None
    hospital_name: str | None = None
    billing_date: str | None = None
    line_items: list[BillLineItem] = Field(default_factory=list)
    total_amount: float | None = None
    payment_status: str | None = None
    language: str = Field(default="en", description="Source document language code")


class ExtractionResult(BaseModel):
    """Everything the Extractor produces for one patient case.

    One of discharge/lab/bill may be None when that document type was not
    found under data/input/ for this patient_id.
    """

    patient_id: str
    discharge: DischargeExtraction | None = None
    lab: LabExtraction | None = None
    bill: BillExtraction | None = None
    source_files: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
