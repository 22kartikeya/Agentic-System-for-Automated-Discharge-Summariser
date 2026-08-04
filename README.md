# Agentic-System-for-Automated-Discharge-Summariser
hi
Agentic AI system for automated hospital discharge summaries (FA5 capstone).

## Documentation (SSoT)

- [`Documentation/REQUIREMENTS_REFERENCE.md`](Documentation/REQUIREMENTS_REFERENCE.md) — single source of truth
- [`Documentation/coding_style/`](Documentation/coding_style/) — company coding patterns
- Project layout section inside the requirements reference mirrors this repo

## Quick start (scaffold)

```bash
cp .env.example .env
# fill secrets, then:
python run.py
```

Services and ports are defined in [`configs/agent_config.yaml`](configs/agent_config.yaml).

## Layout (summary)

| Folder | Role |
| --- | --- |
| `agents/` | Monitor, Extractor, Normalizer, Validator, Summary (A2A) |
| `rag/` | Agno 5-agent RAG Q&A (:8105 streaming) |
| `mcp_servers/` | Primary `:8200/clinicaltools` + Secondary `:8201/analyticstools` |
| `host/` | Google ADK + Gradio orchestrator (:8083) |
| `dashboard/` | Streamlit HITL 5 pages (:8501) |
| `mock_ehr/` | FastAPI Mock EHR (:8050) |
| `configs/` | Runtime `rules.yaml`, prompts, agent/model/MCP config |
| `data/input/` | MCP Roots workspace (sample corpus synced here) |
| `Documentation/` | Specs, seeds, coding style (not runtime) |

Implementation status: **folder scaffold only** — agent/MCP logic not built yet.
