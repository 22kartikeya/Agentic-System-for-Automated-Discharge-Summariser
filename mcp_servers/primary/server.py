"""Primary Clinical Tools MCP Server — port 8200, path /clinicaltools.

Phase 2 added Resources + Prompts. Phase 3 added the Clinical Watcher tool
(Tools + Roots). Phase 4 added the Clinical Data Harvester tool (Tools).
Sampling / Elicitation / remaining Tools land in later phases.

Run from repo root:
    uv run python -m mcp_servers.primary
"""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_servers.primary.prompts import register_prompts
from mcp_servers.primary.resources import register_resources
from mcp_servers.primary.tools.clinical_data_harvester import register_harvester_tools
from mcp_servers.primary.tools.clinical_watcher import register_watcher_tools
from shared.logger import get_logger
from shared.settings import get_service

logger = get_logger("primary_mcp")

mcp = FastMCP(name="Primary Clinical Tools Server")

register_resources(mcp)
register_prompts(mcp)
register_watcher_tools(mcp)
register_harvester_tools(mcp)


def main() -> None:
    # Host/port/path from configs/agent_config.yaml (SSoT §2)
    svc = get_service("primary_mcp")
    host = svc.get("host", "127.0.0.1")
    port = int(svc.get("port", 8200))
    path = svc.get("transport_path", "/clinicaltools")

    logger.info("Primary MCP starting on http://%s:%s%s", host, port, path)
    # Company coding style: streamable-http.
    # FastMCP 2.12: pass host/port/path to run() (path == streamable_http_path).
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        path=path,
    )


if __name__ == "__main__":
    main()
