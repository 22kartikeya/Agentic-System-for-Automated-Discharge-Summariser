"""Extractor graph nodes — Harvester tool + MCP Prompt (NO Sampling, SSoT §5.2).

Two nodes:
1. harvest_node  — calls the Clinical Data Harvester MCP tool for each doc_type.
2. extract_node  — fetches discharge-extraction-prompt via MCP, then asks the
   LLM (direct call, not MCP Sampling — Sampling is Normalizer/Lang-Bridge's
   job in Phase 5) for structured output per doc_type. This works the same
   way for English text, Hindi/Dutch text, and foreign-keyed JSON, because
   the LLM — not brittle key-matching code — does the field mapping.
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
)
from shared.settings import get_bedrock_config, get_service

logger = get_logger("extractor")

_SCHEMA_BY_DOC_TYPE = {
    "discharge": DischargeExtraction,
    "lab": LabExtraction,
    "bill": BillExtraction,
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
    """Validate tool-call args into schema, tolerating empty nested-list rows.

    Observed with Bedrock Nova Lite on nested lists (e.g. bill line_items):
    it sometimes returns one empty {} per expected row instead of leaving
    the field out. Rather than fail the whole document, drop unusable rows
    (beginner-friendly: best-effort, not a silent data fix — see notes list).
    """
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
        # Last resort: clear any list field that still fails validation.
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
    from langchain_aws import ChatBedrockConverse  # local import: optional heavy dep

    cfg = get_bedrock_config()
    return ChatBedrockConverse(
        model_id=cfg["model_id"],
        region_name=cfg["region_name"],
        max_tokens=cfg["max_tokens"],
        temperature=0,
    )


async def extract_node(state: ExtractorState) -> dict:
    """Turn each harvested raw_text into structured clinical fields via the LLM."""
    url = _primary_mcp_url()
    doc_types_present = [
        doc_type for doc_type in state["doc_types"] if not state["harvested"].get(doc_type, {}).get("error")
    ]

    async with Client(url) as client:
        prompt_result = await client.get_prompt(
            "discharge-extraction-prompt",
            {"language": "auto-detect", "doc_types": ",".join(state["doc_types"])},
        )
    instructions = _prompt_text(prompt_result)

    llm = None  # built lazily — only needed when a source is unstructured text
    source_files: dict[str, str] = {}
    errors = list(state.get("errors", []))
    extraction = ExtractionResult(patient_id=state["patient_id"])

    for doc_type in doc_types_present:
        harvest = state["harvested"][doc_type]
        raw_text = harvest.get("raw_text") or ""
        source_files[doc_type] = harvest.get("source_file", "")
        structured_data = harvest.get("structured_data")

        if structured_data is not None:
            # Already valid JSON — map fields directly (fast, deterministic,
            # and avoids an LLM tool-calling quirk seen on nested arrays; see
            # agents/extractor/json_mapping.py for why).
            result = MAPPERS[doc_type](structured_data)
        else:
            if not raw_text.strip():
                errors.append(f"{doc_type}: no text to extract from")
                continue

            schema = _SCHEMA_BY_DOC_TYPE[doc_type]
            system = (
                f"{instructions}\n\n"
                f"You are extracting fields for a '{doc_type}' document. "
                "Fill every field you can find; leave the rest as null/empty. "
                "Detect the source language and report it in the 'language' field."
            )
            try:
                llm = llm or _build_llm()
                result = await _extract_one(llm, schema, system, raw_text)
            except Exception as exc:  # surface LLM/network errors as case-level notes, don't crash the case
                logger.error("LLM extraction failed for %s/%s: %s", state["patient_id"], doc_type, exc)
                errors.append(f"{doc_type}: LLM extraction failed ({exc})")
                continue

        if doc_type == "discharge":
            extraction.discharge = result
        elif doc_type == "lab":
            extraction.lab = result
        elif doc_type == "bill":
            extraction.bill = result

    extraction.source_files = source_files
    extraction.notes = errors

    logger.info("Extraction complete for %s (errors=%s)", state["patient_id"], len(errors))
    return {"extraction": extraction.model_dump(), "errors": errors}
