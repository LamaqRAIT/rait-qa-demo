"""
Reporter node — deterministic template, no LLM call.
All facts are already structured in RunRecord; LLM adds hallucination risk for zero benefit.
"""
import structlog
import app.db as db
from app.core.state import RunRecord

log = structlog.get_logger()


async def generate_report(run: RunRecord) -> str:
    report = _build_report(run)
    run.report_text = report
    await db.update_run(run)
    return report


def _build_report(run: RunRecord) -> str:
    cls = (run.triage.classification or "unknown").upper()
    conf = run.triage.confidence
    evidence = run.triage.evidence or "no evidence recorded"
    n_fail = len(run.failures)
    suites = ", ".join(run.suites_run) or "all suites"
    method = run.suite_selection_method or "fallback_all"

    # Action line
    if run.pr_url:
        action = f"Auto-fix applied — PR opened: {run.pr_url}."
    elif run.status.value == "awaiting_human":
        triggers = ", ".join(run.hitl_triggers) if run.hitl_triggers else "low confidence"
        action = f"Routed to human review ({triggers})."
    elif run.status.value == "complete":
        action = "Pipeline complete — no further action required."
    else:
        action = f"Pipeline ended with status: {run.status.value}."

    # Cost line (omit if zero — vLLM runs are free)
    cost_note = f" LLM cost: ${run.cost_usd:.4f}." if run.cost_usd > 0 else ""

    report = (
        f"{n_fail} failure(s) in {suites} [{method}] classified as {cls} "
        f"with {conf:.0%} confidence. "
        f"Evidence: {evidence}. "
        f"{action}"
        f"{cost_note}"
    )

    log.info("reporter.done", run_id=run.id, classification=cls, confidence=conf)
    return report
