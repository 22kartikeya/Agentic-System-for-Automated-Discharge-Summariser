"""Helpers to load configs/rules.yaml and configs/prompts.yaml for MCP Resources/Prompts."""

from __future__ import annotations

from pathlib import Path

import aiofiles
import yaml

from shared.settings import get_path


def load_rules() -> dict:
    """Load runtime configs/rules.yaml."""
    path = get_path("rules_yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompts_config() -> dict:
    """Load configs/prompts.yaml (prompt bodies for MCP Prompts)."""
    path = get_path("prompts_yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("prompts", {})


def find_patient_file(folder: Path, patient_id: str) -> Path | None:
    """
    Find the best readable file for a patient under doctor_reports/ or lab_reports/.

    Preference order (simple, beginner-friendly):
    1. Prefer .ocr.txt sidecar when a binary exists
    2. Else prefer .txt / .json
    3. Else first matching file
    """
    if not folder.exists():
        return None

    matches = sorted(folder.glob(f"{patient_id}*"))
    if not matches:
        return None

    for path in matches:
        if path.name.endswith(".ocr.txt"):
            return path

    for path in matches:
        if path.suffix.lower() in {".txt", ".json"}:
            return path

    return matches[0]


async def read_text_file(path: Path) -> str:
    """Read a text-ish file with aiofiles (coding-style pattern).

    For PDF/PNG without an OCR sidecar, return a short notice — full OCR
    belongs to the Harvester tool in a later phase.
    """
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".png", ".jpg", ".jpeg"} and not path.name.endswith(".ocr.txt"):
        return (
            f"[binary file: {path.name} — use the .ocr.txt sidecar when available; "
            "full OCR lands in Harvester phase]"
        )

    async with aiofiles.open(path, mode="r", encoding="utf-8", errors="replace") as f:
        return await f.read()
