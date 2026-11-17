# ORCHESTRA AI

Multi-agent autonomous workflow platform (MVP). One objective in; a planned, executed, QA-reviewed deliverable out, with human approval gates.

## Run it

Backend (FastAPI, port 8000):

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

Frontend (Next.js, port 3000):

```bash
cd frontend && npm install && npm run dev
```

Tests: `cd backend && .venv/bin/python -m pytest`

## Configuration (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Without it, agents are simulated (mock provider, labelled in the UI) |
| `ORCHESTRA_MODEL` | `claude-fable-5-2` | Reasoning model |
| `ORCHESTRA_LLM` | `auto` | `auto`, `anthropic` or `mock` |
| `DATABASE_URL` | SQLite file | Set a `postgresql+psycopg://` URL for PostgreSQL |
| `ORCHESTRA_DEFAULT_BUDGET_USD` | `20` | Per-project model budget |
| `SECRET_KEY` | generated | JWT signing key; set it in production |

## Layout

- `backend/app/orchestrator.py`: planning, dependency-aware concurrent execution, QA and retry, escalation, approvals, final consolidation
- `backend/app/agents.py`: the five agents (Research, Planning, Execution, QA, Supervisor)
- `backend/app/llm.py`: model abstraction (Anthropic and mock providers)
- `backend/app/main.py`: HTTP API, live event stream, exports
- `frontend/`: dashboard, project workspace, workflow graph, agent monitor

## Not in this MVP yet

Redis and a separate job queue (workflows run in-process), vector retrieval, file uploads, third-party integrations, custom agents, templates.
