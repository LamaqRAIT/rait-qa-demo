"""
Triage node — two-step self-hosted vLLM inference.

Step A: classify only — one token (drift|bug|env) + logprobs → p_class + margin.
Step B: extract fix (drift only) — schema-enforced JSON via xgrammar guided decoding.

Fallback: if vLLM GPU box is unreachable → _deterministic_triage() (rule-based, no LLM).
When using deterministic fallback, p_class/margin are None and the confidence gate
will unconditionally route to human_review.

No managed API fallbacks (Groq/Gemini/Claude removed per rework decision).
"""
import time
import structlog
from app.core.state import TriageResult
from app.core.intent_parser import extract_test_intents
from app.llm.vllm_client import (
    classify,
    extract_fix,
    build_classify_prompt,
    build_fix_prompt,
)

log = structlog.get_logger()


async def triage(
    run_id: str,
    failures: list[dict],
    dom_report: dict,
    evidence: dict,
    suite_selection_method: str = "fallback_all",
) -> tuple[TriageResult, str | None, int, int, float, float | None, float | None, int | None, int | None]:
    """
    Returns:
        (TriageResult, otel_trace_url, input_tokens, output_tokens, cost_usd,
         p_class, logprob_margin, triage_ttft_ms, triage_total_ms)

    otel_trace_url is always None (OTel auto-instruments the HTTP call via httpx).
    cost_usd is 0.0 for self-hosted inference (no per-token billing).
    p_class / logprob_margin are None when deterministic fallback is used.
    """
    test_intents = extract_test_intents(failures)
    classify_prompt, prompt_hash = build_classify_prompt(
        failures=failures,
        test_intents=test_intents,
        dom_report=dom_report,
        recent_commits=evidence.get("recent_commits", []),
        test_history=evidence.get("test_history", {}),
        suite_selection_method=suite_selection_method,
    )

    t_total_start = time.time()

    # ── Step A: classify (1 token + logprobs) ──────────────────────────────────
    classification, p_class, logprob_margin, in_tok_a, out_tok_a = await classify(
        prompt=classify_prompt,
        run_id=run_id,
    )

    if classification is None:
        log.warning("triage.vllm_unreachable — deterministic fallback", run_id=run_id)
        return _deterministic_triage_extended(failures, dom_report, evidence, test_intents)

    ttft_ms = None  # vLLM reports TTFT via OTel spans (see telemetry.py)

    # ── Step B: extract fix (drift only) ──────────────────────────────────────
    proposed_fix = None
    in_tok_b = out_tok_b = 0
    if classification == "drift":
        fix_prompt = build_fix_prompt(
            classification=classification,
            failures=failures,
            dom_report=dom_report,
            test_intents=test_intents,
        )
        proposed_fix, in_tok_b, out_tok_b = await extract_fix(
            prompt=fix_prompt,
            run_id=run_id,
        )

    triage_total_ms = int((time.time() - t_total_start) * 1000)
    total_in_tok = in_tok_a + in_tok_b
    total_out_tok = out_tok_a + out_tok_b

    log.info(
        "triage.done",
        run_id=run_id,
        classification=classification,
        p_class=round(p_class, 3) if p_class else None,
        margin=round(logprob_margin, 3) if logprob_margin else None,
        tokens_in=total_in_tok,
        tokens_out=total_out_tok,
        total_ms=triage_total_ms,
    )

    # Evidence text for NLI (short summary from DOM + commit signals)
    evidence_text = _build_evidence_text(failures, dom_report, evidence)

    return (
        TriageResult(
            classification=classification,
            confidence=p_class or 0.0,   # kept for backward compat with UI
            evidence=evidence_text,
            proposed_fix=proposed_fix,
        ),
        None,           # trace_url — OTel handles this
        total_in_tok,
        total_out_tok,
        0.0,            # cost_usd — self-hosted, no per-token billing
        p_class,
        logprob_margin,
        ttft_ms,
        triage_total_ms,
    )


def _build_evidence_text(failures: list[dict], dom_report: dict, evidence: dict) -> str:
    """Build a short evidence summary string for NLI and DB storage."""
    parts = []
    if failures:
        parts.append(f"{len(failures)} test(s) failed")
        selectors = [f.get("selector") for f in failures if f.get("selector")]
        if selectors:
            parts.append(f"selector(s): {', '.join(selectors[:2])}")
    candidates = dom_report.get("changed_selectors", [])
    if candidates:
        best = max(candidates, key=lambda c: c.get("confidence", 0))
        parts.append(f"DOM candidate '{best.get('found', '')}' (conf {best.get('confidence', 0):.2f})")
    commits = evidence.get("recent_commits", [])
    if commits:
        parts.append(f"recent commit: '{commits[0].get('message', '')[:60]}'")
    return ". ".join(parts) or "No evidence available."


# ── Deterministic fallback (unchanged from original, no LLM) ─────────────────

def _infer_test_file(test_name: str) -> str:
    t = test_name.lower()
    if "login" in t:    return "backend/tests/suite/test_login.py"
    if "cart" in t:     return "backend/tests/suite/test_cart.py"
    if "search" in t:   return "backend/tests/suite/test_search.py"
    if "registr" in t:  return "backend/tests/suite/test_registration.py"
    if "account" in t:  return "backend/tests/suite/test_account.py"
    if "product" in t:  return "backend/tests/suite/test_products.py"
    if "nav" in t:      return "backend/tests/suite/test_navigation.py"
    return "backend/tests/suite/test_checkout.py"


def _deterministic_triage(
    failures: list[dict],
    dom_report: dict,
    evidence: dict,
    test_intents: list[dict] | None = None,
) -> tuple:
    """Backward-compat signature used in old code paths."""
    result = _deterministic_triage_extended(failures, dom_report, evidence, test_intents)
    # Return old 5-tuple: (TriageResult, trace_url, in_tok, out_tok, cost)
    tr, _, in_tok, out_tok, cost, *_ = result
    return tr, None, in_tok, out_tok, cost


def _deterministic_triage_extended(
    failures: list[dict],
    dom_report: dict,
    evidence: dict,
    test_intents: list[dict] | None = None,
) -> tuple:
    """
    Rule-based fallback. Returns extended tuple matching triage() signature.
    p_class and logprob_margin are None → confidence gate routes to human_review.
    """
    import re as _re
    candidates = dom_report.get("changed_selectors", [])
    recent_commits = evidence.get("recent_commits", [])

    best_candidate = None
    best_conf = 0.0
    for c in candidates:
        if c.get("confidence", 0) > best_conf:
            best_conf = c["confidence"]
            best_candidate = c

    _RENAME_KEYWORDS = _re.compile(
        r'\b(?:rename|refactor|class|selector|copy|text|btn|css|label|aria)\b',
        _re.IGNORECASE,
    )
    commit_signals = [c.get("message", "") for c in recent_commits if _RENAME_KEYWORDS.search(c.get("message", ""))]

    url_failures = [f for f in failures if
        "url" in (f.get("raw", "") + f.get("selector", "")).lower()
        or "redirect" in f.get("raw", "").lower()
        or "wait_for_url" in f.get("raw", "").lower()
        or "to_have_url" in f.get("raw", "").lower()
    ]

    evidence_text = ""
    if best_conf >= 0.70 or commit_signals:
        confidence = min(0.88, max(best_conf, 0.72)) if best_conf > 0 else 0.72
        evidence_text = (
            f"Deterministic: DOM candidate '{best_candidate['found']}' (conf {best_conf:.2f})"
            if best_candidate else f"Deterministic: commit rename signal"
        )
        proposed_fix = None
        if best_candidate and failures:
            for f in failures:
                if f.get("selector"):
                    proposed_fix = {
                        "file": _infer_test_file(f.get("test", "")),
                        "old": f["selector"],
                        "new": best_candidate["found"],
                    }
                    break
        result = TriageResult(
            classification="drift",
            confidence=confidence,
            evidence=evidence_text + " (deterministic)",
            proposed_fix=proposed_fix,
        )
        return result, None, 0, 0, 0.0, None, None, None, None

    elif url_failures:
        url_bug_confidence = 0.75
        if test_intents:
            for item in test_intents:
                ti = (item.get("test_intent") or "").lower()
                if any(kw in ti for kw in ("/dashboard", "/home", "/products", "destination", "redirect")):
                    url_bug_confidence = 0.88
                    break
        result = TriageResult(
            classification="bug",
            confidence=url_bug_confidence,
            evidence="Deterministic: URL/redirect assertion failure (deterministic)",
            proposed_fix=None,
        )
        return result, None, 0, 0, 0.0, None, None, None, None

    else:
        result = TriageResult(
            classification="env",
            confidence=0.60,
            evidence="Deterministic: no DOM candidates, no commit signals (deterministic)",
            proposed_fix=None,
        )
        return result, None, 0, 0, 0.0, None, None, None, None
