# RAIT QA Agent — Complete Architecture Guide

> **Purpose:** A self-contained reference covering every architectural concept in the RAIT QA Agent system. Read it top-to-bottom for first-time understanding, or jump to any section for revision.

---

## Stack at a Glance

```
Browser (Engineer / QA / Manager)
  └── Next.js 15 (Railway)        — Frontend dashboard, approval queue, metrics
        │  REST + SSE / JWT Bearer
        ▼
  FastAPI (Railway)               — 6-node asyncio pipeline, all business logic
        ├── PostgreSQL (Railway)  — State: runs, users, tickets, notifications, events
        ├── Groq API              — LLM triage (llama-3.3-70b-versatile)
        ├── Deterministic Engine  — Zero-cost rule-based fallback for triage
        ├── GitHub API            — Inject drifts (Pages) + open auto-fix PRs
        ├── Langfuse SDK          — LLM observability, cost tracking, trace logging
        └── Slack/Jira Webhooks  — Real-time team notifications and ticket creation

Demo Site (GitHub Pages)          — The "production app" Playwright tests run against
```

---

## Part 1 — Core Concepts

### 1.1 What is the DOM?

When your browser loads a website, it receives a plain text HTML file. The browser reads that text and builds a **live, interactive tree of objects in memory**. This tree is called the **DOM (Document Object Model)**.

Think of HTML as a blueprint and the DOM as the actual building. Every button, input field, heading, and link on a page is a node in this tree.

```
Document
└── html
    └── body
        └── div.product-card
            ├── p  ("Blue T-Shirt — $25")
            └── button.btn-checkout  ("Buy Now")
```

**Why it matters for us:** Playwright locates elements by their DOM address — their CSS class, ID, or text content. If a developer renames `btn-checkout` to `btn-place-order`, that DOM address is gone. Playwright's test fails. That failure is our detection signal.

JavaScript can also read and modify the DOM in real-time. Every cart update, modal popup, and dynamic price change is JavaScript modifying the DOM — not reloading the page.

---

### 1.2 What is Playwright?

Playwright is a Python library (also available in JavaScript) that controls a real web browser (Chrome/Chromium) programmatically. It can open URLs, click buttons, fill forms, read text, take screenshots, and assert that things look a certain way — exactly like a human user, but automated.

**The critical property of Playwright:** Every action is pre-written in a script. Playwright follows instructions blindly without understanding the page.

```python
# A Playwright test — deterministic, pre-written, sequential
def test_checkout_button(page):
    page.goto("https://example.com/checkout")
    page.click(".btn-checkout")                     # Must find .btn-checkout exactly
    assert page.url == "https://example.com/confirmed"
```

If `.btn-checkout` is renamed, Playwright throws `ElementNotFound` and the test fails. **This brittleness is intentional.** You want tests to break when things change — that's the detection mechanism.

**In our system, Playwright is used in two modes:**

1. **Node ③ (Test Runner):** Executes pre-written test scripts. Any selector change breaks the test → failure is logged.
2. **Node ④ (DOM Inspector):** Reads the live DOM of the failing page to extract all current elements for comparison. Here Playwright acts as a DOM reader, not a test executor.

---

### 1.3 What is BrowserUse?

BrowserUse is an AI-driven browser control library. Instead of following a pre-written script, it uses an LLM to decide what to do at each step, based on what it sees on the current page.

At every step, BrowserUse:
1. Takes a screenshot (and/or reads the DOM)
2. Sends it to an LLM with the goal: *"Complete a checkout"*
3. LLM says: *"I see a blue button that says 'Place Order'. Click it."*
4. BrowserUse clicks it
5. Repeat until goal achieved or LLM gives up

**Why BrowserUse never fails on CSS renames:** If `.btn-checkout` became `.btn-place-order`, BrowserUse doesn't care. The LLM reads the page visually: *"There is a button that says 'Place Order' in the checkout context. That is the checkout button."* It clicks it. Task succeeds. **The drift is completely invisible.**

This is why BrowserUse cannot replace Playwright for QA testing. Its intelligence actively hides the thing we need to detect.

---

### 1.4 Playwright vs BrowserUse: The Full Comparison

| Factor | Playwright | BrowserUse |
|---|---|---|
| **Decision-maker** | Pre-written script | LLM at runtime |
| **Cost per test** | ~$0 | $0.01–0.30 (LLM calls/action) |
| **Speed** | 2–5 sec/test | 30–120 sec/test |
| **Deterministic** | Yes — same input = same path | No — LLM varies |
| **Fails on UI change** | ✅ Yes — this is the point | ❌ No — adapts silently |
| **Right for** | Regression detection | Task automation, RPA |
| **Test suites** | Natural fit | Counterproductive |

**The key insight:** Playwright's brittleness is a feature for QA. BrowserUse's intelligence is a feature for automation. They are not competitors — they serve different purposes. Our QA Agent uses **Playwright to detect** and **the LLM to reason about what it detected**.

**Scale argument against BrowserUse for testing:** Google runs ~4.2 million tests per day. At $0.01 per BrowserUse task, that would cost $42,000/day just for test execution. Playwright costs zero per run.

---

## Part 2 — The 6-Node Pipeline

The pipeline runs as a sequential asyncio process inside FastAPI. Results stream to the frontend via Server-Sent Events (SSE) in real time — each node lights up on the dashboard NodeGraph as it completes.

### 2.1 Node ① — Canary Check

**What it does:** Sends a single HTTP HEAD request to the demo site's root URL. Takes ~200ms.

**Why it exists:** Before running 28 Playwright tests (3–4 minutes of work), verify the site is alive. If the site is down, all 28 tests will fail — but it's not a code bug or drift. Without the canary, the LLM would receive 28 failures, waste time triaging them, and potentially file wrong bug tickets or open incorrect PRs.

```
HEAD https://lamaqrait.github.io/rait-qa-demo/
  → 200: site alive → continue to Node ②
  → 4xx/5xx: site down → run.status = 'env_error', pipeline stops
```

**Real-world equivalent:** "Smoke tests" — Google, Netflix, and every serious engineering team run a quick health check before executing any deeper test suite.

---

### 2.2 Node ② — Smart Suite Selection

**What it does:** Decides which of the 8 test suites to run, rather than running all 28+ tests every time.

**Path A — Deterministic (preferred, zero cost):**

At startup and every 12 hours, the backend parses all test files and builds an in-memory **Selector Index**:
```
{
  "btn-checkout":       ["test_checkout.py", "test_navigation.py"],
  "search-input":       ["test_search.py"],
  "reg-email":          ["test_register.py"],
  "login-form":         ["test_login.py", "test_register.py"],
  ...
}
```

When a commit message contains `"renamed btn-checkout to btn-place-order"`, the engine:
1. Extracts identifiers: `btn-checkout`, `btn-place-order`
2. Looks up the Selector Index
3. Returns: `["test_checkout.py", "test_navigation.py"]`
4. Only 8 tests run instead of 28

**Path B — LLM Fallback (when deterministic fails):**

Commit messages like `"fix: various improvements"` or `"WIP"` yield no selector identifiers. The LLM receives the commit message + list of all test files with descriptions and picks the most likely 2–3 suites.

**Path C — Fallback All:**

If the LLM is also uncertain, `method = "fallback_all"` and all 8 suites run. This also triggers a HITL condition (see Part 6).

**Output stored in DB:** `suite_selection_method` ∈ {`deterministic`, `llm`, `fallback_all`}

---

### 2.3 Node ③ — Test Runner

**What it does:** Executes `pytest-playwright` with a headless Chromium browser against the live GitHub Pages demo site. Produces JUnit XML output.

```
pytest test_checkout.py test_navigation.py
  --browser chromium
  --base-url https://lamaqrait.github.io/rait-qa-demo/
```

**If all selected tests pass:**
- `run.status = 'success'`
- Pipeline stops here (no triage needed)
- Report generated, metrics updated

**If any test fails:**
- Failure list collected: test name, error message, selector that failed, stack trace
- Pipeline continues to Node ④

---

### 2.4 Node ④ — DOM Inspector

**What it does:** Playwright opens each *failing page* (not running a test script — just crawling the DOM) and extracts every interactive element currently on it.

```python
page.goto("https://lamaqrait.github.io/rait-qa-demo/checkout.html")
elements = page.query_selector_all("button, a, input, select, [role='button']")

# For each element, record:
{
  "tag": "button",
  "classes": ["btn-primary", "btn-place-order"],
  "id": null,
  "text": "Place Order",
  "position": {"x": 450, "y": 380, "width": 200, "height": 40}
}
```

**Fuzzy matching:** Each current element is scored against the broken selector using string similarity:
- `"btn-place-order"` vs `"btn-checkout"` → shares `"btn-"` prefix, same element type, same position → score **0.87**
- `"btn-cancel"` vs `"btn-checkout"` → only shares `"btn-"` → score **0.31**

Candidates above 0.7 score are passed to the LLM as evidence.

**Why this node exists:** Without DOM inspection, the LLM only knows "test failed." With DOM inspection, the LLM knows "test expected `.btn-checkout` but the page now has `.btn-place-order` at the exact same location." This is the difference between a confident triage and a guess.

---

### 2.5 Node ⑤ — LLM Triage

**What it does:** Sends the failure list + DOM candidates + recent Git commit messages to an LLM and asks for a classification.

**LLM chain (failover order):**
1. **Groq** (llama-3.3-70b-versatile) — primary, fast, low cost
2. **Deterministic rule engine** — pure Python, zero API calls, always available

If Groq fails (API down, rate limited), the deterministic engine uses DOM Inspector confidence scores and commit message keyword matching to produce a classification. This means triage always completes.

**LLM input (simplified):**
```
Failed tests: test_checkout_button_works
Failed selector: .btn-checkout
Error: ElementNotFoundError

DOM candidates on checkout.html:
  - .btn-place-order (score: 0.87, text: "Place Order", same position)
  - .btn-cancel (score: 0.31)

Recent commit: "chore: renamed checkout button CSS class for design system compliance"
```

**LLM output:**
```json
{
  "classification": "drift",
  "confidence": 0.93,
  "proposed_fix": "Replace .btn-checkout with .btn-place-order in test file",
  "evidence": "Commit message explicitly mentions CSS class rename. DOM shows .btn-place-order at identical coordinates.",
  "needs_human_review": false
}
```

**Classification types:**
- `drift` — UI selector/text changed, not a code bug. Fix the test.
- `bug` — Real regression in application behaviour. Fix the code.
- `env` — Infrastructure issue (site down, CDN stale). Not a code problem.
- `flaky` — Test is unreliable due to timing, not a code change.

**Temperature:** Always `temperature=0` for maximum consistency. Same inputs always produce the same classification.

---

### 2.6 Node ⑥ — Auto-Fixer / Reporter

**Three paths based on Node ⑤ output:**

**Path A — Auto-Fix (classification = `drift` AND confidence ≥ 0.80):**
1. Creates branch `qa-agent/auto-heal` in GitHub
2. Applies the proposed selector fix directly in the test file
3. Opens a Pull Request with: full diff, evidence text, confidence score, run ID
4. Fires Slack webhook: *"PR #N opened by QA Agent — drift fixed (93% conf)"*
5. Stores in-app notification
6. `run.status = 'complete'`

**Path B — Bug Ticket (classification = `bug`):**
1. Creates ticket in local DB (mirroring Jira structure): title, severity, description with AI evidence
2. Calls Jira Cloud API (if configured)
3. Fires Slack webhook: *"🚨 HIGH severity bug — run ID abc123"*
4. `run.status = 'failed'` (the site has a real bug — the pipeline correctly identified it)

**Path C — Human Review (confidence < 0.80 OR any HITL condition triggered):**
1. `run.status = 'awaiting_human'`
2. Approval Queue populated in the dashboard (visible to QA Manager and above)
3. Slack DM to QA Manager: *"⏳ Human review needed — run def456, confidence 72%"*
4. Pipeline **pauses** — no auto-fix, no ticket, nothing until human acts
5. On **Approve** → auto-fix runs (same as Path A)
6. On **Reject** → logged as rejected, run closed

**In all paths — Reporter:**
The LLM generates a human-readable incident report stored in `qa_runs.report`:
> *"4 tests failed on checkout.html. Analysis indicates CSS class rename from .btn-checkout to .btn-place-order in commit abc1234. DOM inspection confirms the element exists with new class at coordinates (450, 380). Proposed fix has been applied in PR #14."*

---

## Part 3 — What Can Change in a Commit

### Categories We Handle

**A — UI Drift (the primary case):**
| Change | Example |
|---|---|
| CSS class rename | `.btn-checkout` → `.btn-place-order` |
| HTML element ID rename | `#search-input` → `#q` |
| Button text change | "Submit Order" → "Place Order" |
| Form field rename | `name="reg-email"` → `name="email"` |
| URL/route change | `/checkout` → `/cart/checkout` |

Classification: `drift`. Action: auto-fix PR.

**B — Logic/Business Bugs:**
| Change | Example |
|---|---|
| Wrong redirect | After login → `/products` instead of `/dashboard` |
| Broken calculation | Cart total shows $0 |
| Missing auth guard | Protected page accessible without login |
| API returning wrong data | Price shows `null` |
| Dropped safeguard | Rate limiter removed in refactor |

Classification: `bug`. Action: ticket filed, Slack alert.

**C — Environment Issues:**
| Issue | Example |
|---|---|
| Server down | Site returns 503 |
| CDN stale | Old JS bundle being served |
| SSL cert expired | Browser blocks the site |

Classification: `env`. Canary catches most of these before tests even run.

**D — Flaky Tests:**
| Cause | Example |
|---|---|
| Race condition | Test clicks before animation finishes |
| Timing issue | Element not yet in DOM when assertion runs |
| Third-party slowness | Payment widget loads slow |

Classification: `flaky`. Action: logged, no auto-fix (test needs improvement).

### What We Don't Handle (v1 Scope Limits)

- Mobile-specific layout changes (we only test desktop Chromium)
- Performance regressions (no load time measurement)
- A/B test variants
- API contract changes (no schema comparison)
- Visual pixel-level changes (no screenshot diffing)

These account for ~20% of real-world failures. The current system covers the other ~80%.

---

## Part 4 — Suite Selection in Detail

**The 8 test suites in the demo:**

| File | Tests | What It Covers |
|---|---|---|
| `test_navigation.py` | 4 | Page-to-page navigation, links |
| `test_checkout.py` | 4 | Checkout flow, button, confirmation |
| `test_login.py` | 3 | Login, wrong password, redirect |
| `test_cart.py` | 3 | Add to cart, total update |
| `test_search.py` | 3 | Search bar, results |
| `test_register.py` | 4 | New account creation |
| `test_product.py` | 3 | Product detail page |
| `test_account.py` | 4 | Account settings page |

**In production:** A real product would have 200–5,000 E2E tests across 50–200 suites. The architecture is identical — the Selector Index just gets larger.

**How the Selector Index is maintained:** `APScheduler` rebuilds it every 12 hours by parsing all test files. The index maps each selector string to the test files that use it. On commit trigger, a simple dictionary lookup determines affected suites in milliseconds.

**The method field matters for HITL:** If `suite_selection_method = "fallback_all"`, the pipeline had no idea what changed. This is one of the 5 HITL trigger conditions — running all suites signals ambiguity, and the agent's confidence in its triage may be lower as a result.

---

## Part 5 — Infrastructure Components

### 5.1 JWT Authentication

**What JWT is:** A JSON Web Token is a tamper-proof digital credential. When you log in, the server generates a token containing your identity and role, signs it cryptographically, and sends it to your browser. Every subsequent API call includes this token, and the server verifies the cryptographic signature — no database lookup needed.

**Structure of a JWT:**
```
Header:    { "alg": "HS256" }
Payload:   { "user_id": "abc", "role": "qa_manager", "exp": 1747123456 }
Signature: HMAC(header + payload, JWT_SECRET)  ← mathematically impossible to forge
```

**Why stateless auth matters:** Railway containers can restart at any time. Sessions stored in server memory would be lost. JWTs are self-contained — the server only needs to know `JWT_SECRET` to verify any token, with zero DB reads.

**Our 5 roles and what they can do:**
| Role | Permissions |
|---|---|
| `super_admin` | Full access: reset demo, manage circuit breakers, see all teams' runs |
| `qa_manager` | Approve/reject HITL requests, see all runs in their team |
| `qa_engineer` | Trigger demo flows, see their team's runs |
| `developer` | Read-only: view runs and tickets assigned to them |
| `system_agent` | Backend-to-backend calls only |

Every API endpoint checks the JWT role before responding. The frontend shows or hides UI components based on the role stored in localStorage.

---

### 5.2 GitHub Pages

**What it is:** GitHub Pages is a free static website hosting service built into GitHub. You push HTML/CSS/JS files to a repo, enable Pages, and GitHub serves them at `username.github.io/repo-name`. No server needed, always on, publicly accessible.

**Why we use it for the demo:** The Playwright tests run inside Railway's Docker container. They need to test against a publicly accessible URL. GitHub Pages gives us a live, stable URL that:
1. Railway can reach from its Docker container
2. We control (can inject drift by pushing HTML changes)
3. Costs nothing
4. Never goes down

**How drift injection works:**
1. User clicks "Flow 1 — CSS Drift" in dashboard
2. Frontend calls `POST /demo/inject-drift`
3. Backend uses GitHub API (PAT auth) to commit a change to `checkout.html`:
   - Changes `class="btn-checkout"` → `class="btn-place-order"`
4. GitHub Pages rebuilds and serves the updated file (~30 seconds)
5. Pipeline runs → Playwright tests fail → triage happens → PR opened

**In production:** GitHub Pages would be replaced by the company's actual deployed application URL. The QA Agent would be configured with that URL. Everything else stays the same.

---

### 5.3 PostgreSQL Tables

Five tables in the Railway Postgres instance:

| Table | What it stores |
|---|---|
| `qa_runs` | Every pipeline run: status, classification, confidence, cost, LLM report, Langfuse trace URL, suite selection method |
| `users` + `teams` | Auth: bcrypt-hashed passwords, roles, team membership |
| `tickets` | Bug tickets created by the agent: title, severity, evidence, run ID |
| `notifications` | In-app feed: PR opened, bug filed, HITL needed, circuit breaker tripped |
| `system_events` | Circuit breaker audit trail: what triggered, when, what threshold was applied |

The `qa_runs` table is the single source of truth for the entire pipeline. Every node writes to it (status updates), reads from it (resumability), and the metrics endpoint queries it for aggregations.

---

### 5.4 APScheduler

Two scheduled jobs:
1. **Nightly run** — `02:00 UTC` daily: triggers a full pipeline run automatically, no human needed
2. **Index rebuild** — Every 12 hours: rebuilds the Selector Index from all test files

Uses `SQLiteJobStore` for persistence — jobs survive container restarts.

---

## Part 6 — Safety Mechanisms

### 6.1 The Circuit Breaker (4 Mechanisms)

The circuit breaker is an automatic safety system that monitors pipeline health and restricts autonomous action when something is wrong.

**Mechanism 1 — Cost Guard**
```
Trigger:  Any single run costs > $0.50 in LLM API fees
Response: Force HITL for that run (do not auto-fix)
Reason:   A run costing 500x the normal $0.001 means something is wrong —
          LLM looping, huge context sent, API anomaly.
```

**Mechanism 2 — Error Rate Guard**
```
Trigger:  > 20% of recent runs ended in pipeline errors
Response: Raise auto-fix confidence threshold to 1.01 (unreachable)
          → All runs go to human review
Reason:   Systemic failures indicate a broken foundation.
          Don't keep auto-fixing when the infrastructure is compromised.
```

**Mechanism 3 — False Positive Rate (FPR) Guard**
```
Trigger:  > 15% of auto-fix PRs were rejected by engineers
Response: Dynamically raise confidence threshold
Reason:   If engineers keep rejecting the agent's PRs, it's wrong more often
          than it thinks. Reduce autonomy until accuracy recovers.
```

**Mechanism 4 — Confidence Drift Guard**
```
Trigger:  Average LLM confidence drops > 15 percentage points in 24 hours
          (e.g., was 90%, now 72%)
Response: Force HITL on all runs
Reason:   Sudden confidence drop = the LLM is seeing something new it
          can't reason about. Better to ask humans than to guess blindly.
```

The circuit breaker status is visible in the Metrics tab. The ⚡ "Reset Circuit Breakers" button in the sidebar clears all guard overrides and restores normal thresholds.

---

### 6.2 Human-in-the-Loop (HITL) Gate

Even without a circuit breaker, a single run is routed to human review if **any** of these 5 conditions is true:

| Condition | Reason |
|---|---|
| LLM confidence < 80% | Agent is not confident enough to act autonomously |
| DOM Inspector match score < 0.7 | Couldn't find a convincing candidate element |
| LLM returns `needs_human_review: true` | Agent explicitly flags ambiguity |
| Suite selection was `fallback_all` | No signal about what changed; broader ambiguity |
| Any circuit breaker mechanism is active | Global safety override |

When HITL is triggered, the pipeline **pauses**. No code is modified, no ticket is filed, no PR is opened — until a `qa_manager` or `super_admin` acts on the Approval Queue in the dashboard.

**What the QA Manager sees in the Approval Queue:**
- Classification and confidence score
- All failing tests with error messages
- DOM candidates with scores
- Proposed fix diff (if any)
- Evidence text from the LLM

On **Approve** → auto-fix runs (if drift) or ticket filed (if bug)  
On **Reject** → run is closed, decision logged in audit trail

---

## Part 7 — External Integrations

### 7.1 Groq LLM

Groq runs the `llama-3.3-70b-versatile` model on custom inference hardware (LPU — Language Processing Unit) optimised for speed. It's the fastest large-model inference available, making it ideal for triage where latency matters.

**Used in two places:**
1. **LLM Triage (Node ⑤):** Classification + confidence + proposed fix
2. **Reporter (Node ⑥):** Generating the human-readable incident report

**Settings:** `temperature=0` (deterministic), `max_tokens=1024` for triage, `max_tokens=512` for reports.

**Cost:** ~$0.0001 per triage call. The full pipeline costs ~$0.001 total.

---

### 7.2 GitHub Integration

**Personal Access Token (PAT)** stored as `GITHUB_TOKEN` env var. Used for:

1. **Injecting drift:** `PUT /repos/{owner}/{repo}/contents/{file}` — commits HTML change to trigger a test failure
2. **Opening PRs:** Creates branch `qa-agent/auto-heal`, commits the test fix, opens PR with structured description
3. **Resetting demo:** Reverts GitHub Pages to clean state via another commit

**The PR always requires human review.** The agent cannot merge, cannot write to `main`, and cannot modify production code. It only opens a PR in a dedicated branch.

---

### 7.3 Jira

Jira is an industry-standard task/ticket management system (by Atlassian). When a real bug is detected (classification = `bug`), the agent creates a Jira ticket so it enters the formal engineering workflow — with priority, assignee, severity, and status tracking.

**Why not just Slack?** Slack messages get buried. A Jira ticket stays open until resolved, can be assigned, tracked in sprints, and referenced in code reviews. It's the formal paper trail for a production bug.

**In the demo:** If real Jira API credentials aren't configured, the ticket is stored locally in the `tickets` table and shown in the Tickets tab. The data model is identical.

---

### 7.4 Slack

Slack is a workplace messaging platform (like WhatsApp for companies, with channels and bots). The QA Agent fires alerts at the end of every run via a Slack webhook URL.

**Four alert types:**
```
PR opened:      "🔧 QA Agent opened PR #N — drift fixed (93% conf)"  → #qa-alerts
Bug detected:   "🚨 HIGH bug — Login redirect broken. Ticket BUG-001" → #engineering
HITL needed:    "⏳ Human review needed — run def456, 72% conf"       → #qa-managers
Circuit breaker:"⚡ Circuit breaker active — error rate >20%"         → #ops
```

**Mechanism:** A webhook URL is a special URL that when you POST JSON to it, Slack displays it as a message in a channel. No OAuth, no bot setup — just a URL in `SLACK_WEBHOOK_URL` env var.

In the demo, if `SLACK_WEBHOOK_URL` isn't set, alerts go to the in-app notifications table (the 🔔 bell icon) instead.

---

### 7.5 Langfuse

Langfuse is an LLM observability platform — the equivalent of Datadog or Sentry, but specifically built for LLM systems. It records every LLM call with full context.

**Why it's needed:** Without Langfuse, the LLM is a black box. If a triage goes wrong, you can't see what prompt was sent, what the model replied, or how much it cost.

**What gets logged per run:**
```
Trace: "triage_run_{run_id}"
  ├── Span: "llm_triage"
  │     Input:  [full prompt — failures, DOM candidates, commits]
  │     Output: [full LLM response — JSON classification]
  │     Model:  groq/llama-3.3-70b-versatile
  │     Tokens: 1,847 input / 312 output
  │     Cost:   $0.000082
  │     Latency: 1.2 seconds
  └── Span: "llm_reporter"
        Input:  [classification result + test failures]
        Output: [human-readable incident report]
        Tokens: 892 input / 456 output
        Cost:   $0.000031
```

The `langfuse_trace_url` stored in each run links directly to this trace in the Langfuse dashboard — that's the link visible in the Run Drawer. Clicking it shows the CTO exactly what the LLM was told and what it replied.

---

## Part 8 — Metrics Calculation

All metrics are computed from the `qa_runs` table at query time by the `/metrics/summary` endpoint.

| Metric | SQL equivalent |
|---|---|
| `total_runs` | `COUNT(*)` from qa_runs |
| `success_runs` | `COUNT(*) WHERE status = 'complete'` |
| `success_rate` | `success_runs / total_runs` |
| `avg_cost_usd` | `AVG(cost_usd) WHERE cost_usd IS NOT NULL` |
| `total_cost_usd` | `SUM(cost_usd)` |
| `avg_confidence` | `AVG((triage_result->>'confidence')::float)` |
| Classification breakdown | `GROUP BY triage_result->>'classification'` |

**Circuit breaker metrics** come from `system_events`:
- Error rate: `(failed runs / total runs)` in the last 24 hours
- FPR: `(PR rejected events) / (PR opened events)` in the last 30 days
- Confidence drift: current 24h average vs previous 24h average

**Cost tracking:** The Langfuse SDK returns token counts per call. The backend computes `cost_usd = (input_tokens * input_rate) + (output_tokens * output_rate)` using current Groq pricing and stores it in `qa_runs.cost_usd`.

---

## Part 9 — Real-World Scale and the AI Code Quality Problem

### 9.1 Real-World Test Suite Scale

| Organisation | Scale |
|---|---|
| Google (2017 data, much larger today) | 4.2 million tests; ~2% flaky = 63,000 flaky tests |
| Facebook/Meta | Millions of tests, probabilistic flakiness scoring at scale |
| Enterprise SaaS (Series B–C) | 200–500 E2E tests, 20–80 suites |
| Enterprise (banking, healthcare) | 500–5,000 E2E tests, heavily regulated |

Our demo: 28 tests, 8 suites. A real product adds 200–5,000 tests — the architecture is identical.

**What real commits break in production (beyond CSS renames):**
- Payment calculation inverted by AI refactor (produces wrong totals)
- Auth middleware rate limiter silently dropped in refactor (security hole)
- Login redirect sends users to wrong page
- Cart total shows `undefined` after API response format change
- Order status stuck at "Pending" after payment
- Feature flag toggle removes a button entirely

---

### 9.2 The AI Code Quality Problem (Why QA Agents Matter More Now)

Counterintuitively, AI coding tools **increase** the need for automated QA.

**The data:**
- **CodeRabbit, December 2025:** AI-generated code has **1.7x more bugs** than human-written code
- **QASource, Q3 2024 – Q4 2025:** Engineering teams using AI coding tools see **23.5% more production incidents per PR**

**Why this happens:**

When a human writes code manually, they: write it → run it locally → click through the UI → verify the behaviour → write/update tests → push. The verification loop is natural.

When an AI writes code: engineer prompts → reviews the diff visually → pushes. **Steps 2–4 (running the app, clicking through flows, verifying behaviour) disappear.** The code looks correct. It compiles. Types check. Linter passes. But nobody actually ran the checkout flow.

**The 4 failure modes of AI-generated code** (per CodeRabbit research):
1. **Intent inversion** — Code does the literal opposite: `price * (1 - discount)` instead of `price * (1 + tax)`. Syntactically valid. Only an E2E test catches it.
2. **Dropped safeguards** — AI reproduces the happy path and silently removes defensive logic: null checks, rate limiters, idempotency guards.
3. **Contextual mismatch** — AI imports a library you don't use, follows a pattern from its training data that conflicts with your codebase conventions.
4. **Silent pass problem** — Code passes every unit test and is still wrong. E.g., an AI refactor preserves all 200 covered behaviours but quietly changes the one path that wasn't tested.

**The QA Agent's role:** It is the automated verification step that AI-assisted development removed. It runs E2E tests on every push, triages failures in under 4 minutes at $0.001 each, and heals trivial selector drift automatically — freeing engineers to only spend time on real bugs.

**The honest framing:** Our demo scenarios (CSS renames) are intentionally simple to make the concept immediately obvious. The real value is the triage and automation engine — which classifies intent inversions, dropped safeguards, and auth bypasses just as well as selector renames, because LLM reasoning scales with failure complexity.

---

## Appendix — One-Page Architecture Summary

```
┌─────────────────────────────────────────────────────────────────────────┐
│  TRIGGER: Developer push OR nightly APScheduler OR manual flow button   │
└──────────────────────────────┬──────────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ ① CANARY — HEAD request to demo site                                     │
│   PASS → continue  │  FAIL → status=env_error, pipeline stops            │
└──────────────────────────────┬───────────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ ② SUITE SELECT                                                            │
│   Commit has selectors → Selector Index lookup → pick 2-3 suites         │
│   Vague commit → Groq LLM picks suites                                   │
│   No signal → fallback_all (triggers HITL condition)                     │
└──────────────────────────────┬───────────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ ③ TEST RUNNER — pytest-playwright + Chromium headless                    │
│   All tests PASS → status=success, pipeline stops                        │
│   Any test FAILS → failure list → continue                               │
└──────────────────────────────┬───────────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ ④ DOM INSPECTOR — Playwright crawls failing page                         │
│   Extracts all interactive elements                                      │
│   Fuzzy-matches against broken selectors (0.0–1.0 score)                 │
│   Top candidates → passed to triage                                      │
└──────────────────────────────┬───────────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ ⑤ LLM TRIAGE — Groq llama-3.3-70b → Deterministic fallback              │
│   Input: failures + DOM candidates + recent commits                      │
│   Output: classification + confidence (0.0–1.0) + proposed_fix          │
│                                                                           │
│   CIRCUIT BREAKERS monitor this node:                                    │
│   Cost Guard | Error Rate Guard | FPR Guard | Confidence Drift Guard     │
└────────────────────────────────┬────────────────────────────────────────-┘
                                 │
              ┌──────────────────┼──────────────────────┐
              ▼                  ▼                       ▼
   classification=drift    classification=bug      conf < 0.80 OR
   confidence ≥ 0.80                               HITL condition
              │                  │                       │
              ▼                  ▼                       ▼
┌─────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ ⑥ AUTO-FIXER   │  │ ⑥ BUG TICKET         │  │ ⑥ HITL QUEUE         │
│ GitHub PR opens │  │ Jira ticket created  │  │ Pipeline pauses       │
│ Slack: #qa-alerts│  │ Slack: #engineering  │  │ QA Manager reviews    │
│ In-app notif    │  │ In-app notif         │  │ Approve → auto-fix    │
│ Langfuse logged │  │ Langfuse logged      │  │ Reject → logged       │
└────────┬────────┘  └──────────┬───────────┘  └──────────┬────────────┘
         └─────────────────────┬┴───────────────────────────┘
                               ▼
         REPORTER (always): LLM writes incident report → stored in qa_runs
         METRICS updated: classification counts, cost, confidence averages
```

**Hard rules the agent never breaks:**
- No auto-merge: all PRs require human review and approval
- No writes to main: only `qa-agent/auto-heal` branch
- No action without logging: every decision has an audit trail in Postgres + Langfuse
- No action when unsure: confidence < 80% always routes to HITL
- No runaway cost: $0.50/run hard cap triggers circuit breaker

---

*Last updated: May 2026 · RAIT QA Agent v2.0*
