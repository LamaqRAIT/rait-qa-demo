"""
Reporter node — deterministic template-based report generation.

The LLM call (Claude Haiku) has been removed per the rework decision:
all facts are already structured in RunRecord; an LLM adds hallucination risk
with zero benefit. The template covers all four outcomes and includes
the confidence gate signal summary for auditability.
"""
import json
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
    cls = run.triage.classification or "unknown"
    suites_str = ", ".join(run.suites_run) or "all suites"
    n_failures = len(run.failures)
    gate_str = _gate_summary(run)

    if cls == "drift":
        fix = run.triage.proposed_fix
        if run.pr_url and fix:
            old = fix.get("old", "?")[:60]
            new = fix.get("new", "?")[:60]
            return (
                f"Selector drift detected in {suites_str}: {n_failures} test(s) failed. "
                f"Auto-healed — selector updated from '{old}' to '{new}' ({gate_str}). "
                f"PR: {run.pr_url}."
            )
        elif run.pr_url:
            return (
                f"Selector drift detected in {suites_str}: {n_failures} test(s) failed. "
                f"Auto-healed ({gate_str}). PR: {run.pr_url}."
            )
        else:
            held = ", ".join(run.gate_held_checks) if run.gate_held_checks else "low confidence"
            return (
                f"Selector drift detected in {suites_str}: {n_failures} test(s) failed. "
                f"Sent for human review — gate held on: {held}. "
                f"Awaiting approval at /approve/{run.id}."
            )

    elif cls == "bug":
        return (
            f"Functional bug detected in {suites_str}: {n_failures} test(s) failed. "
            f"Evidence: {run.triage.evidence or 'n/a'}. "
            f"Jira ticket filed. No auto-fix applied."
        )

    elif cls == "env":
        return (
            f"Environment issue detected in {suites_str}: {n_failures} test(s) failed. "
            f"Evidence: {run.triage.evidence or 'n/a'}. "
            f"Ops team notified. No auto-fix applied."
        )

    else:
        return (
            f"Run {run.id[:8]}: {n_failures} failure(s) in {suites_str} "
            f"— classification: {cls.upper()}. "
            f"Pipeline status: {run.status.value}."
        )


def _gate_summary(run: RunRecord) -> str:
    parts = []
    if run.p_class is not None:
        parts.append(f"p={run.p_class:.2f}")
    if run.logprob_margin is not None:
        parts.append(f"margin={run.logprob_margin:.2f}")
    if run.nli_entailment is not None:
        parts.append(f"nli={run.nli_entailment:.2f}")
    if run.fix_grounded is not None:
        parts.append(f"grounded={'yes' if run.fix_grounded else 'no'}")
    return "gate: " + ", ".join(parts) if parts else "gate: deterministic fallback"
