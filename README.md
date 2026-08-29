# HELM: Autonomous Software Engineering Agent

HELM is a production-grade autonomous software engineering runtime platform.

It is capable of taking natural-language software tasks against Git repositories and autonomously:
1. Understanding repository architecture and symbols via multi-language AST parsing (Python, TS/JS, Go).
2. Building an in-memory and relational Code Graph with caller/callee hierarchy and dependency tracing.
3. Conducting scoped hybrid retrieval (keywords, symbols, graph traversal, and Git commit history).
4. Generating structured implementation plans.
5. Performing controlled surgical edits inside an isolated Docker sandbox (with local process fallback).
6. Running tests, linters, type checks, and build steps.
7. Diagnosing failures and executing an autonomous self-correction loop.
8. Performing an independent diff review pass.
9. Enforcing security policies and requesting human approval for gated actions (such as pushing branches or creating PRs).

---

## Architecture Overview

```text
                    HELM
                     │
               Control Plane (FastAPI)
                     │
              Agent Orchestrator
                     │
             Modified Hermes Runtime
                     │
              DeepSeek V4 Flash
                     │
        ┌────────────┼────────────┐
        │            │            │
  Context Engine Tool Runtime   Memory
  (Code Graph)   (Sandbox)   (Project State)
        │            │
        └────────────┼────────────┘
                     │
               Policy Engine
                     │
          ┌──────────┴──────────┐
          │                     │
       Automatic             Approval
          │                     │
          └──────────┬──────────┘
                     │
                   GitHub
```

---

## Local Development Quickstart

### Prerequisites
* Python 3.11+
* Git
* Docker (optional - sandbox automatically uses LocalProcessExecutor if Docker daemon is inactive)
* Redis (optional - falls back to high-performance async in-memory bus)

### Setup & Health Check

```bash
# 1. Check system readiness
python scripts/doctor.py

# 2. Run test suites
pytest tests -v

# 3. Start local development API server
uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## API Endpoints

* `GET /health`: Health check
* `POST /api/projects`: Register a repository
* `GET /api/projects`: List projects
* `GET /api/projects/{id}/graph`: Query repository code graph
* `POST /api/tasks`: Launch an autonomous SWE task
* `GET /api/tasks/{id}`: Query task progress & status
* `GET /api/tasks/{id}/events`: Stream real-time operational events (SSE)
* `GET /api/tasks/{id}/changes`: Structured diff and files changed
* `GET /api/approvals`: List pending approvals
* `POST /api/approvals/{id}/approve`: Approve gated action
* `POST /api/approvals/{id}/reject`: Reject gated action
