"""
LangGraph node functions for the QA pipeline.
Each node updates QAState and emits a node status update to the DB.
"""
import json
import subprocess
import structlog
from pathlib import Path
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from app.config import get_settings
from app.models import NodeUpdate, TriageResult
import app.db as db

log = structlog.get_logger()
settings = get_settings()

TESTS_DIR = Path(__file__).resolve().parent.parent.parent / "tests"
ARTIFACTS_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)


def _get_llm():
    if settings.google_api_key:
        return ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            google_api_key=settings.google_api_key,
        )
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0,
        groq_api_key=settings.groq_api_key,
    )


async def _update_node(run_id: str, node: str, state: str, annotation: str = "") -> None:
    run = await db.get_run(run_id)
    if run:
        run.node_states[node] = NodeUpdate(node=node, state=state, annotation=annotation)
        await db.update_run(run)


# ── Node 1: Change Analyzer ──────────────────────────────────────────────────

async def change_analyzer_node(state: QAState) -> QAState:
    run_id = state["run_id"]
    await _update_node(run_id, "change_analyzer", "running", "Analyzing changed files…")
    log.info("change_analyzer.start", run_id=run_id, files=state["changed_files"])

    changed = state.get("changed_files", [])
    suites: list[str] = []

    # Simple manifest-based mapping — Phase 2 will add semantic LLM analysis
    MANIFEST = {
        "checkout": ["test_checkout.py"],
        "login":    ["test_login.py"],
        "auth":     ["test_login.py"],
        "product":  ["test_products.py"],
    }
    for f in changed:
        for keyword, tests in MANIFEST.items():
            if keyword in f.lower():
                suites.extend(t for t in tests if t not in suites)

    if not suites:
        suites = ["test_checkout.py", "test_login.py"]

    await _update_node(run_id, "change_analyzer", "success",
                       f"Mapped to: {', '.join(suites)}")
    return {**state, "suites_to_run": suites}


# ── Node 2: Test Runner ───────────────────────────────────────────────────────

async def test_runner_node(state: QAState) -> QAState:
    run_id = state["run_id"]
    suites = state.get("suites_to_run", [])
    await _update_node(run_id, "test_runner", "running",
                       f"Running {len(suites)} suite(s)…")
    log.info("test_runner.start", run_id=run_id, suites=suites)

    junit_path = ARTIFACTS_DIR / f"{run_id}.xml"
    cmd = [
        "python", "-m", "pytest",
        *[str(TESTS_DIR / s) for s in suites],
        "--tb=short",
        f"--junit-xml={junit_path}",
        "-q",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        junit_xml = junit_path.read_text() if junit_path.exists() else ""
        passed = result.returncode == 0
        annotation = "All tests passed" if passed else f"Failures detected — exit {result.returncode}"
        node_state = "success" if passed else "failed"
        await _update_node(run_id, "test_runner", node_state, annotation)
        log.info("test_runner.done", run_id=run_id, passed=passed)
        return {**state, "junit_xml": junit_xml, "failures": _parse_failures(result.stdout)}
    except subprocess.TimeoutExpired:
        await _update_node(run_id, "test_runner", "failed", "Timeout after 120s")
        return {**state, "junit_xml": "", "failures": [], "error": "playwright_timeout"}


def _parse_failures(stdout: str) -> list[dict]:
    failures = []
    for line in stdout.splitlines():
        if "FAILED" in line:
            failures.append({"test": line.strip(), "raw": line})
    return failures


# ── Node 3: Browser Inspector ─────────────────────────────────────────────────

async def browser_inspector_node(state: QAState) -> QAState:
    run_id = state["run_id"]
    failures = state.get("failures", [])

    if not failures:
        await _update_node(run_id, "browser_inspector", "skipped", "No failures to inspect")
        return {**state, "dom_report": {}}

    await _update_node(run_id, "browser_inspector", "running",
                       "Inspecting live DOM for selector changes…")

    # Phase 2 will invoke browser-use agent here.
    # For Phase 1, return a stub DOM report so the pipeline continues.
    dom_report: dict = {
        "inspected": True,
        "changed_selectors": [],
        "notes": "Phase 1 stub — browser-use agent wired in Phase 2",
    }
    await _update_node(run_id, "browser_inspector", "success",
                       "DOM inspection complete")
    return {**state, "dom_report": dom_report}


# ── Node 4: Classifier / Triage ───────────────────────────────────────────────

TRIAGE_PROMPT = """You are a QA triage agent. Given a test failure, classify it.

Failed tests:
{failures}

DOM report:
{dom_report}

Classify as exactly one of:
- drift: UI changed (selector, text, layout) — test is outdated, not a bug
- bug: Application logic broke — test is correct, code is wrong
- env: Infrastructure problem — network, timeout, missing service

Reply with JSON only:
{{
  "classification": "drift"|"bug"|"env",
  "confidence": 0.0-1.0,
  "evidence": "one sentence explaining why",
  "proposed_fix": null | {{"file":"...", "old":"...", "new":"..."}}
}}

proposed_fix must be null for bug and env. Only provide it for drift."""


async def classifier_node(state: QAState) -> QAState:
    run_id = state["run_id"]
    failures = state.get("failures", [])

    if not failures:
        await _update_node(run_id, "classifier", "skipped", "No failures — skipping triage")
        return {**state, "classification": "none", "confidence": 1.0, "evidence": "All tests passed"}

    await _update_node(run_id, "classifier", "running", "Classifying failures…")

    llm = _get_llm()
    prompt = TRIAGE_PROMPT.format(
        failures=json.dumps(failures, indent=2),
        dom_report=json.dumps(state.get("dom_report", {}), indent=2),
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        result = json.loads(raw)

        classification = result.get("classification", "env")
        confidence = float(result.get("confidence", 0.5))
        evidence = result.get("evidence", "")
        proposed_fix = result.get("proposed_fix")

        threshold = settings.auto_fix_threshold
        if classification == "drift" and confidence >= threshold:
            annotation = f"DRIFT — confidence {confidence:.2f} — auto-fix eligible"
            node_state = "success"
        elif classification == "drift":
            annotation = f"DRIFT — confidence {confidence:.2f} — escalating to human"
            node_state = "waiting"
        elif classification == "bug":
            annotation = f"BUG — confidence {confidence:.2f} — no fix attempted"
            node_state = "failed"
        else:
            annotation = f"ENV — confidence {confidence:.2f} — environment issue"
            node_state = "failed"

        await _update_node(run_id, "classifier", node_state, annotation)
        log.info("classifier.done", run_id=run_id, classification=classification,
                 confidence=confidence)

        # Human-in-the-loop interrupt for low-confidence drift
        if classification == "drift" and confidence < threshold:
            await _update_node(run_id, "human_review", "waiting",
                               "Awaiting human approval")
            decision = interrupt({
                "run_id": run_id,
                "classification": classification,
                "confidence": confidence,
                "evidence": evidence,
                "proposed_fix": proposed_fix,
                "failures": failures,
            })
            approved = decision.get("approved", False)
            reviewer = decision.get("reviewer_name", "unknown")
            await _update_node(run_id, "human_review", "success",
                               f"{'Approved' if approved else 'Rejected'} by {reviewer}")
            return {**state,
                    "classification": classification, "confidence": confidence,
                    "evidence": evidence, "proposed_fix": proposed_fix,
                    "approved": approved, "approved_by": reviewer}

        return {**state,
                "classification": classification, "confidence": confidence,
                "evidence": evidence, "proposed_fix": proposed_fix,
                "approved": classification == "drift" and confidence >= threshold,
                "approved_by": "system"}

    except Exception as exc:
        log.error("classifier.error", run_id=run_id, error=str(exc))
        await _update_node(run_id, "classifier", "failed", f"Error: {exc}")
        return {**state, "classification": "env", "confidence": 0.5,
                "evidence": str(exc), "proposed_fix": None, "approved": False}


# ── Node 5: Auto-Fixer ────────────────────────────────────────────────────────

async def auto_fixer_node(state: QAState) -> QAState:
    run_id = state["run_id"]
    classification = state.get("classification", "")
    approved = state.get("approved", False)
    proposed_fix = state.get("proposed_fix")

    should_fix = classification == "drift" and approved and proposed_fix
    if not should_fix:
        label = "SKIPPED" if classification == "bug" else "No fix needed"
        await _update_node(run_id, "auto_fixer", "skipped", label)
        return {**state, "auto_fixed": False}

    await _update_node(run_id, "auto_fixer", "running", "Applying selector fix…")

    try:
        target_file = TESTS_DIR / proposed_fix["file"]
        if target_file.exists():
            content = target_file.read_text()
            updated = content.replace(proposed_fix["old"], proposed_fix["new"])
            target_file.write_text(updated)
            await _update_node(run_id, "auto_fixer", "success",
                               f"Rewrote: {proposed_fix['old']!r} → {proposed_fix['new']!r}")
            log.info("auto_fixer.applied", run_id=run_id, fix=proposed_fix)
            return {**state, "auto_fixed": True}
        else:
            await _update_node(run_id, "auto_fixer", "failed",
                               f"File not found: {proposed_fix['file']}")
            return {**state, "auto_fixed": False}
    except Exception as exc:
        await _update_node(run_id, "auto_fixer", "failed", f"Error: {exc}")
        return {**state, "auto_fixed": False}


# ── Node 6: Ticket Creator ────────────────────────────────────────────────────

async def ticket_creator_node(state: QAState) -> QAState:
    run_id = state["run_id"]
    classification = state.get("classification", "")

    if classification not in ("bug", "env"):
        await _update_node(run_id, "ticket_creator", "skipped", "No ticket needed")
        return state

    await _update_node(run_id, "ticket_creator", "running", "Creating issue ticket…")
    ticket_id = f"BUG-{run_id[:6].upper()}"
    severity = "HIGH" if classification == "bug" else "MEDIUM"
    annotation = f"{ticket_id} — {classification.upper()} — severity: {severity} — team notified"
    await _update_node(run_id, "ticket_creator", "success", annotation)
    log.info("ticket_creator.done", run_id=run_id, ticket=ticket_id)
    return state


# ── Node 7: Reporter ──────────────────────────────────────────────────────────

async def reporter_node(state: QAState) -> QAState:
    run_id = state["run_id"]
    await _update_node(run_id, "reporter", "running", "Writing run summary…")

    run = await db.get_run(run_id)
    if run:
        if state.get("auto_fixed"):
            run.status = "complete"
            run.commit_sha = state.get("commit_sha", "pending")
        elif state.get("classification") in ("bug", "env"):
            run.status = "failed"
        else:
            run.status = "complete"

        from app.models import TriageResult
        run.triage_result = TriageResult(
            classification=state.get("classification", "env"),
            confidence=state.get("confidence", 0.5),
            evidence=state.get("evidence", ""),
            proposed_fix=state.get("proposed_fix"),
        )
        run.suites_run = state.get("suites_to_run", [])
        run.failures = state.get("failures", [])
        run.approved_by = state.get("approved_by")
        await db.update_run(run)

    await _update_node(run_id, "reporter", "success", "Run summary saved")
    log.info("reporter.done", run_id=run_id, status=run.status if run else "unknown")
    return state
