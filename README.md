# Agentic-System-for-Automated-Discharge-Summariser

Agentic AI system for automated hospital discharge summaries (FA5 capstone).

## Documentation (SSoT)

- [`Documentation/REQUIREMENTS_REFERENCE.md`](Documentation/REQUIREMENTS_REFERENCE.md) — single source of truth
- [`Documentation/architecture.md`](Documentation/architecture.md) — finalized end-to-end architecture diagram
- [`Documentation/coding_style/`](Documentation/coding_style/) — company coding patterns
- [`.cursor/plans/phased_implementation_roadmap_816e3afe.plan.md`](.cursor/plans/phased_implementation_roadmap_816e3afe.plan.md) — build order + per-phase status
- Project layout section inside the requirements reference mirrors this repo

## Quick start

```bash
cp .env.example .env
uv sync
```

Run the services that exist so far (each in its own terminal):

```bash
uv run python -m mock_ehr          # Mock EHR REST API on :8050
uv run python -m mcp_servers.primary  # Primary MCP (clinicaltools) on :8200
uv run python -m agents.monitor    # Discharge Monitor A2A service on :8103
```

Host/port for every service come from [`configs/agent_config.yaml`](configs/agent_config.yaml).

## Implementation status

Built bottom-up, one phase at a time, per the phased roadmap. Each ✅ phase has been reviewed and has a manual test; everything else is a docstring/TODO stub.

| Phase | Module | Status |
| --- | --- | --- |
| 1 | `shared/` config + logger, Mock EHR FastAPI `:8050` (seeded from `mock_ehr/seed.py`) | ✅ done |
| 2 | Primary MCP skeleton `:8200/clinicaltools` — resources (`rules.yaml`) + prompts | ✅ done |
| 3 | MCP Roots + Clinical Watcher tool + Discharge Monitor agent (`:8103`, A2A) | ✅ done |
| 4 | Clinical Data Harvester tool + Extractor agent | ⏳ stub |
| 5 | Medical Lang Bridge + Sampling + Normalizer agent | ⏳ stub |
| 6–7 | Rules Engine / EHR Validation / Insight Reporter tools + Secondary MCP `:8201` | ⏳ stub |
| 8 | Validator agent + release gate | ⏳ stub |
| 9 | Summary Generator (A2A streaming) | ⏳ stub |
| 10 | Agno RAG `:8105` | ⏳ stub |
| 11 | Streamlit HITL dashboard `:8501` | ⏳ stub |
| 12 | Host (Google ADK + Gradio) `:8083` + `run.py` wiring | ⏳ stub |

## Layout (summary)

| Folder | Role |
| --- | --- |
| `agents/` | Monitor (✅), Extractor, Normalizer, Validator, Summary (A2A) |
| `rag/` | Agno 5-agent RAG Q&A (:8105 streaming) |
| `mcp_servers/` | Primary `:8200/clinicaltools` (resources/prompts/watcher ✅) + Secondary `:8201/analyticstools` |
| `host/` | Google ADK + Gradio orchestrator (:8083) |
| `dashboard/` | Streamlit HITL 5 pages (:8501) |
| `mock_ehr/` | FastAPI Mock EHR (:8050) ✅ |
| `configs/` | Runtime `rules.yaml`, prompts, agent/model/MCP config |
| `data/input/` | MCP Roots workspace (sample corpus synced here) |
| `Documentation/` | Specs, seeds, coding style (not runtime) |

## Manually testing what's built

```bash
# Mock EHR
curl http://127.0.0.1:8050/health
curl http://127.0.0.1:8050/patients/P1019

# Primary MCP resources/prompts — use an MCP client (FastMCP CLI, Inspector, etc.)
uv run fastmcp dev mcp_servers/primary/server.py
```
