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
uv run python -m agents.extractor  # Clinical Extractor A2A service on :8100
```

Extractor needs AWS Bedrock credentials in `.env` (see `.env.example`) for free-text /
OCR sources. JSON intake files map directly (no LLM). PDFs use PyPDF2; set
`TESSERACT_ENABLED=true` (and install `tesseract`) for image OCR when no `.ocr.txt`
sidecar exists.

Host/port for every service come from [`configs/agent_config.yaml`](configs/agent_config.yaml).

## Implementation status

Built bottom-up, one phase at a time, per the phased roadmap. Each ✅ phase has been reviewed and has a manual test; everything else is a docstring/TODO stub.

| Phase | Module | Status |
| --- | --- | --- |
| 1 | `shared/` config + logger, Mock EHR FastAPI `:8050` (seeded from `mock_ehr/seed.py`) | ✅ done |
| 2 | Primary MCP skeleton `:8200/clinicaltools` — resources (`rules.yaml`) + prompts | ✅ done |
| 3 | MCP Roots + Clinical Watcher tool + Discharge Monitor agent (`:8103`, A2A) | ✅ done |
| 4 | Clinical Data Harvester + Extractor (`:8100`, LangGraph, A2A) — Tools + Resources + Prompts; PDF/optional OCR | ✅ done |
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
| `agents/` | Monitor (✅), Extractor (✅), Normalizer, Validator, Summary (A2A) |
| `rag/` | Agno 5-agent RAG Q&A (:8105 streaming) |
| `mcp_servers/` | Primary `:8200/clinicaltools` (resources/prompts/watcher/harvester ✅, PDF/OCR readers) + Secondary `:8201/analyticstools` |
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

# Primary MCP (Watcher + Harvester) — FastMCP Inspector / CLI
uv run fastmcp dev mcp_servers/primary/server.py

# Extractor LangGraph pipeline (needs Primary MCP up; Bedrock for .txt/OCR only)
# Works for any patient_id under data/input/ (e.g. P1019, P1021, P1024)
uv run python -c "
import asyncio, json
from agents.extractor.graph import run_extraction
print(json.dumps(asyncio.run(run_extraction('P1019')), indent=2, ensure_ascii=False))
"

# A2A AgentCards (public)
curl http://127.0.0.1:8103/.well-known/agent.json
curl http://127.0.0.1:8100/.well-known/agent.json
```
