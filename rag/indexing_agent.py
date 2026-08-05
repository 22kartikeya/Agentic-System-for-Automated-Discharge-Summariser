"""Agno Indexing Agent — parse intake docs into FAISS (SSoT §5.6 agent 1).

Beginner picture:
  1. Find this patient_id's files under data/input/ (any patient, any digit length).
  2. Read text (txt/json/ocr; PDF via PyPDF2; optional Tesseract for images).
  3. Split with RecursiveCharacterTextSplitter (sizes from agent_config.yaml).
  4. Save into FAISS at data/vector_db/{patient_id}/.
  5. Re-index when the intake file set changes (new uploads must not stay stale).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agno.agent import Agent
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.model import get_agno_model
from rag.vectorstore import (
    clear_patient_index,
    indexed_source_paths,
    patient_is_indexed,
    save_patient_index,
)
from shared.logger import get_logger
from shared.settings import get_path, get_service

logger = get_logger("rag_indexing")

_PATIENT_PREFIX_RE = re.compile(r"^(P\d+)", re.IGNORECASE)

# Text-like suffixes we can index directly. Prefer .ocr.txt when present for scans.
_TEXT_SUFFIXES = {".txt", ".json", ".md", ".csv"}
_BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}


def _rag_cfg() -> dict:
    return get_service("rag")


def _patient_id_from_filename(name: str) -> str | None:
    """Extract P### from filenames like P1019_thomas_wright.txt / P1021_labs.json."""
    stem = name
    # Strip multi-part suffixes like .pdf.ocr.txt
    while True:
        lowered = stem.lower()
        if lowered.endswith(".ocr.txt"):
            stem = stem[: -len(".ocr.txt")]
            continue
        p = Path(stem)
        if p.suffix:
            stem = p.stem
            continue
        break
    match = _PATIENT_PREFIX_RE.match(stem)
    return match.group(1).upper() if match else None


def _read_file_text(path: Path) -> str:
    """Read text/json/ocr, or extract PDF/image text for new uploads without sidecars."""
    suffix = path.suffix.lower()
    name = path.name.lower()

    if name.endswith(".ocr.txt") or suffix in {".txt", ".md", ".csv"}:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return ""

    if suffix == ".json":
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return ""
        try:
            data = json.loads(raw)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return raw

    if suffix in _BINARY_SUFFIXES:
        try:
            from mcp_servers.primary.document_readers import read_binary_document

            text, meta = read_binary_document(path)
            if meta.get("error"):
                logger.warning("Binary read %s: %s", path.name, meta["error"])
            return text or ""
        except Exception as exc:
            logger.warning("Could not extract text from %s: %s", path, exc)
            return ""

    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _logical_stem(path: Path) -> str:
    """Stable stem so .pdf, .pdf.ocr.txt, and .json siblings share one key."""
    name = path.name
    if name.lower().endswith(".ocr.txt"):
        name = name[: -len(".ocr.txt")]
    return Path(name).stem


def _file_priority(path: Path) -> int:
    """Higher wins: OCR sidecar > text/json > raw PDF/image."""
    name = path.name.lower()
    if name.endswith(".ocr.txt"):
        return 3
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return 2
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return 1
    return 0


def _collect_patient_files(patient_id: str) -> list[tuple[Path, str]]:
    """Return [(path, doc_type), ...] for this patient under intake folders.

    Prefers .ocr.txt / .json / .txt over raw binary siblings. Falls back to PDF
    (PyPDF2) or image OCR when no text sidecar exists — needed for new uploads.
    """
    patient_id = patient_id.strip().upper()
    input_root = get_path("input_root")
    folders = {
        "doctor_reports": get_path("input_doctor_reports"),
        "lab_reports": get_path("input_lab_reports"),
        "bills": get_path("input_bills"),
    }
    for key, folder in list(folders.items()):
        if not folder.is_dir():
            alt = input_root / key
            folders[key] = alt if alt.is_dir() else folder

    # key -> (path, doc_type, priority)
    chosen: dict[str, tuple[Path, str, int]] = {}
    for doc_type, folder in folders.items():
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            if _patient_id_from_filename(path.name) != patient_id:
                continue
            priority = _file_priority(path)
            if priority <= 0:
                continue
            key = f"{doc_type}:{_logical_stem(path)}"
            prev = chosen.get(key)
            if prev is None or priority > prev[2]:
                chosen[key] = (path, doc_type, priority)

    return [(path, doc_type) for path, doc_type, _ in chosen.values()]


def _indexed_source_paths(patient_id: str) -> set[str]:
    """Paths currently stored in meta.json (empty if not indexed)."""
    return indexed_source_paths(patient_id)

def index_patient_documents(patient_id: str, force: bool = False) -> str:
    """Index one patient's intake files into FAISS. Safe for any patient_id."""
    patient_id = str(patient_id).strip().upper()
    files = _collect_patient_files(patient_id)

    if not files:
        clear_patient_index(patient_id)
        msg = f"No intake files found for patient {patient_id} under data/input/."
        logger.warning(msg)
        return msg

    current_paths = {str(path) for path, _ in files}
    if (
        not force
        and patient_is_indexed(patient_id)
        and _indexed_source_paths(patient_id) == current_paths
    ):
        msg = f"Patient {patient_id} already indexed — skipped."
        logger.info(msg)
        return msg

    cfg = _rag_cfg()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=int(cfg.get("chunk_size", 500)),
        chunk_overlap=int(cfg.get("chunk_overlap", 50)),
    )

    chunks: list[dict] = []
    for path, doc_type in files:
        text = _read_file_text(path)
        if not text.strip():
            logger.warning("Empty text for %s — skipped (need OCR sidecar or readable PDF)", path.name)
            continue
        for piece in splitter.split_text(text):
            chunks.append(
                {
                    "text": piece,
                    "source_path": str(path),
                    "doc_type": doc_type,
                }
            )

    if not chunks:
        clear_patient_index(patient_id)
        msg = (
            f"Found {len(files)} file(s) for {patient_id} but extracted no readable text "
            "(add .txt/.json/.ocr.txt or a text PDF)."
        )
        logger.warning(msg)
        return msg

    count = save_patient_index(patient_id, chunks)
    return f"Indexed {count} chunk(s) from {len(files)} file(s) for {patient_id}."


indexing_agent = Agent(
    name="indexing_agent",
    model=get_agno_model(),
    description="Indexes discharge / lab / bill intake documents into FAISS for any patient_id.",
    instructions=(
        "When asked to index a patient, call index_patient_documents with that patient_id. "
        "Never invent file paths. Never invent clinical content."
    ),
    tools=[index_patient_documents],
)


async def run_indexing(patient_id: str, force: bool = False) -> str:
    """Deterministic indexing path used by the RAG pipeline (beginner-simple)."""
    return index_patient_documents(patient_id, force=force)
