"""Extractor graph nodes — Tools + Resources + Prompts (NO Sampling, SSoT §5.2).

Two nodes:
1. harvest_node  — Clinical Data Harvester MCP tool for each doc_type.
2. extract_node  — reads MCP Resources for this patient_id, fetches
   discharge-extraction-prompt via MCP, then structures fields
   (JSON mapper for structured files; LLM for unstructured text).

Works for ANY patient_id that has files under data/input/ — no hard-coded
sample patient list.
"""

from __future__ import annotations

import json

from fastmcp import Client
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from agents.extractor.json_mapping import MAPPERS
from agents.extractor.state import ExtractorState
from shared.logger import get_logger
from shared.models.extraction import (
    BillExtraction,
    DischargeExtraction,
    ExtractionResult,
    LabExtraction,
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
    cleaned = dict(args)
    for field_name in schema.model_fields:
        value = cleaned.get(field_name)
        if isinstance(value, list):
            cleaned[field_name] = [
                item
                for item in value
                if isinstance(item, dict) and any(v not in (None, "", []) for v in item.values())
            ]
    try:
        return schema.model_validate(cleaned)
    except ValidationError:
        for field_name, value in list(cleaned.items()):
            if isinstance(value, list):
                cleaned[field_name] = []
        return schema.model_validate(cleaned)


async def _extract_one(llm, schema: type[BaseModel], system: str, raw_text: str) -> BaseModel:
    """Structured extraction with a tolerant fallback for messy tool-call output."""
    structured_llm = llm.with_structured_output(schema, include_raw=True)
    response = await structured_llm.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=raw_text)]
    )
    if response.get("parsed") is not None:
        return response["parsed"]

    raw_message = response.get("raw")
    tool_calls = getattr(raw_message, "tool_calls", None) or []
    if not tool_calls:
        raise ValueError("LLM returned no structured output")
    return _coerce_and_validate(schema, tool_calls[0].get("args", {}))


def _build_llm():
    """Direct Bedrock Nova Lite client (SSoT §10) — no MCP Sampling here."""
    from langchain_aws import ChatBedrockConverse

    cfg = get_bedrock_config()
    return ChatBedrockConverse(
        model_id=cfg["model_id"],
        region_name=cfg["region_name"],
        max_tokens=cfg["max_tokens"],
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
        structured_data = harvest.get("structured_data")
        resource_uri = _RESOURCE_BY_DOC_TYPE.get(doc_type, "").format(patient_id=patient_id)
        resource_text = resources.get(resource_uri, "")

        if structured_data is not None:
            result = MAPPERS[doc_type](structured_data)
            # Lab JSON with unknown/foreign keys → empty tests. Fall through to LLM
            # so new patients with non-English keys still extract correctly.
            if doc_type == "lab" and isinstance(result, LabExtraction) and not result.tests:
                structured_data = None
            else:
                if doc_type == "discharge" and isinstance(result, DischargeExtraction):
                    extraction.discharge = fill_fa5_and_rules_aliases(result)
                elif doc_type == "lab":
                    extraction.lab = result
                elif doc_type == "bill":
                    extraction.bill = result
                continue

        if structured_data is None:
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
                "Detect the source language and report it in the 'language' field. "
                "For discharge docs, fill BOTH naming styles when possible: "
                "attending_physician/consulting_doctors/allergies/follow_up_appointment "
                "AND doctors/adr_allergy_info/follow_up_appointments."
            )
            try:
                llm = llm or _build_llm()
                result = await _extract_one(llm, schema, system, raw_text)
            except Exception as exc:
                logger.error("LLM extraction failed for %s/%s: %s", patient_id, doc_type, exc)
                errors.append(f"{doc_type}: LLM extraction failed ({exc})")
                continue

            if doc_type == "discharge" and isinstance(result, DischargeExtraction):
                extraction.discharge = fill_fa5_and_rules_aliases(result)
            elif doc_type == "lab":
                extraction.lab = result
            elif doc_type == "bill":
                extraction.bill = result

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
