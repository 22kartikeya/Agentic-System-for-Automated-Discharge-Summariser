"""Project launcher — starts all SSoT services from configs/agent_config.yaml.

Status: scaffold only. Will spawn Mock EHR, dual MCP, agents, RAG, Host, HITL.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    config = root / "configs" / "agent_config.yaml"
    print("Agentic Discharge Summaries — launcher scaffold")
    print(f"Repo root: {root}")
    print(f"Config:    {config} ({'found' if config.exists() else 'MISSING'})")
    print()
    print("Services to start (see configs/agent_config.yaml):")
    print("  mock_ehr        :8050")
    print("  primary_mcp     :8200/clinicaltools")
    print("  secondary_mcp   :8201/analyticstools")
    print("  extractor       :8100")
    print("  validator       :8101")
    print("  normalizer      :8102")
    print("  monitor         :8103")
    print("  summary         :8104 (streaming)")
    print("  rag             :8105 (streaming)")
    print("  host            :8083 (Gradio)")
    print("  hitl_dashboard  :8501 (Streamlit)")
    print()
    print("TODO: implement process orchestration once services are built.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
