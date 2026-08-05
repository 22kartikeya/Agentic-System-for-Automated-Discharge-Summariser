"""Clinical Data Harvester Tool (SSoT §3.5, §13 step 3).

Tool only — no Sampling, no Elicitation. Given a patient_id and a document
type, finds the file under data/input/ and returns readable text (+ parsed
JSON when the source is already structured). It does NOT try to map
foreign-language JSON keys into clinical fields — that semantic mapping is
the Clinical Extractor Agent's job (it uses the LLM so it can handle any
source language or key naming). The Harvester's only job is "get me the
text/tables out of this file".
"""

from __future__ import annotations

import json

from fastmcp import FastMCP

from mcp_servers.primary.rules_loader import find_patient_file, read_text_file
from shared.logger import get_logger
from shared.settings import get_path

logger = get_logger("clinical_data_harvester")

# doc_type (tool param) -> paths.* key in agent_config.yaml
_DOC_TYPE_PATH_KEYS = {
    "discharge": "input_doctor_reports",
    "lab": "input_lab_reports",
    "bill": "input_bills",
}

_BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}


async def _harvest_one(patient_id: str, doc_type: str) -> dict:
    """Locate and read one file for patient_id/doc_type. Returns a plain dict."""
    if doc_type not in _DOC_TYPE_PATH_KEYS:
        return {
            "patient_id": patient_id,
            "doc_type": doc_type,
            "error": f"unknown doc_type '{doc_type}', expected one of {list(_DOC_TYPE_PATH_KEYS)}",
        }

    folder = get_path(_DOC_TYPE_PATH_KEYS[doc_type])
    path = find_patient_file(folder, patient_id)
    if path is None:
        return {
            "patient_id": patient_id,
            "doc_type": doc_type,
            "error": f"no {doc_type} file found for {patient_id} under {folder}",
            "raw_text": "",
            "structured_data": None,
        }

    suffix = path.suffix.lower()
    is_binary = suffix in _BINARY_SUFFIXES and not path.name.endswith(".ocr.txt")

    result = {
        "patient_id": patient_id,
        "doc_type": doc_type,
        "source_file": path.name,
        "format": suffix.lstrip("."),
        "ocr_used": False,
        "structured_data": None,
    }

    if suffix == ".json":
        text = await read_text_file(path)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("Bad JSON in %s: %s", path, exc)
            result["raw_text"] = text
            result["error"] = f"invalid JSON: {exc}"
            return result
        result["structured_data"] = data
        # Prefer an embedded raw_text field (some samples include one for
        # OCR-style completeness); otherwise dump the JSON as text so the
        # Extractor's LLM step always has something readable to work from.
        result["raw_text"] = data.get("raw_text") or json.dumps(data, ensure_ascii=False, indent=2)
        return result

    if is_binary:
        # No OCR sidecar present. Tesseract OCR is optional (SSoT §10) and
        # disabled by default (TESSERACT_ENABLED=false) — surface a clear
        # notice instead of silently returning nothing.
        result["raw_text"] = (
            f"[binary file: {path.name} — no .ocr.txt sidecar found and OCR is disabled. "
            "Enable TESSERACT_ENABLED and add OCR to harvest this file.]"
        )
        result["error"] = "binary_without_ocr"
        return result

    # .txt / .ocr.txt sidecar — plain readable text
    result["raw_text"] = await read_text_file(path)
    result["ocr_used"] = path.name.endswith(".ocr.txt")
    return result


def register_harvester_tools(mcp: FastMCP) -> None:
    """Attach the Clinical Data Harvester tool to the Primary MCP server."""

    @mcp.tool(
        name="clinical_data_harvester",
        title="Clinical Data Harvester Tool",
        description=(
            "Extract text/tables from a patient's discharge report, lab report, "
            "or bill under data/input/. doc_type must be 'discharge', 'lab', or 'bill'."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def clinical_data_harvester(patient_id: str, doc_type: str) -> str:
        """Harvest one document for one patient. Returns a JSON string."""
        result = await _harvest_one(patient_id, doc_type)
        logger.info(
            "Harvested %s/%s -> %s (error=%s)",
            patient_id,
            doc_type,
            result.get("source_file"),
            result.get("error"),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
