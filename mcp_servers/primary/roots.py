"""Roots helpers — URI ↔ Path + path-traversal guards (SSoT §3.8).

Clinical Watcher must only scan inside client-declared Roots.
Use Path.relative_to() so anything outside the root is rejected.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse


# Subfolders under the MCP Root workspace (SSoT §12 / architecture)
INTAKE_SUBFOLDERS = ("doctor_reports", "lab_reports", "bills")


def file_uri_to_path(uri: str) -> Path:
    """Convert a file:// URI to a local Path.

    Examples:
        file:///data/input  ->  /data/input
        file:///Users/me/proj/data/input  ->  /Users/me/proj/data/input
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Only file:// Roots are supported, got: {uri}")
    # unquote handles spaces / encoded chars in paths
    return Path(unquote(parsed.path)).resolve()


def path_to_file_uri(path: Path) -> str:
    """Convert a local Path to a file:// URI (absolute)."""
    resolved = path.resolve()
    return resolved.as_uri()


def assert_inside_root(candidate: Path, root: Path) -> Path:
    """Return resolved candidate if it is inside root; else raise ValueError.

    This is the SSoT §3.8 path-traversal prevention rule.
    """
    candidate_resolved = candidate.resolve()
    root_resolved = root.resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Path escapes declared Root: {candidate_resolved} not under {root_resolved}"
        ) from exc
    return candidate_resolved


def safe_join(root: Path, *parts: str) -> Path:
    """Join parts under root and reject traversal (e.g. '../etc')."""
    return assert_inside_root(root.joinpath(*parts), root)
