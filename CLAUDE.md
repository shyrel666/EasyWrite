# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

EasyWrite: an AI copilot for writing Chinese government/enterprise technical bid documents (技术标书). FastAPI backend + Vue 3 SPA. Code comments, docstrings, UI strings, and docs are in Chinese — keep new ones in Chinese to match. `README.md` (usage, API table), `walkthrough.md` (design rationale), and `implementation_plan.md` (v0.2.0 refactor log) are the reference docs.

## Commands

```bash
# Backend (run from backend/ — .env is resolved relative to CWD; data paths are absolute)
pip install -r backend/requirements.txt
cd backend && python -m uvicorn app.main:app --port 8000 --reload   # UI at /, API docs at /docs

# Frontend
cd frontend && npm install
npm run dev     # :5173, proxies /api -> 127.0.0.1:8000 (backend must be running)
npm run build   # outputs to backend/app/static/ (committed; FastAPI serves it in production)

# Tests (run from repo root; conftest inserts backend/ into sys.path)
python -m pytest tests/ -q
python -m pytest tests/test_api.py::test_full_lifecycle -v
```

No linter/formatter is configured. After changing `frontend/src`, run `npm run build` so the committed `backend/app/static/` bundle stays in sync.

`tests/conftest.py` sets `DATA_DIR`/`DB_PATH`/other path env vars to a temp dir and blanks `LLM_API_KEY`/`EMBEDDING_API_KEY` *before* importing the app, so tests never touch `backend/data/` and always run in offline/mock mode, asserting deterministic rule-based fallbacks. Settings, the DB engine and the JSON-backed managers resolve paths at import time — any new runtime path must derive from `settings` so this isolation keeps working (`tests/test_runtime_isolation.py` checks it).

Don't put CJK characters in `strftime()` format strings — it raises `UnicodeEncodeError` on Windows; build Chinese date strings with f-strings.

## Architecture

### Backend layout (`backend/app/`)
- `main.py` — mounts 11 routers from `api/` under `/api/v1`, serves `static/` SPA with a history-mode fallback (`/{full_path}` returns `index.html` for `text/html` requests).
- `api/` — thin routers per domain; business logic lives in `services/`.
- `core/` — module-level singletons imported everywhere: `llm_client`, `embedding_client`, `ai_settings_manager`, `task_manager`, `settings`.
- `db/` — SQLModel + SQLite (WAL) at `backend/data/easywrite.db`. `init_db()` runs at import of `db/database.py`. Tables: `projects`, `kb_documents`, `kb_chunks` (embeddings as float32 BLOB + `embedding_version` fingerprint), `kb_items`. Nested structures (outline tree, facts, deviation matrix, tender analysis) are JSON text columns, always loaded/saved whole.
- `services/project_store.py` — the only path to project persistence; converts `ProjectModel` rows ↔ Pydantic `Project` (in `models/schemas.py`). Pattern: `project_store.get(id)` → mutate the Pydantic object → `project_store.save(project)`.
- Some state is JSON files, not SQLite: `data/ai_settings.json`, `data/templates.json`, `data/enterprise_assets.json` (`ai_settings.json` holds API keys in plaintext and is git-ignored, created on first save; the other two are git-tracked seed data).

### LLM configuration and "honest mode" signalling
All LLM calls go through `core/llm_client.py` (OpenAI-compatible; sync + async clients). Config comes from `ai_settings_manager` (UI-editable, hot-reloaded via `llm_client.reload_config()`), falling back to env vars in `core/config.py`. Embeddings are configured separately (`embedding_client`) and are optional.

Core rule across the codebase: **never fabricate data when the LLM is unavailable**.
- `chat_completion*` falls back to `_mock_bid_generation` and sets mode `"mock"`; `llm_client.get_mode()` must be propagated in API responses as `mode` so the frontend shows the offline banner.
- `chat_completion_structured()` returns `None` when unconfigured — callers must take an explicit degraded path (rules / sentinel values), not invent content.
- Missing tender fields use the sentinel `NOT_MENTIONED = "未提及"` (`schemas.py`). Deviation responses stay `"待生成"` unless `get_mode() == "llm"`. Rule-based paths report `extraction_mode`/`mode: "rules"`.

### Long-running work
- Background jobs (KB ingestion, tender analysis upload, compliance check, batch deviation responses) use `task_manager.submit(type, fn)`; `fn(ctx)` reports via `ctx.report(progress, msg)` and can check `ctx.cancelled()`. Frontend polls `GET /api/v1/tasks/{id}` (`pollTask` in `frontend/src/api/client.js`). Task records are in-memory only.
- Section writing streams over SSE (`api/sections.py`): first event carries retrieval refs/mode, then `{token}` events, then `{done}`; content is persisted to the outline node at stream end. Uses `AsyncOpenAI` so the event loop isn't blocked.

### RAG pipeline (`services/rag/`)
Ingest (`ingestor.py`): docx parse (`parser/word_parser.py`) → `chunker` (heading-aware, tables as own chunks, small-chunk merging only within the same subsection to preserve breadcrumbs) → `enricher` (doc name + breadcrumb prefix, optional LLM summary) → `indexer` (BM25 via jieba + optional dense vectors) → optional `curator` (LLM knowledge items).

Retrieve (`retriever.py`): multi-query → BM25 ∥ dense recall → RRF fusion (rank-based, `RAG_RRF_K`) → LLM listwise rerank (0–10) → threshold `RAG_RERANK_THRESHOLD` (below it, return "no confident reference" rather than inject weak matches) → parent context expansion. Per-section `pinned_refs`/`excluded_refs` on `OutlineNode` force-include/exclude chunks. `knowledge_index` keeps BM25/dense indexes in memory, rebuilt from SQLite. Tuning knobs are in `core/config.py`.

### Bid workflow (project `stage`: created → tender_analyzed → outline_confirmed → writing)
1. `parser/tender_analyzer.py` — 18-field tender extraction, LLM-first with regex supplement.
2. `generator/outline_generator.py` — level-1 draft aligned to scoring items → human confirmation gate → expand full tree with per-leaf word budgets.
3. `generator/section_generator.py` — prompt = global facts (hard constraints) + retrieved refs + sibling-section context + enterprise assets.
4. `parser/deviation_engine.py` — deterministic requirement extraction (★ items first), LLM point-by-point responses.
5. `checker/compliance_checker.py` (multi-round LLM evidence check, keyword scan fallback) and `checker/quality_inspector.py` (8-dimension rule checks, polish).
6. `exporter/docx_generator.py` — python-docx with real Heading styles, Word TOC/page-number fields, eastAsia fonts; `diagram_renderer.py` renders Mermaid via a simplified matplotlib implementation (unsupported types fall back to a code slot).

### Frontend (`frontend/src/`)
Vue 3 + Pinia (`stores/project.js`, `stores/ai.js`) + vue-router (history mode; project views under `/project/:id/...`) + Element Plus + Tailwind. All HTTP goes through `api/client.js` (`request`, `uploadFile`, `streamSSE`, `pollTask`). No CDN dependencies — keep assets local.
