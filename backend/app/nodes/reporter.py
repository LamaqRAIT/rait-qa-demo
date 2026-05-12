"""
Reporter node — uses Claude Haiku (cheapest) to format run summary.
Persists the report text to run.report_text for UI display.
"""
import structlog
import app.db as db
from app.core.state import RunRecord
from app.llm.client import call_llm

log = structlog.get_logger()

REPORT_PROMPT = """Write a concise one-paragraph QA run summary (3–4 sentences max). Be specific.

Classification: {classification}
Confidence: {confidence:.0%}
Evidence: {evidence}
Failures: {failure_count} test(s)
Test suites: {suites}
PR opened: {pr_url}
Auto-fixed: {auto_fixed}
Cost: ${cost:.4f}

Include: what failed, the root cause, what action was taken, and whether it needs human follow-up.
Do NOT include headers, bullet points, or formatting — plain prose only."""


async def generate_report(run: RunRecord) -> str:
    prompt = REPORT_PROMPT.format(
        classification=run.triage.classification or "unknown",
        confidence=run.triage.confidence,
        evidence=run.triage.evidence or "n/a",
        failure_count=len(run.failures),
        suites=", ".join(run.suites_run) or "all suites",
        pr_url=run.pr_url or "none",
        auto_fixed=bool(run.pr_url),
        cost=run.cost_usd,
    )

    try:
        raw, _, _, _, _, _ = await call_llm(
            prompt=prompt,
            run_id=run.id,
            call_name="reporter",
            model_preference="haiku",
            max_tokens=256,
        )
        report = raw.strip() if raw else _fallback_report(run)
    except Exception as exc:
        log.warning("reporter.llm_error", run_id=run.id, error=str(exc)[:100])
        report = _fallback_report(run)

    run.report_text = report
    await db.update_run(run)
    return report


def _fallback_report(run: RunRecord) -> str:
    cls = run.triage.classification or "unknown"
    fix_note = f" PR opened: {run.pr_url}." if run.pr_url else ""
    status = "awaiting human review" if run.status.value == "awaiting_human" else "complete"
    return (
        f"Run {run.id[:8]}: {len(run.failures)} failure(s) in {', '.join(run.suites_run) or 'the test suite'} "
        f"classified as {cls.upper()} with {run.triage.confidence:.0%} confidence. "
        f"Evidence: {run.triage.evidence or 'n/a'}.{fix_note} "
        f"Pipeline status: {status}."
    )
