"""
Reporter node — formats the final run summary using Gemini.
Updates run status in DB to complete or failed.
"""
import asyncio
import json
import structlog
import google.generativeai as genai
from app.config import get_settings
from app.core.state import RunRecord, RunStatus

log = structlog.get_logger()

REPORT_PROMPT = """Write a concise one-paragraph QA run summary (3-4 sentences max).

Classification: {classification}
Confidence: {confidence}
Evidence: {evidence}
Failures: {failure_count}
PR opened: {pr_url}
Auto-fixed: {auto_fixed}

Be specific about what failed, why, and what action was taken."""


async def generate_report(run: RunRecord) -> str:
    settings = get_settings()
    if not settings.google_api_key:
        return _fallback_report(run)
    genai.configure(api_key=settings.google_api_key)
    prompt = REPORT_PROMPT.format(
        classification=run.triage.classification,
        confidence=run.triage.confidence,
        evidence=run.triage.evidence,
        failure_count=len(run.failures),
        pr_url=run.pr_url or "none",
        auto_fixed=bool(run.pr_url),
    )
    _MODELS = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]
    try:
        for model_name in _MODELS:
            try:
                m = genai.GenerativeModel(model_name)
                for attempt in range(2):
                    try:
                        response = await m.generate_content_async(prompt)
                        return response.text.strip()
                    except Exception as e:
                        if "429" in str(e) and attempt == 0:
                            await asyncio.sleep(15)
                        else:
                            raise
            except Exception:
                continue
    except Exception as exc:
        log.warning("reporter.gemini_error", run_id=run.id, error=str(exc))
    return _fallback_report(run)


def _fallback_report(run: RunRecord) -> str:
    cls = run.triage.classification or "unknown"
    fix_note = f" PR opened: {run.pr_url}" if run.pr_url else ""
    return (
        f"Run {run.id[:8]} — {len(run.failures)} failure(s) classified as {cls} "
        f"(confidence {run.triage.confidence:.2f}). "
        f"Evidence: {run.triage.evidence or 'n/a'}.{fix_note}"
    )
