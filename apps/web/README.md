# HELM Web UI Integration Guide

This directory documents the integration contracts and endpoints for connecting the Next.js frontend to the HELM API control plane.

## 1. Environment Setup

Configure your Next.js application with:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

In production (Vercel):

```env
NEXT_PUBLIC_API_URL=https://api.your-helm-domain.com
```

---

## 2. API Endpoints

### Projects
* `POST /api/projects`: Register a repository
* `GET /api/projects`: List all connected projects
* `GET /api/projects/{id}`: Project status and metadata
* `GET /api/projects/{id}/graph`: Code graph nodes and relationship edges
* `GET /api/projects/{id}/symbols`: Repository symbol index

### Tasks & Execution
* `POST /api/tasks`: Dispatch an autonomous engineering task (`{ project_id, prompt, mode: "autonomous" | "guided" | "assist" }`)
* `GET /api/tasks/{id}`: Task progress, status, changed files, test counts
* `POST /api/tasks/{id}/cancel`: Abort task

### Live Streaming (SSE)
* `GET /api/tasks/{id}/events`: Server-Sent Events stream of real-time operational events (`run_started`, `phase_started`, `tool_started`, `file_modified`, `test_completed`, `approval_required`, `run_completed`, etc.)

### Changes & Diff
* `GET /api/tasks/{id}/changes`: Structured list of modified files, lines added/removed, and file diffs
* `GET /api/tasks/{id}/diff`: Raw unified git diff string

### Approvals (Human-in-the-Loop)
* `GET /api/approvals`: List pending approval requests
* `POST /api/approvals/{id}/approve`: Approve gated action (e.g. Git push / PR creation)
* `POST /api/approvals/{id}/reject`: Reject gated action
