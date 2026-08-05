"""Extractor graph nodes — Tools + Resources + Prompts (NO Sampling, SSoT §5.2).

Two nodes:
1. harvest_node  — Clinical Data Harvester MCP tool for each doc_type.
2. extract_node  — reads MCP Resources, fetches discharge-extraction-prompt,
   then uses the LLM (Bedrock) to fill structured fields for every doc.

Simple rule: if there is text, use the LLM. No separate JSON-mapper path.

Works for ANY patient_id that has files under data/input/.
"""

from __future__ import annotations

import json
import re

from fastmcp import Client
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from agents.extractor.state import ExtractorState
from shared.logger import get_logger
from shared.models.extraction import (
    BillExtraction,
    DischargeExtraction,
    ExtractionResult,
    LabExtraction,
    PrescriptionItem,
    fill_fa5_and_rules_aliases,
)
from shared.settings import get_bedrock_config, get_service

logger = get_logger("extractor")

_SCHEMA_BY_DOC_TYPE = {
    "discharge": DischargeExtraction,
    "lab": LabExtraction,
    "bill": BillExtraction,
}

# SSoT §3.3 Resources used by Extractor (Templates — patient_id filled at runtime)
_RESOURCE_BY_DOC_TYPE = {
    "discharge": "resource://discharge-report/{patient_id}",
    "lab": "resource://lab-report/{patient_id}",
    # bills: no MCP Resource URI in SSoT §3.3 — Harvester only
}


def _primary_mcp_url() -> str:
    """Build Primary MCP streamable-HTTP URL from agent_config.yaml."""
    svc = get_service("primary_mcp")
    host = svc.get("host", "127.0.0.1")
    port = int(svc.get("port", 8200))
    path = svc.get("transport_path", "/clinicaltools")
    return f"http://{host}:{port}{path}"


def _merge_bill_from_structured(
    bill: BillExtraction, structured: dict | None
) -> BillExtraction:
    """Fill bill gaps the LLM dropped using harvest structured_data (same JSON source).

    Nova Lite often returns empty {} rows for nested line_items; coerce drops them
    and completeness then flags line_items missing. Prefer the already-parsed JSON
    when those fields are empty — not a second extraction path.
    """
    if not isinstance(structured, dict):
        return bill
    data = bill.model_dump()
    if not data.get("patient_id"):
        data["patient_id"] = structured.get("patient_id")
    if not data.get("hospital_name"):
        data["hospital_name"] = (
            structured.get("hospital_name")
            or structured.get("vendor_name")
            or structured.get("facility_name")
        )
    if not data.get("billing_date"):
        data["billing_date"] = (
            structured.get("billing_date")
            or structured.get("issue_date")
            or structured.get("bill_date")
        )
    if data.get("total_amount") is None:
        data["total_amount"] = structured.get("total_amount")
    if not data.get("payment_status"):
        data["payment_status"] = structured.get("payment_status")
    if not data.get("line_items"):
        rows: list[dict] = []
        for item in structured.get("line_items") or []:
            if not isinstance(item, dict):
                continue
            desc = item.get("description") or item.get("item") or item.get("name")
            if not desc:
                continue
            rows.append(
                {
                    "description": str(desc),
                    "item_code": item.get("item_code"),
                    "qty": item.get("qty"),
                    "unit_price": item.get("unit_price"),
                    "total": item.get("total"),
                }
            )
        if rows:
            data["line_items"] = rows
    try:
        return BillExtraction.model_validate(data)
    except ValidationError:
        logger.warning("Bill structured merge failed validation — keeping LLM result")
        return bill



def _tool_result_to_dict(result: object) -> dict:
    """The Harvester tool returns one JSON string block; parse it back to a dict."""
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw_text": text}
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    return {"error": f"unrecognized tool result: {result!r}"}


def _prompt_text(get_prompt_result) -> str:
    """Flatten a GetPromptResult's messages into one instruction string."""
    parts = []
    for message in get_prompt_result.messages:
        text = getattr(message.content, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _resource_text(read_result) -> str:
    """Flatten read_resource contents into one string."""
    # FastMCP Client may return a list of content blocks, or an object with .contents
    blocks = read_result
    if hasattr(read_result, "contents"):
        blocks = read_result.contents
    parts = []
    for block in blocks or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        elif isinstance(block, dict) and "text" in block:
            parts.append(str(block["text"]))
    return "\n".join(parts)


async def harvest_node(state: ExtractorState) -> dict:
    """Call the Clinical Data Harvester tool for every requested doc_type."""
    url = _primary_mcp_url()
    harvested: dict[str, dict] = {}
    errors: list[str] = list(state.get("errors", []))

    async with Client(url) as client:
        for doc_type in state["doc_types"]:
            result = await client.call_tool(
                "clinical_data_harvester",
                {"patient_id": state["patient_id"], "doc_type": doc_type},
                raise_on_error=False,
            )
            parsed = _tool_result_to_dict(result)
            harvested[doc_type] = parsed
            if parsed.get("error"):
                errors.append(f"{doc_type}: {parsed['error']}")

    logger.info("Harvested %s doc_type(s) for %s", len(harvested), state["patient_id"])
    return {"harvested": harvested, "errors": errors}


def _coerce_and_validate(schema: type[BaseModel], args: dict) -> BaseModel:
    """Validate tool-call args into schema, tolerating empty nested-list rows."""
    cleaned = dict(args or {})
    # Common LLM key aliases for prescription rows
    if "medications" in cleaned and isinstance(cleaned["medications"], list):
        fixed_meds = []
        for item in cleaned["medications"]:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if not row.get("medicine_name"):
                for alt in ("name", "medication", "drug", "geneesmiddel", "medication_name"):
                    if row.get(alt):
                        row["medicine_name"] = row[alt]
                        break
            fixed_meds.append(row)
        cleaned["medications"] = fixed_meds

    # Nova sometimes returns null for list fields — treat as empty list.
    for field_name, field_info in schema.model_fields.items():
        if field_name not in cleaned or cleaned[field_name] is not None:
            continue
        ann = str(getattr(field_info, "annotation", ""))
        if "list" in ann.lower():
            cleaned[field_name] = []

    # Dutch/English approval phrases → bool
    if "discharge_approved" in cleaned and isinstance(cleaned["discharge_approved"], str):
        token = cleaned["discharge_approved"].strip().lower()
        if token in {"yes", "y", "ja", "true", "1", "ok"}:
            cleaned["discharge_approved"] = True
        elif token in {"no", "n", "nee", "false", "0"}:
            cleaned["discharge_approved"] = False
        else:
            cleaned["discharge_approved"] = None

    for field_name in schema.model_fields:
        value = cleaned.get(field_name)
        if isinstance(value, list):
            kept_rows = []
            for item in value:
                if isinstance(item, dict) and any(
                    v not in (None, "", []) for v in item.values()
                ):
                    kept_rows.append(item)
                elif isinstance(item, str) and item.strip():
                    kept_rows.append(item)
            cleaned[field_name] = kept_rows
    try:
        return schema.model_validate(cleaned)
    except ValidationError:
        # Keep lists; only drop rows that still fail individually.
        for field_name, value in list(cleaned.items()):
            if not isinstance(value, list):
                continue
            kept = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    kept.append(item)
                elif isinstance(item, dict) and any(
                    v not in (None, "", []) for v in item.values()
                ):
                    kept.append(item)
            cleaned[field_name] = kept
        try:
            return schema.model_validate(cleaned)
        except ValidationError:
            # Last resort: clear only the failing list fields, not everything.
            data = {k: v for k, v in cleaned.items() if not isinstance(v, list)}
            for field_name, field_info in schema.model_fields.items():
                ann = str(getattr(field_info, "annotation", ""))
                if "list" in ann.lower() and field_name not in data:
                    data[field_name] = []
            return schema.model_validate(data)


def _parse_json_object(text: str) -> dict:
    """Pull the first JSON object out of an LLM reply."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("LLM returned no JSON object")


async def _extract_one(llm, schema: type[BaseModel], system: str, raw_text: str) -> BaseModel:
    """Structured extraction with one JSON retry (same LLM path, SSoT simplicity)."""
    structured_llm = llm.with_structured_output(schema, include_raw=True)
    try:
        response = await structured_llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=raw_text)]
        )
        if response.get("parsed") is not None:
            return response["parsed"]
        raw_message = response.get("raw")
        tool_calls = getattr(raw_message, "tool_calls", None) or []
        if tool_calls:
            return _coerce_and_validate(schema, tool_calls[0].get("args", {}) or {})
        err = response.get("parsing_error")
        raise ValueError(f"LLM returned no structured output ({err})")
    except Exception as first_exc:
        logger.warning("Structured extract retry via JSON: %s", first_exc)
        retry_system = (
            f"{system}\n\n"
            "IMPORTANT: Reply with ONLY one JSON object matching the extraction fields. "
            "Copy every medication / lab test / bill line from the source. "
            "Do not wrap in markdown."
        )
        reply = await llm.ainvoke(
            [SystemMessage(content=retry_system), HumanMessage(content=raw_text)]
        )
        content = getattr(reply, "content", None)
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", part)) if isinstance(part, dict) else str(part)
                for part in content
            )
        return _coerce_and_validate(schema, _parse_json_object(str(content or "")))


async def _extract_json_force(
    llm, schema: type[BaseModel], system: str, raw_text: str
) -> BaseModel:
    """Force a plain-JSON completion (same Bedrock model, no tool schema)."""
    retry_system = (
        f"{system}\n\n"
        "Reply with ONLY one JSON object. Include a non-empty medications "
        "array when the source lists medicines. No markdown."
    )
    reply = await llm.ainvoke(
        [SystemMessage(content=retry_system), HumanMessage(content=raw_text)]
    )
    content = getattr(reply, "content", None)
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", part)) if isinstance(part, dict) else str(part)
            for part in content
        )
    return _coerce_and_validate(schema, _parse_json_object(str(content or "")))


class _MedsOnly(BaseModel):
    """Tiny schema used only when full discharge JSON drops the med table."""

    medications: list[PrescriptionItem] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    follow_up_appointment: str | None = None
    discharge_approved: bool | None = None


async def _extract_meds_rescue(llm, raw_text: str) -> _MedsOnly:
    """Last LLM retry focused only on meds / allergies / follow-up."""
    system = (
        "Extract medications, allergies, follow_up_appointment, and "
        "discharge_approved from this clinical discharge note. "
        "Copy EVERY prescription table row into medications with medicine_name. "
        "Reply with ONLY JSON."
    )
    return await _extract_json_force(llm, _MedsOnly, system, raw_text)


def _build_llm():
    """Direct Bedrock Nova Lite client (SSoT §10) — no MCP Sampling here."""
    from langchain_aws import ChatBedrockConverse

    cfg = get_bedrock_config()
    return ChatBedrockConverse(
        model_id=cfg["model_id"],
        region_name=cfg["region_name"],
        max_tokens=max(int(cfg["max_tokens"]), 4096),
        temperature=0,
    )


def _pick_text_for_extract(harvest: dict, resource_text: str) -> str:
    """Prefer Harvester text; if empty, fall back to MCP Resource text."""
    harvested = (harvest.get("raw_text") or "").strip()
    resource = (resource_text or "").strip()
    # Skip "not found" / binary notices from resources when harvest already has real text
    if harvested and not harvested.startswith("[no ") and not harvested.startswith("[binary"):
        if resource and resource != harvested and not resource.startswith("[no "):
            # Both usable — give LLM both (beginner-simple concat)
            return f"{harvested}\n\n--- MCP resource copy ---\n{resource}"
        return harvested
    if resource and not resource.startswith("[no "):
        return resource
    return harvested or resource


def _source_looks_like_it_has_meds(raw_text: str) -> bool:
    """Heuristic: source likely lists discharge medications (any language)."""
    text = (raw_text or "").lower()
    markers = (
        "amoxicill",
        "metformin",
        "paracetamol",
        "lisinopril",
        "atorvastatin",
        "medication",
        "medications",
        "prescription",
        "recept",
        "geneesmiddel",
        "ontslagrecept",
        "medicine_name",
        "sterkte",
    )
    return any(m in text for m in markers)


def _infer_discharge_approved(raw_text: str, current: bool | None) -> bool | None:
    """If the LLM left discharge_approved null, infer from common source phrases."""
    if current is not None:
        return current
    text = raw_text or ""
    # Patterns like "Discharge OK: YES", "Ontslag goedgekeurd: JA"
    if re.search(
        r"(discharge\s*(ok|approved)|ontslag\s*goedgekeurd)\s*[:\-]?\s*(yes|y|ja|true|1)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"(discharge\s*(ok|approved)|ontslag\s*goedgekeurd)\s*[:\-]?\s*(no|n|nee|false|0)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return False
    return current


async def extract_node(state: ExtractorState) -> dict:
    """Resources + Prompt + structure fields for this patient_id."""
    url = _primary_mcp_url()
    patient_id = state["patient_id"]
    errors = list(state.get("errors", []))
    resources: dict[str, str] = {}
    resources_used: dict[str, str] = {}

    async with Client(url) as client:
        # --- MCP Prompts (SSoT §3.4 / §5.2) ---
        prompt_result = await client.get_prompt(
            "discharge-extraction-prompt",
            {"language": "auto-detect", "doc_types": ",".join(state["doc_types"])},
        )
        instructions = _prompt_text(prompt_result)

        # --- MCP Resources (SSoT §3.3 / §5.2) — any patient_id, template filled at runtime ---
        for doc_type, uri_template in _RESOURCE_BY_DOC_TYPE.items():
            if doc_type not in state["doc_types"]:
                continue
            uri = uri_template.format(patient_id=patient_id)
            try:
                read_result = await client.read_resource(uri)
                text = _resource_text(read_result)
                resources[uri] = text
                resources_used[uri] = f"{len(text)} chars"
                logger.info("Read MCP resource %s (%s chars)", uri, len(text))
            except Exception as exc:
                note = f"resource {uri}: {exc}"
                errors.append(note)
                resources_used[uri] = f"error: {exc}"

    llm = None
    source_files: dict[str, str] = {}
    extraction = ExtractionResult(patient_id=patient_id)

    for doc_type in state["doc_types"]:
        harvest = state["harvested"].get(doc_type) or {}
        if harvest.get("error") and doc_type not in _RESOURCE_BY_DOC_TYPE:
            # Bill (etc.) with no file and no resource — nothing to extract
            continue

        source_files[doc_type] = harvest.get("source_file", "")
        resource_uri = _RESOURCE_BY_DOC_TYPE.get(doc_type, "").format(patient_id=patient_id)
        resource_text = resources.get(resource_uri, "")
        raw_text = _pick_text_for_extract(harvest, resource_text)

        if not raw_text.strip() or raw_text.strip().startswith("[binary"):
            if harvest.get("error"):
                continue
            errors.append(f"{doc_type}: no text to extract from")
            continue

        schema = _SCHEMA_BY_DOC_TYPE[doc_type]
        system = (
            f"{instructions}\n\n"
            f"You are extracting fields for a '{doc_type}' document. "
            "Fill every field you can find; leave the rest as null/empty. "
            "If the input is JSON, copy every list item (medications, tests, "
            "line_items) — do not drop or invent rows. "
            "For bills: map vendor_name→hospital_name, issue_date→billing_date, "
            "and copy line_items with description/qty/unit_price/total. "
            "If the source has a prescription / medication table (any language), "
            "extract EVERY row into medications with medicine_name, strength, "
            "dosage, frequency, route, period, remarks, total_quantity. "
            "Also extract allergies and follow-up appointment text when present. "
            "Detect the source language and report it in the 'language' field. "
            "For discharge docs, fill BOTH naming styles when possible: "
            "attending_physician/consulting_doctors/allergies/follow_up_appointment "
            "AND doctors/adr_allergy_info/follow_up_appointments."
        )
        try:
            llm = llm or _build_llm()
            result = await _extract_one(llm, schema, system, raw_text)
            # Same LLM path, one focused retry when a discharge clearly has a
            # med table but the first pass returned zero medications.
            if (
                doc_type == "discharge"
                and isinstance(result, DischargeExtraction)
                and not result.medications
                and _source_looks_like_it_has_meds(raw_text)
            ):
                logger.warning(
                    "Discharge meds empty for %s — retrying focused extraction",
                    patient_id,
                )
                focus = (
                    system
                    + "\n\nSECOND PASS: The source contains a medication/"
                    "prescription table. You MUST fill medications with every "
                    "row (medicine_name required). Also copy allergies and "
                    "follow_up_appointment text. Set discharge_approved true "
                    "when the source says approved/JA/yes."
                )
                result = await _extract_one(llm, schema, focus, raw_text)
                if not result.medications:
                    logger.warning(
                        "Focused structured pass still empty for %s — JSON force",
                        patient_id,
                    )
                    result = await _extract_json_force(llm, schema, focus, raw_text)
                if not result.medications:
                    logger.warning(
                        "Full JSON still missing meds for %s — meds rescue",
                        patient_id,
                    )
                    try:
                        rescue = await _extract_meds_rescue(llm, raw_text)
                        if rescue.medications:
                            result.medications = rescue.medications
                        if rescue.allergies and not result.allergies:
                            result.allergies = rescue.allergies
                        if rescue.follow_up_appointment and not (
                            result.follow_up_appointment or result.follow_up_appointments
                        ):
                            result.follow_up_appointment = rescue.follow_up_appointment
                        if (
                            result.discharge_approved is None
                            and rescue.discharge_approved is not None
                        ):
                            result.discharge_approved = rescue.discharge_approved
                    except Exception as rescue_exc:
                        logger.warning(
                            "Meds rescue failed for %s: %s", patient_id, rescue_exc
                        )
        except Exception as exc:
            logger.error("LLM extraction failed for %s/%s: %s", patient_id, doc_type, exc)
            errors.append(f"{doc_type}: LLM extraction failed ({exc})")
            continue

        if doc_type == "discharge" and isinstance(result, DischargeExtraction):
            result.discharge_approved = _infer_discharge_approved(
                raw_text, result.discharge_approved
            )
            extraction.discharge = fill_fa5_and_rules_aliases(result)
        elif doc_type == "lab":
            extraction.lab = result
        elif doc_type == "bill" and isinstance(result, BillExtraction):
            extraction.bill = _merge_bill_from_structured(
                result, harvest.get("structured_data")
            )

    extraction.source_files = source_files
    extraction.resources_used = resources_used
    extraction.notes = errors

    logger.info(
        "Extraction complete for %s (resources=%s, errors=%s)",
        patient_id,
        len(resources_used),
        len(errors),
    )
    return {
        "resources": resources,
        "extraction": extraction.model_dump(),
        "errors": errors,
    }
