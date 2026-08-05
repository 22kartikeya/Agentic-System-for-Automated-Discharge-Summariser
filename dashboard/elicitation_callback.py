"""Streamlit elicitation callback (SSoT §3.7).

Beginner picture:
  - Rules Engine calls ctx.elicit() once with a dynamic schema.
  - This handler stages the request and, when the reviewer already filled
    Page 3's form (accept / decline / cancel), returns that ElicitResult.
  - If nothing is staged yet, declines safely (Mandatory HITL) so headless
    runs never hang.

Page 3 is the human-facing form; this module is the MCP client bridge.
"""

from __future__ import annotations

from typing import Any

from fastmcp.client.elicitation import ElicitResult

from shared.logger import get_logger

logger = get_logger("hitl_elicitation")

# Staged reviewer decision for the next Rules Engine elicit() call.
# Shape: {"action": "accept"|"decline"|"cancel", "data": dict|None, "message": str}
_staged_response: dict[str, Any] | None = None
_last_request: dict[str, Any] | None = None


def stage_elicitation_response(
    action: str,
    data: dict | None = None,
    message: str = "",
) -> None:
    """Called from Page 3 when the reviewer clicks Accept / Decline / Cancel."""
    global _staged_response
    if action not in {"accept", "decline", "cancel"}:
        raise ValueError(f"invalid elicitation action: {action}")
    _staged_response = {"action": action, "data": data or {}, "message": message}
    logger.info("Staged elicitation action=%s fields=%s", action, list((data or {}).keys()))


def clear_staged_response() -> None:
    global _staged_response
    _staged_response = None


def last_elicitation_request() -> dict | None:
    """Most recent elicit request seen by the handler (for Page 3 display)."""
    return _last_request


def _schema_field_names(response_type: Any) -> list[str]:
    """Best-effort field list from a Pydantic model / schema object."""
    if response_type is None:
        return []
    model_fields = getattr(response_type, "model_fields", None)
    if isinstance(model_fields, dict):
        return list(model_fields.keys())
    schema = getattr(response_type, "model_json_schema", None)
    if callable(schema):
        props = (schema() or {}).get("properties") or {}
        return list(props.keys())
    return []


async def streamlit_elicitation_handler(message, response_type, params, context) -> ElicitResult:
    """FastMCP elicitation_handler used during interactive Validator re-runs."""
    global _last_request, _staged_response
    fields = _schema_field_names(response_type)
    _last_request = {
        "message": str(message or ""),
        "fields": fields,
    }
    logger.info("Elicitation request: %s fields=%s", message, fields)

    staged = _staged_response
    _staged_response = None  # one-shot

    if staged is None:
        logger.info("No staged HITL response — declining elicitation")
        return ElicitResult(action="decline")

    action = staged.get("action", "decline")
    if action == "accept":
        data = staged.get("data") or {}
        # Build an instance of the dynamic schema when possible
        try:
            if response_type is not None and hasattr(response_type, "model_validate"):
                model = response_type.model_validate(data)
                return ElicitResult(action="accept", data=model)
        except Exception as exc:
            logger.warning("Could not bind elicitation data to schema (%s) — declining", exc)
            return ElicitResult(action="decline")
        return ElicitResult(action="accept", data=data)

    return ElicitResult(action=action)
