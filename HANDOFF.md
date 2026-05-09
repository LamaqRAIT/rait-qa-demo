# Demo Handoff — Vedant

**Date:** 2026-05-09  
**From:** Lamaq  
**Repo:** `LamaqRAIT/rait-qa-demo` (GitHub)  
**Status:** Phase 1 infrastructure deployed. Backend building. Env vars not yet set.

---

## What Has Been Built

### 1. Demo Site — GitHub Pages
**Live at:** `https://lamaqrait.github.io/rait-qa-demo/`

Four static HTML pages that act as the fake e-commerce target:

| File | Purpose |
|---|---|
| `demo-site/login.html` | Login form. Flow 3 target — has JS that redirects to `products.html` instead of `dashboard.html` when `localStorage.auth_drift = true` |
| `demo-site/products.html` | Product listing page |
| `demo-site/checkout.html` | **Primary test target (normal state)** — button has `class="btn btn-checkout"` and text "Submit Order" |
| `demo-site/dashboard.html` | Post-login dashboard |
| `demo-site/checkout-drift-class.html` | Flow 1 trigger — class changed to `btn-place-order` |
| `demo-site/checkout-drift-text.html` | Flow 2 trigger — button text changed to "Place Order" |

**The drift pages are pre-baked variants.** To trigger a demo flow, the tests just report failures consistent with those pages — the actual switching is handled in the backend's mock data (see below).

**How GitHub Pages deploys:** `.github/workflows/deploy-demo-site.yml` — auto-triggers on any push that touches `demo-site/**`. Source is set to "GitHub Actions" in repo settings.

---

### 2. Backend — Railway
**Service name:** `rait-qa-demo` (the `elegant-amazement` Railway project)  
**Dockerfile:** `backend/Dockerfile` — `python:3.12-slim`, no Playwright/Chromium in this build  
**Root directory in Railway settings:** `backend/`

**FastAPI endpoints:**

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check |
| `POST /webhook/manual?scenario=flow1` | Trigger a demo run. `scenario` = `flow1`, `flow2`, or `flow3` |
| `POST /webhook` | GitHub webhook (not yet wired — Phase 2) |
| `GET /runs` | List last 20 runs |
| `GET /runs/{run_id}/stream` | SSE stream — push to `EventSource` in UI |
| `POST /approve/{run_id}` | Human approval endpoint — resumes LangGraph interrupt |
| `GET /git/log` | Returns last 5 commits (for UI git log panel) |

**LangGraph pipeline nodes (in order):**
```
change_analyzer → test_runner → browser_inspector → classifier
    → [auto_fixer | ticket_creator] → reporter
```

**Phase 1 stubs:** `test_runner_node` and `browser_inspector_node` return mock data keyed by `scenario`. The **classifier still calls Gemini/Groq for real** — the AI triage is genuine, fed consistent mock inputs.

Mock data lives in `backend/app/agents/nodes.py` at the top (`_MOCK_FAILURES`, `_MOCK_DOM_REPORTS`).

---

### 3. Agent UI — Railway
**Service name:** separate Railway service in the `proactive-perception` project  
**Next.js 15.1.11, React 19, ReactFlow, Tailwind**  
**Root directory in Railway settings:** `agent-ui/`

Key components:
- `src/app/page.tsx` — main dashboard, SSE client, run state
- `src/components/NodeGraph.tsx` — ReactFlow pipeline visualisation, 9 nodes
- `src/components/ApprovalQueue.tsx` — confidence bar, selector diff, approve/reject
- `tailwind.config.ts` — full design system: pastel palette + shimmer animations

---

## Current Deployment State

| Component | Status | Blocker |
|---|---|---|
| Demo site | **Live** | — |
| Agent UI | **Deployed, Online** | `NEXT_PUBLIC_API_URL` not set — all API calls hit `undefined` |
| Backend | **Building** (latest: `dff9182`) | Env vars not set; need to complete first successful deploy |

---

## What You Need To Do First (In Order)

### Step 1 — Verify backend deployed

Go to Railway → `elegant-amazement` project → `rait-qa-demo` service → Deployments tab.  
If it shows "Active / Deployment successful" for commit `dff9182`, it's up.  
Copy the public URL (e.g. `https://rait-qa-demo-production.up.railway.app`).

### Step 2 — Add env vars to the backend

In Railway → backend service → Variables tab, add:

```
GOOGLE_API_KEY=<Gemini key from aistudio.google.com>
GROQ_API_KEY=<Groq key from console.groq.com>
BASE_URL=https://lamaqrait.github.io/rait-qa-demo
AGENT_UI_URL=<agent-ui railway URL — see step 3>
DATABASE_URL=   ← leave blank for SQLite (fine for demo), or add Railway Postgres URL
```

### Step 3 — Expose the agent-ui and set its env var

In Railway → agent-ui service → Settings → Networking → Generate Domain.  
Copy the `*.up.railway.app` URL.  
Add it as `AGENT_UI_URL` in the backend (Step 2).

Then in Railway → agent-ui service → Variables:
```
NEXT_PUBLIC_API_URL=<backend railway URL from Step 1>
```

Both services will redeploy automatically.

### Step 4 — Test the health check

```bash
curl https://<backend-url>/health
# Expected: {"status":"ok","timestamp":"..."}
```

### Step 5 — Trigger Flow 1 manually

```bash
curl -X POST "https://<backend-url>/webhook/manual?scenario=flow1"
# Returns: {"run_id":"...","status":"started","scenario":"flow1"}
```

Open the Agent UI in the browser. Select the run. You should see the node graph animating through the pipeline with real-time SSE updates.

Expected outcome for Flow 1:
- `test_runner` → FAILED (selector `.btn-checkout` not found)
- `browser_inspector` → found `.btn-place-order`
- `classifier` → DRIFT, confidence ~0.90 (real LLM call)
- `auto_fixer` → SKIPPED (mock — no real file to patch in Phase 1)
- `reporter` → complete

### Step 6 — Test Flow 2 (human approval)

```bash
curl -X POST "https://<backend-url>/webhook/manual?scenario=flow2"
```

Classifier should return DRIFT with lower confidence (~0.60) and pause at `human_review`. The ApprovalQueue component in the UI will show the proposed fix diff. Enter a reviewer name, click Approve, watch the pipeline resume.

### Step 7 — Test Flow 3 (bug → ticket)

```bash
curl -X POST "https://<backend-url>/webhook/manual?scenario=flow3"
```

Classifier should return BUG. `auto_fixer` shows SKIPPED with strikethrough. `ticket_creator` runs and logs a ticket ID.

---

## Known Issues / Current Gaps

| Issue | Impact | Fix |
|---|---|---|
| `NEXT_PUBLIC_API_URL` not set | Agent UI shows no data | Step 3 above |
| No GitHub webhook configured | Can't demo "push triggers agent" | Phase 2 — wire `POST /webhook` in GitHub repo settings |
| `auto_fixer_node` doesn't write a real git commit | Flow 1 shows "fix applied" but no actual commit | Phase 2 — implement GitPython commit to `qa-agent/auto-heal` branch |
| `browser_inspector_node` is mocked | DOM inspection is simulated | Phase 2 — re-add `browser-use` + `playwright` to requirements once base layer is cached |
| No PostgreSQL — SQLite only | Fine for demo, resets on redeploy | Add Railway Postgres add-on if run history needs to persist across deploys |
| `QAState` missing `scenario` in webhook (non-manual) | GitHub webhook trigger won't pass scenario | Fine for Phase 1 — webhook isn't live yet |

---

## File Map — What Lives Where

```
claude-qa-agent/demo/
├── .github/
│   └── workflows/
│       └── deploy-demo-site.yml        ← GitHub Pages auto-deploy
├── demo-site/
│   ├── index.html                      ← redirects to login.html
│   ├── login.html                      ← Flow 3 target
│   ├── products.html
│   ├── checkout.html                   ← NORMAL state (test target)
│   ├── checkout-drift-class.html       ← Flow 1 drift variant
│   ├── checkout-drift-text.html        ← Flow 2 drift variant
│   ├── dashboard.html
│   └── styles.css                      ← shared pastel design system
├── backend/
│   ├── Dockerfile                      ← python:3.12-slim, no Chromium
│   ├── railway.toml                    ← builder=dockerfile
│   ├── requirements.txt                ← 14 packages, no playwright/browser-use
│   ├── .env.example                    ← copy to .env for local dev
│   └── app/
│       ├── main.py                     ← FastAPI, all endpoints
│       ├── config.py                   ← settings + CORS origins
│       ├── models.py                   ← QARun, ApprovalRequest, NodeUpdate
│       ├── db.py                       ← SQLAlchemy async, SQLite/Postgres
│       └── agents/
│           ├── state.py                ← QAState TypedDict (includes scenario field)
│           ├── nodes.py                ← 7 node functions + mock data dicts
│           └── pipeline.py             ← LangGraph StateGraph, conditional routing
└── agent-ui/
    ├── package.json                    ← next@15.1.11, reactflow, lucide-react
    ├── tailwind.config.ts              ← design tokens, shimmer animations
    └── src/
        ├── app/page.tsx                ← main dashboard
        └── components/
            ├── NodeGraph.tsx           ← ReactFlow pipeline viz
            └── ApprovalQueue.tsx       ← HITL approval UI
```

---

## Phase 2 Checklist (After Phase 1 is Verified Working)

- [ ] Wire GitHub webhook: repo Settings → Webhooks → `POST https://<backend>/webhook` → push events
- [ ] Re-add `playwright==1.49.0` and `browser-use>=0.1.40` to `requirements.txt`
- [ ] Implement real browser-use call in `browser_inspector_node` (currently mocked)
- [ ] Implement real git commit in `auto_fixer_node` using GitPython (commit to `qa-agent/auto-heal` branch)
- [ ] Open PR via PyGithub after auto-fix commit
- [ ] Add Railway Postgres add-on and set `DATABASE_URL` for persistent run history
- [ ] Freeze requirements to exact `==` versions (run `pip freeze` after successful deploy)

---

## Reference Docs

| Doc | Location | What It Contains |
|---|---|---|
| Full demo plan | `claude-qa-agent/references/DEMO_PLAN.md` | Architecture decisions, 3 flow specs, design system, SSE pattern, HITL pattern |
| Production architecture | `claude-qa-agent/references/RAIT_QA_Production_Architecture.drawio` | Drawio diagram |
| Week 1 POA | `claude-qa-agent/references/week1-poa.md` | Deliverables and milestones |
| Env var template | `claude-qa-agent/demo/backend/.env.example` | All env vars with descriptions |
