"""
Triage node — unified LLM client (Claude Sonnet 4.6 → Groq → Gemini) + Langfuse.
Classifies test failures from a structured evidence bundle.
"""
import json
import structlog
from app.core.state import TriageResult
from app.core.intent_parser import extract_test_intents
from app.llm.client import call_llm, strip_json_fences

log = structlog.get_logger()

TRIAGE_PROMPT = """You are a QA triage agent for an e-commerce platform. Classify the following test failures using all available evidence.

FAILURES:
{failures}

TEST INTENTS (what each failing test is actually verifying at a business/behaviour level):
{test_intents}

DOM INSPECTION REPORT:
{dom_report}

RECENT COMMITS (last 5):
{recent_commits}

TEST HISTORY:
{test_history}

SUITE SELECTION METHOD: {suite_selection_method}

Classify as exactly one of:
- drift: The UI changed legitimately (CSS class renamed, text copy changed, layout updated) — the test selector is outdated, NOT a bug
- bug: Application logic broke — the test is correct but the application behavior is wrong (wrong redirect, wrong calculation, missing feature)
- env: Infrastructure problem — network failure, timeout, missing service, canary check failed

Classification rules (apply in order):
0. Read TEST INTENTS first. If a test intent explicitly states a required destination, value, or behaviour, and the failure directly violates that stated requirement, classify as bug with high confidence — even when DOM candidates exist and even when the commit message is vague.
1. If canary failed → env (confidence 0.95)
2. If a recent commit renames a CSS class/attribute that matches the failing selector → drift (confidence 0.90–0.97)
3. If a recent commit changes text copy matching a failing :has-text selector → drift (confidence 0.90–0.95)
4. If DOM inspection found a replacement candidate with confidence > 0.80 AND the change still satisfies the test intent → drift (confidence 0.85+)
5. If the selector still exists on the page (found_on_page=True) → NOT drift, likely bug or env
6. If a URL/redirect assertion fails AND the test intent explicitly names the expected destination → bug (confidence 0.92+)
7. If multiple unrelated tests from different suites fail simultaneously → env (confidence 0.85)
8. If failure is TimeoutError with no commit touching the affected file → env (confidence 0.70)
9. If failure is an assertion error (wrong value, wrong count, wrong state) with no matching commit → bug (confidence 0.75)

Reply with ONLY valid JSON, no markdown fences:
{{"classification": "drift"|"bug"|"env", "confidence": 0.0-1.0, "evidence": "one sentence citing the strongest signal", "proposed_fix": null | {{"file": "backend/tests/suite/test_checkout.py", "old": "exact_old_string_in_file", "new": "new_string_to_replace_with"}}}}

Rules for proposed_fix:
- MUST be null for bug and env.
- For drift: file must be the test file path, old/new must be exact strings appearing in that file.
- proposed_fix.old is typically the old CSS selector or text string used in the test.
- proposed_fix.new must be the new selector confirmed by DOM inspection (highest-confidence candidate)."""


async def triage(
    run_id: str,
    failures: list[dict],
    dom_report: dict,
    evidence: dict,
    suite_selection_method: str = "fallback_all",
) -> tuple[TriageResult, str | None, int, int, float]:
    """
    Returns (TriageResult, langfuse_trace_url, input_tokens, output_tokens, cost_usd).
    Note: we now return trace_url (full URL) instead of trace_id for direct UI linking.
    """
    test_intents = extract_test_intents(failures)
    prompt = TRIAGE_PROMPT.format(
        failures=json.dumps(failures, indent=2),
        test_intents=json.dumps(test_intents, indent=2),
        dom_report=json.dumps(dom_report, indent=2),
        recent_commits=json.dumps(evidence.get("recent_commits", []), indent=2),
        test_history=json.dumps(evidence.get("test_history", {}), indent=2),
        suite_selection_method=suite_selection_method,
    )

    try:
        raw, in_tok, out_tok, cost, model_used, trace_url = await call_llm(
            prompt=prompt,
            run_id=run_id,
            call_name="triage",
            model_preference="sonnet",
            max_tokens=512,
        )

        if not raw:
            # LLM unavailable — use deterministic evidence-based fallback
            log.warning("triage.llm_unavailable", run_id=run_id)
            return _deterministic_triage(failures, dom_report, evidence, test_intents)

        result = json.loads(strip_json_fences(raw))
        classification = result.get("classification", "env")
        confidence = float(result.get("confidence", 0.5))
        evidence_text = result.get("evidence", "")
        proposed_fix = result.get("proposed_fix")

        # Enforce invariant: proposed_fix must be null for non-drift
        if classification != "drift":
            proposed_fix = None

        log.info(
            "triage.done",
            run_id=run_id,
            model=model_used,
            classification=classification,
            confidence=confidence,
            tokens_in=in_tok,
            tokens_out=out_tok,
            cost=cost,
        )

        return (
            TriageResult(classification=classification, confidence=confidence, evidence=evidence_text, proposed_fix=proposed_fix),
            trace_url, in_tok, out_tok, cost,
        )

    except Exception as exc:
        log.error("triage.error", run_id=run_id, error=str(exc)[:200])
        return _deterministic_triage(failures, dom_report, evidence, test_intents)


def _infer_test_file(test_name: str) -> str:
    """Map a test name / class name to its test file path."""
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
) -> tuple[TriageResult, None, int, int, float]:
    """
    Rule-based fallback when LLM is unavailable.
    Uses DOM candidates + commit messages + error types to classify.
    Marked with '(deterministic)' in the evidence so it's auditable.
    """
    candidates = dom_report.get("changed_selectors", [])
    recent_commits = evidence.get("recent_commits", [])

    # Check for selector candidates from DOM inspection
    best_candidate = None
    best_conf = 0.0
    for c in candidates:
        if c.get("confidence", 0) > best_conf:
            best_conf = c["confidence"]
            best_candidate = c

    # Check commit messages for rename signals (use word-boundary-aware keyword matching to avoid false positives)
    import re as _re
    _RENAME_KEYWORDS = _re.compile(
        r'\b(?:rename|refactor|class|selector|copy|text|btn|css|label|aria)\b',
        _re.IGNORECASE,
    )
    commit_signals = []
    for commit in recent_commits:
        msg = commit.get("message", "").lower()
        if _RENAME_KEYWORDS.search(msg):
            commit_signals.append(msg)

    # Check for URL/redirect assertion failures (bug signal)
    url_failures = [f for f in failures if
        "url" in (f.get("raw", "") + f.get("selector", "")).lower()
        or "redirect" in f.get("raw", "").lower()
        or "wait_for_url" in f.get("raw", "").lower()
        or ("login" in f.get("test", "").lower() and "credential" in f.get("test", "").lower())
        or "to_have_url" in f.get("raw", "").lower()
    ]

    # Classification logic
    if best_conf >= 0.70 or commit_signals:
        # Strong DOM candidate or commit rename signal → drift
        confidence = min(0.88, max(best_conf, 0.72)) if best_conf > 0 else 0.72
        evidence_str = (
            f"Deterministic: DOM candidate '{best_candidate['found']}' (conf {best_conf:.2f}) + "
            f"commit signal: {commit_signals[0][:60] if commit_signals else 'n/a'}"
        ) if best_candidate else f"Deterministic: commit rename signal detected — {commit_signals[0][:80] if commit_signals else 'n/a'}"

        proposed_fix = None

        # Try to build proposed_fix from DOM candidate
        if best_candidate and failures:
            for f in failures:
                if f.get("selector"):
                    # Infer which test file the failure came from
                    test_name = f.get("test", "")
                    test_file = "backend/tests/suite/test_checkout.py"
                    if "login" in test_name.lower():
                        test_file = "backend/tests/suite/test_login.py"
                    elif "cart" in test_name.lower():
                        test_file = "backend/tests/suite/test_cart.py"
                    elif "search" in test_name.lower():
                        test_file = "backend/tests/suite/test_search.py"
                    elif "registr" in test_name.lower():
                        test_file = "backend/tests/suite/test_registration.py"
                    elif "nav" in test_name.lower():
                        test_file = "backend/tests/suite/test_navigation.py"
                    proposed_fix = {
                        "file": test_file,
                        "old": f["selector"],
                        "new": best_candidate["found"],
                    }
                    break

        # Fallback: infer from commit message when no DOM candidate
        if not proposed_fix and commit_signals and failures:
            import re as _re2
            # Try "rename X to Y" pattern in commit messages
            for msg in commit_signals:
                m = _re2.search(r'rename\s+([^\s]+)\s+to\s+([^\s\[\]]+)', msg, _re2.IGNORECASE)
                if m:
                    old_name, new_name = m.group(1), m.group(2)
                    for f in failures:
                        if f.get("selector"):
                            selector = f["selector"]
                            test_name = f.get("test", "")
                            test_file = _infer_test_file(test_name)
                            # Map the commit rename to selector strings used in tests
                            if old_name in selector or old_name.replace("-", "_") in selector:
                                proposed_fix = {
                                    "file": test_file,
                                    "old": selector,
                                    "new": selector.replace(old_name, new_name),
                                }
                                break
                    if proposed_fix:
                        break

        return (
            TriageResult(classification="drift", confidence=confidence, evidence=evidence_str + " (deterministic)", proposed_fix=proposed_fix),
            None, 0, 0, 0.0,
        )

    elif url_failures:
        # Boost confidence when a test intent explicitly names the redirect destination
        url_bug_confidence = 0.75
        intent_evidence = ""
        if test_intents:
            for item in test_intents:
                ti = (item.get("test_intent") or "").lower()
                if any(kw in ti for kw in ("/dashboard", "/home", "/products", "destination", "redirect", "not any other")):
                    url_bug_confidence = 0.88
                    intent_evidence = f" Intent confirms required destination: '{item.get('test_intent', '')}'"
                    break
        return (
            TriageResult(
                classification="bug",
                confidence=url_bug_confidence,
                evidence=f"Deterministic: URL/redirect assertion failure with no matching DOM candidate.{intent_evidence} (deterministic)",
                proposed_fix=None,
            ),
            None, 0, 0, 0.0,
        )

    else:
        return (
            TriageResult(
                classification="env",
                confidence=0.60,
                evidence="Deterministic: no DOM candidates, no commit signals, no URL assertion — infrastructure suspected (deterministic)",
                proposed_fix=None,
            ),
            None, 0, 0, 0.0,
        )
