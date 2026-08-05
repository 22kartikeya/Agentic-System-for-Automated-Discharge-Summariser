# Agentic-System-for-Automated-Discharge-Summariser

Agentic AI system for automated hospital discharge summaries (FA5 capstone).

## Documentation (SSoT)

- [`Documentation/REQUIREMENTS_REFERENCE.md`](Documentation/REQUIREMENTS_REFERENCE.md) — single source of truth
- [`Documentation/architecture.md`](Documentation/architecture.md) — end-to-end architecture
- [`Documentation/coding_style/`](Documentation/coding_style/) — company coding patterns
- [`.cursor/plans/phased_implementation_roadmap_816e3afe.plan.md`](.cursor/plans/phased_implementation_roadmap_816e3afe.plan.md) — build order + per-phase status

## Quick start

```bash
cp .env.example .env   # add AWS Bedrock credentials
uv sync
```

Run services that exist so far (each in its own terminal):

```bash
uv run python -m mock_ehr               # Mock EHR REST API           :8050
uv run python -m mcp_servers.primary    # Primary MCP /clinicaltools  :8200
uv run python -m agents.monitor         # Discharge Monitor A2A       :8103
uv run python -m agents.extractor       # Clinical Extractor A2A      :8100
uv run python -m agents.normalizer      # Clinical Normalizer A2A     :8102
```

Host/port for every service: [`configs/agent_config.yaml`](configs/agent_config.yaml).

### Notes for Phases 4–5

- **Extractor** — JSON intake maps directly (no LLM). Unstructured text uses Bedrock.
  PDFs via PyPDF2; set `TESSERACT_ENABLED=true` for image OCR when no `.ocr.txt` sidecar.
- **Normalizer** — MCP **Sampling** via Medical Lang Bridge. The LLM runs in the
  client `sampling_callback` (LiteLLM), **not** inside the MCP server.
- **Languages** — primary set from `rules.yaml` / seed: `en`, `es`, `hi`, `de`, `fr`, `nl`
  (`shared/language.py`). Unexpected languages use a **fallback** path (still translate;
  never reject). After Sampling, `shared/clinical_normalize.py` expands abbreviations,
  canonicalizes med names (§12.3), and applies ICD-10.
- Normalizer A2A asks Extractor over A2A when no extraction JSON is embedded —
  start Extractor (`:8100`) before that path.

## Implementation status

Built bottom-up per the phased roadmap. ✅ = implemented + manually smoke-tested.

| Phase | Module | Status |
| --- | --- | --- |
| 1 | `shared/` settings + logger · Mock EHR FastAPI `:8050` | ✅ done |
| 2 | Primary MCP `:8200/clinicaltools` — resources + prompts | ✅ done |
| 3 | Roots + Clinical Watcher + Monitor A2A `:8103` | ✅ done |
| 4 | Harvester + Extractor LangGraph A2A `:8100` (Tools + Resources + Prompts) | ✅ done |
| 5 | Medical Lang Bridge (Sampling) + Normalizer LangGraph A2A `:8102` | ✅ done |
| 6–7 | Rules Engine / EHR Validation / Reporter + Secondary MCP `:8201` | ⏳ stub |
| 8 | Validator agent + release gate `:8101` | ⏳ stub |
| 9 | Summary Generator (A2A streaming) `:8104` | ⏳ stub |
| 10 | Agno RAG `:8105` | ⏳ stub |
| 11 | Streamlit HITL dashboard `:8501` | ⏳ stub |
| 12 | Host (ADK + Gradio) `:8083` + `run.py` | ⏳ stub |

**Phase 5 includes:** Sampling contract (`create_message` + `ModelPreferences`),
MCP prompt-driven instructions, translation confidence, primary/fallback languages,
abbrev + med canonicalize + ICD-10 post-pass, A2A AgentCard + auth.

**Next up:** Phase 6 — Rules Engine, EHR Validation Tool, Clinical Insight Reporter.

## Layout (summary)

| Folder | Role |
| --- | --- |
| `agents/` | Monitor ✅ · Extractor ✅ · Normalizer ✅ · Validator / Summary (stubs) |
| `mcp_servers/` | Primary `:8200` (resources, prompts, watcher, harvester, lang-bridge ✅) · Secondary stub |
| `shared/` | settings, logger, language, clinical_normalize, llm, a2a helpers ✅ |
| `mock_ehr/` | FastAPI Mock EHR `:8050` ✅ |
| `configs/` | `rules.yaml`, prompts, agent/model/MCP config |
| `data/input/` | MCP Roots workspace (sample corpus) |
| `rag/` · `host/` · `dashboard/` | stubs (Phases 10–12) |
| `Documentation/` | Specs, seeds, coding style (not runtime) |

## Manually testing what's built

```bash
# Mock EHR
curl http://127.0.0.1:8050/health
curl http://127.0.0.1:8050/patients/P1019

# Primary MCP — FastMCP Inspector / CLI
uv run fastmcp dev mcp_servers/primary/server.py

# Extractor (needs Primary MCP + Bedrock for unstructured docs)
uv run python -c "
import asyncio, json
from agents.extractor.graph import run_extraction
print(json.dumps(asyncio.run(run_extraction('P1019')), indent=2, ensure_ascii=False))
"

# Normalizer — Hindi sample via Sampling (needs Primary MCP + Bedrock)
uv run python -c "
import asyncio, json
from agents.extractor.graph import run_extraction
from agents.normalizer.graph import run_normalization
async def main():
    ext = await run_extraction('P1021')
    print(json.dumps(await run_normalization('P1021', ext), indent=2, ensure_ascii=False))
asyncio.run(main())
"

# A2A AgentCards (public; services must be running)
curl http://127.0.0.1:8103/.well-known/agent.json
curl http://127.0.0.1:8100/.well-known/agent.json
curl http://127.0.0.1:8102/.well-known/agent.json
```
