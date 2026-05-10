"""
Triage node — Gemini direct SDK + Langfuse observability.
No LangChain. Classifies failures from evidence bundle.
"""
import json
import re
import structlog
import google.generativeai as genai
from app.config import get_settings
from app.core.state import TriageResult

log = structlog.get_logger()

TRIAGE_PROMPT = """You are a QA triage agent. Classify the following test failures based on all available evidence.

FAILURES:
{failures}

DOM INSPECTION REPORT:
{dom_report}

RECENT COMMITS:
{recent_commits}

TEST HISTORY:
{test_history}

Classify as exactly one of:
- drift: The UI changed legitimately (CSS class renamed, text copy changed, layout updated) — the test selector is outdated, NOT a bug in the application
- bug: Application logic broke — the test is correct, the application behavior is wrong
- env: Infrastructure problem — network failure, timeout, missing service, canary check failed

Rules:
- If a commit message mentions renaming a CSS class that matches the failing selector → high confidence DRIFT
- If DOM inspection found a candidate selector with confidence > 0.80 → strong evidence of DRIFT
- If the selector still exists on the page (found_on_page=True) → cannot be DRIFT, likely BUG or ENV
- If multiple unrelated tests fail simultaneously → likely ENV

Reply with ONLY valid JSON, no markdown fences:
{{"classification": "drift"|"bug"|"env", "confidence": 0.0-1.0, "evidence": "one sentence explaining the primary signal", "proposed_fix": null | {{"file": "tests/suite/test_checkout.py", "old": "old_selector_string", "new": "new_selector_string"}}}}

proposed_fix MUST be null for bug and env classifications.
For drift, proposed_fix.file must be the test file containing the broken selector.
proposed_fix.old and proposed_fix.new must be exact strings that appear in the test file."""


def _init_langfuse():
    settings = get_settings()
    if not settings.langfuse_public_key:
        return None
    try:
        from langfuse import Langfuse
        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception:
        return None


async def triage(
    run_id: str,
    failures: list[dict],
    dom_report: dict,
    evidence: dict,
) -> TriageResult:
    settings = get_settings()
    genai.configure(api_key=settings.google_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = TRIAGE_PROMPT.format(
        failures=json.dumps(failures, indent=2),
        dom_report=json.dumps(dom_report, indent=2),
        recent_commits=json.dumps(evidence.get("recent_commits", []), indent=2),
        test_history=json.dumps(evidence.get("test_history", {}), indent=2),
    )

    lf = _init_langfuse()
    trace = None
    if lf:
        trace = lf.trace(name="triage", metadata={"run_id": run_id})

    try:
        response = await model.generate_content_async(prompt)
        raw = response.text.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)

        classification = result.get("classification", "env")
        confidence = float(result.get("confidence", 0.5))
        evidence_text = result.get("evidence", "")
        proposed_fix = result.get("proposed_fix")

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
        output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
        cost_usd = (input_tokens * 0.000000075) + (output_tokens * 0.0000003)

        if trace:
            trace.generation(
                name="triage_call",
                model="gemini-2.0-flash",
                input=prompt[:2000],
                output=raw[:1000],
                usage={"input": input_tokens, "output": output_tokens},
                metadata={"run_id": run_id, "classification": classification, "confidence": confidence},
            )
            lf.flush()

        log.info(
            "triage.done",
            run_id=run_id,
            classification=classification,
            confidence=confidence,
            tokens_in=input_tokens,
            tokens_out=output_tokens,
        )

        return TriageResult(
            classification=classification,
            confidence=confidence,
            evidence=evidence_text,
            proposed_fix=proposed_fix,
        )

    except Exception as exc:
        log.error("triage.error", run_id=run_id, error=str(exc))
        if trace:
            lf.flush()
        return TriageResult(
            classification="env",
            confidence=0.5,
            evidence=f"Triage failed: {exc}",
            proposed_fix=None,
        )
