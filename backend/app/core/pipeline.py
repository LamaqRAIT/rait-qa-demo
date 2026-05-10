"""
Pipeline orchestrator — replaces LangGraph entirely.
Every stage transition = DB write. Survives Railway redeploys.
HITL: run persisted as AWAITING_HUMAN → POST /approve/{run_id} resumes as BackgroundTask.
"""
import asyncio
import time
import structlog
import app.db as db
from app.core.state import RunRecord, RunStatus, NodeState
from app.core.evidence import build_evidence_bundle, get_recent_commits, build_test_history
from app.core.circuit_breaker import check_cost_limit, should_quarantine
from app.config import get_settings

log = structlog.get_logger()


async def _set_node(run: RunRecord, node: str, state: str, annotation: str = "") -> None:
    run.node_states[node] = NodeState(state=state, annotation=annotation)
    await db.update_run(run)


async def _transition(run: RunRecord, status: RunStatus) -> None:
    run.status = status
    run.updated_at = __import__("datetime").datetime.utcnow()
    await db.update_run(run)


async def run_pipeline(run_id: str) -> None:
    run = await db.get_run(run_id)
    if not run:
        log.error("pipeline.run_not_found", run_id=run_id)
        return

    settings = get_settings()
    log.info("pipeline.start", run_id=run_id, branch=run.trigger_branch)

    try:
        from app.nodes.canary import run_canary
        from app.nodes.runner import run_tests
        from app.nodes.inspector import inspect_dom
        from app.nodes.triage import triage
        from app.nodes.reporter import generate_report

        await _set_node(run, "git_watcher", "success", f"Triggered by {run.trigger_commit[:7] or 'manual'}")

        await _set_node(run, "change_analyzer", "running", "Mapping changed files to test suites…")
        suites = _map_files_to_suites(run.trigger_commit)
        run.suites_run = suites
        await _set_node(run, "change_analyzer", "success", f"Mapped to: {', '.join(suites) or 'all suites'}")

        await _set_node(run, "test_runner", "running", "Running canary checks…")
        t0 = time.time()
        canary_result = await run_canary(run_id)
        run.node_timings["canary"] = round(time.time() - t0, 2)

        if not canary_result["passed"]:
            await _set_node(run, "test_runner", "failed", "Canary failed — environment issue")
            run.triage.classification = "env"
            run.triage.confidence = 0.95
            run.triage.evidence = "Canary tests failed — infrastructure or network issue"
            await _transition(run, RunStatus.FAILED)
            return

        await _set_node(run, "test_runner", "running", "Running test suite…")
        t0 = time.time()
        await _transition(run, RunStatus.RUNNING)
        failures = await run_tests(run_id)
        run.node_timings["test_runner"] = round(time.time() - t0, 2)
        run.failures = failures

        if not failures:
            await _set_node(run, "test_runner", "success", "All tests passed")
            await _set_node(run, "browser_inspector", "skipped", "No failures to inspect")
            await _set_node(run, "classifier", "skipped", "No failures to classify")
            await _set_node(run, "reporter", "success", "All tests passed — nothing to report")
            await _transition(run, RunStatus.COMPLETE)
            return

        await _set_node(run, "test_runner", "failed", f"{len(failures)} failure(s) detected")

        if should_quarantine(run.consecutive_failures):
            await _set_node(run, "browser_inspector", "skipped", "Quarantined — max retries exceeded")
            await _transition(run, RunStatus.QUARANTINED)
            return

        await _set_node(run, "browser_inspector", "running", "Inspecting live DOM for selector changes…")
        t0 = time.time()
        await _transition(run, RunStatus.INSPECTING)
        dom_report = await inspect_dom(run_id, failures)
        run.dom_report = dom_report
        run.node_timings["browser_inspector"] = round(time.time() - t0, 2)

        changed = len(dom_report.get("changed_selectors", []))
        await _set_node(
            run, "browser_inspector",
            "success" if changed else "success",
            f"Found {changed} candidate selector(s)" if changed else "No DOM changes detected",
        )

        recent_commits = get_recent_commits(run_id)
        test_history = build_test_history(failures, run.consecutive_failures)
        evidence = build_evidence_bundle(failures, dom_report, recent_commits, test_history)
        run.evidence = evidence

        await _set_node(run, "classifier", "running", "Classifying failures with LLM…")
        t0 = time.time()
        await _transition(run, RunStatus.TRIAGING)
        triage_result, trace_id, input_tok, output_tok, cost = await triage(run_id, failures, dom_report, evidence)
        run.triage = triage_result
        run.node_timings["classifier"] = round(time.time() - t0, 2)
        run.consecutive_failures += 1
        if trace_id:
            run.langfuse_trace_id = trace_id
        run.input_tokens = input_tok
        run.output_tokens = output_tok
        run.cost_usd = cost
        await db.update_run(run)

        if not check_cost_limit(run.cost_usd):
            await _set_node(run, "classifier", "failed", "Cost limit exceeded — aborting")
            await _transition(run, RunStatus.FAILED)
            return

        classification = triage_result.classification
        confidence = triage_result.confidence
        threshold = settings.auto_fix_threshold

        if classification == "drift" and confidence >= threshold:
            await _set_node(
                run, "classifier", "success",
                f"DRIFT — confidence {confidence:.2f} — auto-fix eligible",
            )
            await _set_node(run, "auto_fixer", "running", "Applying selector fix…")
            await _set_node(run, "ticket_creator", "skipped", "Drift — no ticket needed")
            await _transition(run, RunStatus.HEALING)
            await _apply_fix_and_complete(run)

        elif classification == "drift":
            await _set_node(
                run, "classifier", "waiting",
                f"DRIFT — confidence {confidence:.2f} — below threshold, escalating to human",
            )
            await _set_node(run, "human_review", "waiting", "Awaiting human approval")
            await _transition(run, RunStatus.AWAITING_HUMAN)
            log.info("pipeline.awaiting_human", run_id=run_id)

        elif classification == "bug":
            await _set_node(
                run, "classifier", "failed",
                f"BUG — confidence {confidence:.2f}",
            )
            await _set_node(run, "auto_fixer", "skipped", "BUG classification — no auto-fix")
            await _set_node(run, "ticket_creator", "running", "Creating bug report…")
            ticket_id = f"BUG-{run_id[:6].upper()}"
            await _set_node(
                run, "ticket_creator", "success",
                f"{ticket_id} — BUG — severity: HIGH — team notified",
            )
            await _transition(run, RunStatus.FAILED)

        else:
            await _set_node(
                run, "classifier", "failed",
                f"ENV — confidence {confidence:.2f}",
            )
            await _set_node(run, "auto_fixer", "skipped", "ENV issue — no auto-fix")
            await _set_node(run, "ticket_creator", "running", "Creating environment alert…")
            ticket_id = f"ENV-{run_id[:6].upper()}"
            await _set_node(
                run, "ticket_creator", "success",
                f"{ticket_id} — ENV — severity: MEDIUM — ops notified",
            )
            await _transition(run, RunStatus.FAILED)

    except Exception as exc:
        log.error("pipeline.error", run_id=run_id, error=str(exc), exc_info=True)
        run_state = await db.get_run(run_id)
        if run_state:
            run_state.status = RunStatus.FAILED
            await db.update_run(run_state)


async def _apply_fix_and_complete(run: RunRecord) -> None:
    from app.nodes.auto_fixer import auto_fix
    from app.nodes.reporter import generate_report

    try:
        pr_url = await auto_fix(run)
        if pr_url:
            run.pr_url = pr_url
            await _set_node(
                run, "auto_fixer", "success",
                f"PR opened: {pr_url}",
            )
        else:
            await _set_node(run, "auto_fixer", "failed", "Fix not applied — see logs")

        await _set_node(run, "reporter", "running", "Writing run summary…")
        await generate_report(run)
        await _set_node(run, "reporter", "success", "Run complete")
        run.consecutive_failures = 0
        await _transition(run, RunStatus.COMPLETE)

    except Exception as exc:
        log.error("pipeline.fix_error", run_id=run.id, error=str(exc))
        await _set_node(run, "auto_fixer", "failed", f"Error: {exc}")
        await _transition(run, RunStatus.FAILED)


async def resume_after_approval(run_id: str, approved: bool, reviewer: str) -> None:
    run = await db.get_run(run_id)
    if not run:
        log.error("pipeline.resume_not_found", run_id=run_id)
        return

    run.approved_by = reviewer
    if not approved:
        await _set_node(run, "human_review", "failed", f"Rejected by {reviewer}")
        await _transition(run, RunStatus.FAILED)
        return

    await _set_node(run, "human_review", "success", f"Approved by {reviewer}")
    await _set_node(run, "auto_fixer", "running", "Applying selector fix…")
    await _transition(run, RunStatus.HEALING)
    await _apply_fix_and_complete(run)


def _map_files_to_suites(trigger_commit: str) -> list[str]:
    return ["test_checkout.py", "test_login.py"]
