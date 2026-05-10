"""
Triage node — Gemini direct SDK + Langfuse observability.
No LangChain. Classifies failures from evidence bundle.
"""
import asyncio
import json
import re
import structlog
import google.generativeai as genai
from app.config import get_settings
from app.core.state import TriageResult


async def _call_groq(prompt: str, api_key: str) -> tuple[str, int, int]:
    """Call Groq API. Returns (text, input_tokens, output_tokens)."""
    from groq import AsyncGroq
    client = AsyncGroq(api_key=api_key)
    resp = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=512,
    )
    text = resp.choices[0].message.content or ""
    usage = resp.usage
    return text, (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0)


async def _call_gemini(prompt: str, model_name: str) -> tuple[str, int, int]:
    """Call Gemini API. Returns (text, input_tokens, output_tokens)."""
    model = genai.GenerativeModel(model_name)
    response = await model.generate_content_async(prompt)
    raw = response.text
    usage = getattr(response, "usage_metadata", None)
    input_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
    output_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
    return raw, input_tok, output_tok

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
- If a commit message mentions changing text copy that matches the failing has-text selector → high confidence DRIFT
- If DOM inspection found a candidate selector with confidence > 0.80 → strong evidence of DRIFT
- If the selector still exists on the page (found_on_page=True) → cannot be DRIFT, likely BUG or ENV
- If the failure is a URL/redirect assertion (wait_for_url, to_have_url) and the redirect destination changed in a commit → BUG (application behavior broke)
- If multiple unrelated tests fail simultaneously → likely ENV
- proposed_fix must be null for bug and env classifications

Reply with ONLY valid JSON, no markdown fences:
{{"classification": "drift"|"bug"|"env", "confidence": 0.0-1.0, "evidence": "one sentence explaining the primary signal", "proposed_fix": null | {{"file": "backend/tests/suite/test_checkout.py", "old": "old_selector_string", "new": "new_selector_string"}}}}

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
    if settings.google_api_key:
        genai.configure(api_key=settings.google_api_key)

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

    raw = ""
    used_model = "groq/llama-3.3-70b-versatile"
    input_tokens = output_tokens = 0

    try:
        # 1st choice: Groq (generous free tier, fast)
        if settings.groq_api_key:
            raw, input_tokens, output_tokens = await _call_groq(prompt, settings.groq_api_key)
            used_model = "groq/llama-3.3-70b-versatile"
            log.info("triage.groq_ok", run_id=run_id, tokens=input_tokens + output_tokens)
        elif settings.google_api_key:
            # Gemini fallback — try models with 1 retry on 429
            for model_name in ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]:
                try:
                    for attempt in range(2):
                        try:
                            raw, input_tokens, output_tokens = await _call_gemini(prompt, model_name)
                            used_model = model_name
                            break
                        except Exception as e:
                            if "429" in str(e) and attempt == 0:
                                await asyncio.sleep(15)
                            else:
                                raise
                    break
                except Exception as e:
                    log.warning("triage.gemini_model_failed", model=model_name, error=str(e)[:80])
                    continue
        else:
            raise RuntimeError("No LLM API key configured (GROQ_API_KEY or GOOGLE_API_KEY)")

        raw = raw.strip()
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)

        classification = result.get("classification", "env")
        confidence = float(result.get("confidence", 0.5))
        evidence_text = result.get("evidence", "")
        proposed_fix = result.get("proposed_fix")
        cost_usd = (input_tokens * 0.000000059) + (output_tokens * 0.000000079)

        if trace:
            trace.generation(
                name="triage_call",
                model=used_model,
                input=prompt[:2000],
                output=raw[:1000],
                usage={"input": input_tokens, "output": output_tokens},
                metadata={"run_id": run_id, "classification": classification, "confidence": confidence},
            )
            lf.flush()

        log.info(
            "triage.done",
            run_id=run_id,
            model=used_model,
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
        log.error("triage.error", run_id=run_id, error=str(exc)[:200])
        if trace:
            lf.flush()
        return TriageResult(
            classification="env",
            confidence=0.5,
            evidence=f"Triage failed: {exc}",
            proposed_fix=None,
        )
