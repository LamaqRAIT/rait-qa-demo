"""
Triage node — two-step triage when vLLM is available, single-step LLM otherwise.

Step A: one-token classification with logprobs → p_class, logprob_margin (gate signals)
Step B: fix extraction given the Step-A classification (evidence, confidence, proposed_fix)

Falls back to single-step LLM (Claude/Groq/Gemini) when vLLM is unavailable, and to
the deterministic rule engine when all LLM providers fail.
"""
import hashlib
import json
import math
import os
import structlog
from pydantic import BaseModel, field_validator, model_validator
from app.core.state import TriageResult
from app.core.intent_parser import extract_test_intents
from app.llm.client import call_llm, strip_json_fences, _call_vllm_with_logprobs

log = structlog.get_logger()

_VALID_CLASSIFICATIONS = {"drift", "bug", "env"}

# Gate thresholds
_P_CLASS_MIN = 0.50          # softmax prob of winning class must exceed this
_LOGPROB_MARGIN_MIN = 0.80   # top1-top2 nats — below this, two classes are too close
_DOM_CORR_MIN = 0.70         # for drift, DOM candidate must be at least this confident


class TriageOutput(BaseModel):
    """Strict schema for LLM triage response — validation errors trigger a one-shot retry."""
    classification: str
    confidence: float
    evidence: str
    proposed_fix: dict | None = None

    @field_validator("classification")
    @classmethod
    def valid_classification(cls, v: str) -> str:
        if v not in _VALID_CLASSIFICATIONS:
            raise ValueError(f"classification must be one of {_VALID_CLASSIFICATIONS}, got '{v}'")
        return v

    @field_validator("confidence")
    @classmethod
    def valid_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {v}")
        return round(v, 4)

    @model_validator(mode="after")
    def fix_non_null(self) -> "TriageOutput":
        if self.classification != "drift":
            self.proposed_fix = None
        return self


class FixOutput(BaseModel):
    """Schema for Step B fix extraction response."""
    confidence: float
    evidence: str
    proposed_fix: dict | None = None

    @field_validator("confidence")
    @classmethod
    def valid_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {v}")
        return round(v, 4)


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


# ── Prompts ───────────────────────────────────────────────────────────────────

STEP_A_PROMPT = """You are a QA triage expert. Classify these test failures as exactly ONE word.

FAILURES:
{failures_summary}

DOM INSPECTION (selector candidates found):
{dom_summary}

RECENT COMMITS:
{commits_summary}

TEST HISTORY:
{history_summary}

Choose exactly one: drift  bug  env

- drift: test selector is outdated (UI changed legitimately)
- bug: application logic broke (test is correct, app behavior is wrong)
- env: infrastructure problem (network, timeout, missing service)

Reply with ONLY one word: drift, bug, or env"""


STEP_B_PROMPT = """You are a QA triage expert. The classification for these test failures is: {cls}

FAILURES:
{failures}

DOM INSPECTION REPORT:
{dom_report}

RECENT COMMITS (last 5):
{recent_commits}

TEST HISTORY:
{test_history}

TEST INTENTS:
{test_intents}

Based on classification={cls}:
- confidence: how confident are you in this classification (0.0-1.0)?
- evidence: one sentence citing the strongest signal
- proposed_fix: for drift only — the exact test file edit needed. null for bug/env.

Reply with ONLY valid JSON, no markdown fences:
{{"confidence": 0.0-1.0, "evidence": "one sentence", "proposed_fix": null | {{"file": "backend/tests/suite/test_X.py", "old": "exact_selector_in_file", "new": "replacement_selector"}}}}

For proposed_fix.old: use the EXACT string from the failing test file (the old CSS selector or text string).
For proposed_fix.new: use the replacement found in DOM inspection (highest-confidence candidate)."""


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


# ── Gate signal helpers ───────────────────────────────────────────────────────

def _extract_gate_signals(
    logprob_entries: list[dict],
    chosen_cls: str,
) -> tuple[float, float]:
    """
    From a list of {token, logprob} dicts (top candidates at position 0),
    return (p_class, logprob_margin).
    p_class = softmax probability of the winning class.
    logprob_margin = logprob(winner) − logprob(second class).
    """
    cls_tokens = {"drift", "bug", "env"}
    scores: dict[str, float] = {}

    for entry in logprob_entries:
        tok = entry.get("token", "").strip().lower()
        lp = entry.get("logprob", -999.0)
        if tok in cls_tokens and tok not in scores:
            scores[tok] = lp

    if not scores:
        return 0.0, 0.0

    # p_class: softmax over the three class tokens we saw
    all_lps = list(scores.values())
    max_lp = max(all_lps)
    denom = sum(math.exp(lp - max_lp) for lp in all_lps)
    winner_lp = scores.get(chosen_cls, min(all_lps))
    p_class = math.exp(winner_lp - max_lp) / denom

    # logprob_margin: winner minus the next-best class
    sorted_lps = sorted(scores.values(), reverse=True)
    logprob_margin = sorted_lps[0] - sorted_lps[1] if len(sorted_lps) >= 2 else 10.0

    return round(p_class, 4), round(logprob_margin, 4)


def _compute_dom_corroboration(dom_report: dict) -> float:
    """Pull best DOM candidate confidence from the inspection report."""
    best = 0.0
    for c in dom_report.get("changed_selectors", []):
        best = max(best, c.get("confidence", 0.0))
    return round(best, 4)


def _compute_fix_grounded(proposed_fix: dict | None) -> bool | None:
    """
    Check whether proposed_fix.old actually appears in the named test file.
    Returns None when the file can't be read (not an error — just unverifiable).
    """
    if not proposed_fix:
        return None
    file_rel = proposed_fix.get("file", "")
    old_str = proposed_fix.get("old", "")
    if not file_rel or not old_str:
        return None
    # Test files live at /app/<file_rel> in the container
    candidates = [
        os.path.join("/app", file_rel),
        os.path.join("/app/backend", file_rel.lstrip("backend/")),
        file_rel,
    ]
    for path in candidates:
        try:
            content = open(path).read()
            return old_str in content
        except OSError:
            continue
    return None


def _run_confidence_gate(
    classification: str,
    p_class: float,
    logprob_margin: float,
    fix_grounded: bool | None,
    dom_corroboration: float,
) -> tuple[str, list[str]]:
    """
    Returns (gate_route, held_checks).
    gate_route: "auto_fix" | "human_review"
    held_checks: list of signal names that failed (empty = all green)
    """
    held: list[str] = []

    if p_class < _P_CLASS_MIN:
        held.append(f"p_class={p_class:.3f}<{_P_CLASS_MIN}")
    if logprob_margin < _LOGPROB_MARGIN_MIN:
        held.append(f"logprob_margin={logprob_margin:.3f}<{_LOGPROB_MARGIN_MIN}")

    if classification == "drift":
        if dom_corroboration < _DOM_CORR_MIN:
            held.append(f"dom_corroboration={dom_corroboration:.3f}<{_DOM_CORR_MIN}")
        if fix_grounded is False:
            held.append("fix_grounded=False")

    gate_route = "human_review" if held else "auto_fix"
    return gate_route, held


# ── Two-step triage (vLLM path) ───────────────────────────────────────────────

async def _two_step_triage(
    run_id: str,
    failures: list[dict],
    dom_report: dict,
    evidence: dict,
    test_intents: list[dict],
) -> tuple["TriageOutput | None", float, float, int, int, float]:
    """
    Step A: classification-only prompt with logprobs.
    Step B: fix extraction given Step A's classification.
    Returns (output, p_class, logprob_margin, in_tok, out_tok, cost).
    Returns (None, 0, 0, 0, 0, 0) if vLLM is unavailable or both steps fail.
    """
    from app.config import get_settings
    from app.llm.client import _compute_cost

    settings = get_settings()
    if not settings.vllm_base_url:
        return None, 0.0, 0.0, 0, 0, 0.0

    # Compact summaries for Step A (keep prompt short for fast one-token response)
    failures_summary = "; ".join(
        f"{f.get('test','?')}: {f.get('error','?')[:80]}" for f in failures[:5]
    )
    dom_summary = "; ".join(
        f"{c.get('original','?')}→{c.get('found','?')} ({c.get('confidence',0):.0%})"
        for c in dom_report.get("changed_selectors", [])[:3]
    ) or "none"
    commits_summary = "; ".join(
        c.get("message", "")[:60] for c in evidence.get("recent_commits", [])[:3]
    ) or "none"
    history_summary = json.dumps(evidence.get("test_history", {}).get("last_30_days", {}))

    step_a_prompt = STEP_A_PROMPT.format(
        failures_summary=failures_summary,
        dom_summary=dom_summary,
        commits_summary=commits_summary,
        history_summary=history_summary,
    )

    try:
        text_a, in_a, out_a, logprob_entries = await _call_vllm_with_logprobs(
            step_a_prompt, max_tokens=5, top_logprobs=5
        )
    except Exception as exc:
        log.warning("triage.step_a.failed", run_id=run_id, error=str(exc)[:120])
        return None, 0.0, 0.0, 0, 0, 0.0

    # Parse Step A classification
    chosen_cls = text_a.strip().lower().split()[0] if text_a.strip() else ""
    if chosen_cls not in _VALID_CLASSIFICATIONS:
        # Try to find a valid class in the logprob entries
        for entry in logprob_entries:
            tok = entry.get("token", "").strip().lower()
            if tok in _VALID_CLASSIFICATIONS:
                chosen_cls = tok
                break
    if chosen_cls not in _VALID_CLASSIFICATIONS:
        log.warning("triage.step_a.no_valid_class", run_id=run_id, raw=text_a[:80])
        return None, 0.0, 0.0, 0, 0, 0.0

    p_class, logprob_margin = _extract_gate_signals(logprob_entries, chosen_cls)
    log.info("triage.step_a.done", run_id=run_id, cls=chosen_cls, p_class=p_class, margin=logprob_margin)

    # Step B: fix extraction
    step_b_prompt = STEP_B_PROMPT.format(
        cls=chosen_cls,
        failures=json.dumps(failures, indent=2),
        dom_report=json.dumps(dom_report, indent=2),
        recent_commits=json.dumps(evidence.get("recent_commits", []), indent=2),
        test_history=json.dumps(evidence.get("test_history", {}), indent=2),
        test_intents=json.dumps(test_intents, indent=2),
    )

    try:
        from app.llm.client import _call_vllm
        text_b, in_b, out_b = await _call_vllm(step_b_prompt, max_tokens=400)
    except Exception as exc:
        log.warning("triage.step_b.failed", run_id=run_id, error=str(exc)[:120])
        # Return Step A classification with low confidence
        return (
            TriageOutput(
                classification=chosen_cls,
                confidence=p_class,
                evidence=f"Step A classification (Step B failed): {chosen_cls}",
                proposed_fix=None,
            ),
            p_class, logprob_margin, in_a, out_a, 0.0,
        )

    # Parse Step B JSON
    try:
        data_b = json.loads(strip_json_fences(text_b))
        fix_b = FixOutput(**data_b)
        output = TriageOutput(
            classification=chosen_cls,
            confidence=fix_b.confidence,
            evidence=fix_b.evidence,
            proposed_fix=fix_b.proposed_fix if chosen_cls == "drift" else None,
        )
    except Exception as exc:
        log.warning("triage.step_b.parse_failed", run_id=run_id, error=str(exc)[:120], raw=text_b[:200])
        output = TriageOutput(
            classification=chosen_cls,
            confidence=p_class,
            evidence=f"Two-step triage (Step B parse failed): classified as {chosen_cls}",
            proposed_fix=None,
        )

    total_in = in_a + in_b
    total_out = out_a + out_b
    cost = _compute_cost("vllm/gemma-4-26B-A4B", total_in, total_out)
    log.info("triage.step_b.done", run_id=run_id, confidence=output.confidence, tokens=total_in + total_out)

    return output, p_class, logprob_margin, total_in, total_out, cost


# ── Main entry point ──────────────────────────────────────────────────────────

async def triage(
    run_id: str,
    failures: list[dict],
    dom_report: dict,
    evidence: dict,
    suite_selection_method: str = "fallback_all",
) -> tuple[TriageResult, str | None, int, int, float, str, str, str]:
    """
    Returns (TriageResult, langfuse_trace_url, input_tokens, output_tokens, cost_usd,
             triage_prompt, triage_response, prompt_hash).
    """
    from app.config import get_settings

    settings = get_settings()
    test_intents = extract_test_intents(failures)

    # Build the single-step prompt (used as audit log even when two-step runs)
    prompt = TRIAGE_PROMPT.format(
        failures=json.dumps(failures, indent=2),
        test_intents=json.dumps(test_intents, indent=2),
        dom_report=json.dumps(dom_report, indent=2),
        recent_commits=json.dumps(evidence.get("recent_commits", []), indent=2),
        test_history=json.dumps(evidence.get("test_history", {}), indent=2),
        suite_selection_method=suite_selection_method,
    )
    phash = _prompt_hash(prompt)

    dom_corroboration = _compute_dom_corroboration(dom_report)

    try:
        # ── Path 1: two-step vLLM triage ──────────────────────────────────────
        if settings.vllm_base_url:
            validated, p_class, logprob_margin, in_tok, out_tok, cost = await _two_step_triage(
                run_id, failures, dom_report, evidence, test_intents
            )

            if validated is not None:
                fix_grounded = _compute_fix_grounded(validated.proposed_fix)
                gate_route, gate_held = _run_confidence_gate(
                    validated.classification, p_class, logprob_margin,
                    fix_grounded, dom_corroboration
                )
                log.info(
                    "triage.two_step.done",
                    run_id=run_id,
                    cls=validated.classification,
                    confidence=validated.confidence,
                    p_class=p_class,
                    logprob_margin=logprob_margin,
                    gate=gate_route,
                    held=gate_held,
                )
                return (
                    TriageResult(
                        classification=validated.classification,
                        confidence=validated.confidence,
                        evidence=validated.evidence,
                        proposed_fix=validated.proposed_fix,
                        p_class=p_class,
                        logprob_margin=logprob_margin,
                        fix_grounded=fix_grounded,
                        dom_corroboration=dom_corroboration,
                    ),
                    None, in_tok, out_tok, cost, prompt, "", phash,
                )

        # ── Path 2: single-step managed LLM (Claude/Groq/Gemini) ─────────────
        raw, in_tok, out_tok, cost, model_used, trace_url = await call_llm(
            prompt=prompt,
            run_id=run_id,
            call_name="triage",
            model_preference="sonnet",
            max_tokens=512,
        )

        if not raw:
            log.warning("triage.llm_unavailable", run_id=run_id)
            result, url, it, ot, c = _deterministic_triage(failures, dom_report, evidence, test_intents)
            result.dom_corroboration = dom_corroboration
            return result, url, it, ot, c, prompt, "", phash

        validated_single, raw = await _parse_and_validate(raw, prompt, run_id, in_tok, out_tok, cost, model_used)

        if validated_single is None:
            result, url, it, ot, c = _deterministic_triage(failures, dom_report, evidence, test_intents)
            result.dom_corroboration = dom_corroboration
            return result, url, it, ot, c, prompt, raw, phash

        log.info(
            "triage.single_step.done",
            run_id=run_id,
            model=model_used,
            classification=validated_single.classification,
            confidence=validated_single.confidence,
        )
        return (
            TriageResult(
                classification=validated_single.classification,
                confidence=validated_single.confidence,
                evidence=validated_single.evidence,
                proposed_fix=validated_single.proposed_fix,
                dom_corroboration=dom_corroboration,
            ),
            trace_url, in_tok, out_tok, cost, prompt, raw, phash,
        )

    except Exception as exc:
        log.error("triage.error", run_id=run_id, error=str(exc)[:200])
        result, url, it, ot, c = _deterministic_triage(failures, dom_report, evidence, test_intents)
        result.dom_corroboration = dom_corroboration
        return result, url, it, ot, c, prompt, "", phash


async def _parse_and_validate(
    raw: str,
    original_prompt: str,
    run_id: str,
    in_tok: int,
    out_tok: int,
    cost: float,
    model_used: str,
) -> tuple["TriageOutput | None", str]:
    """Parse + Pydantic-validate LLM output. One-shot retry on failure."""
    from pydantic import ValidationError

    def _try_parse(text: str) -> "TriageOutput | None":
        try:
            data = json.loads(strip_json_fences(text))
            return TriageOutput(**data)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            log.warning("triage.validation_failed", run_id=run_id, error=str(e)[:150], raw=text[:200])
            return None

    result = _try_parse(raw)
    if result is not None:
        return result, raw

    correction_prompt = (
        f"{original_prompt}\n\n"
        f"Your previous response was invalid:\n```\n{raw[:400]}\n```\n"
        "Fix the JSON so that:\n"
        '- classification is exactly one of: "drift", "bug", "env"\n'
        "- confidence is a float between 0.0 and 1.0\n"
        "- proposed_fix is null unless classification is drift\n"
        "Reply with ONLY the corrected JSON object, no markdown."
    )
    log.info("triage.retry_correction", run_id=run_id)
    retry_raw, _, _, _, _, _ = await call_llm(
        prompt=correction_prompt,
        run_id=run_id,
        call_name="triage_correction",
        model_preference="sonnet",
        max_tokens=512,
    )
    result = _try_parse(retry_raw) if retry_raw else None
    return result, retry_raw or raw


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
) -> tuple[TriageResult, None, int, int, float]:
    """Rule-based fallback when all LLM providers fail."""
    candidates = dom_report.get("changed_selectors", [])
    recent_commits = evidence.get("recent_commits", [])

    best_candidate = None
    best_conf = 0.0
    for c in candidates:
        if c.get("confidence", 0) > best_conf:
            best_conf = c["confidence"]
            best_candidate = c

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

    url_failures = [f for f in failures if
        "url" in (f.get("raw", "") + f.get("selector", "")).lower()
        or "redirect" in f.get("raw", "").lower()
        or "wait_for_url" in f.get("raw", "").lower()
        or ("login" in f.get("test", "").lower() and "credential" in f.get("test", "").lower())
        or "to_have_url" in f.get("raw", "").lower()
    ]

    if best_conf >= 0.70 or commit_signals:
        confidence = min(0.88, max(best_conf, 0.72)) if best_conf > 0 else 0.72
        evidence_str = (
            f"Deterministic: DOM candidate '{best_candidate['found']}' (conf {best_conf:.2f}) + "
            f"commit signal: {commit_signals[0][:60] if commit_signals else 'n/a'}"
        ) if best_candidate else f"Deterministic: commit rename signal — {commit_signals[0][:80] if commit_signals else 'n/a'}"

        proposed_fix = None
        if best_candidate and failures:
            for f in failures:
                if f.get("selector"):
                    test_file = _infer_test_file(f.get("test", ""))
                    proposed_fix = {"file": test_file, "old": f["selector"], "new": best_candidate["found"]}
                    break

        if not proposed_fix and commit_signals and failures:
            for msg in commit_signals:
                m = _re.search(r'rename\s+([^\s]+)\s+to\s+([^\s\[\]]+)', msg, _re.IGNORECASE)
                if m:
                    old_name, new_name = m.group(1), m.group(2)
                    for f in failures:
                        if f.get("selector"):
                            selector = f["selector"]
                            if old_name in selector or old_name.replace("-", "_") in selector:
                                proposed_fix = {
                                    "file": _infer_test_file(f.get("test", "")),
                                    "old": selector,
                                    "new": selector.replace(old_name, new_name),
                                }
                                break
                    if proposed_fix:
                        break

        return (
            TriageResult(
                classification="drift", confidence=confidence,
                evidence=evidence_str + " (deterministic)",
                proposed_fix=proposed_fix,
            ),
            None, 0, 0, 0.0,
        )

    elif url_failures:
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
                classification="bug", confidence=url_bug_confidence,
                evidence=f"Deterministic: URL/redirect assertion failure.{intent_evidence} (deterministic)",
                proposed_fix=None,
            ),
            None, 0, 0, 0.0,
        )

    else:
        return (
            TriageResult(
                classification="env", confidence=0.60,
                evidence="Deterministic: no DOM candidates, no commit signals, no URL assertion — infrastructure suspected (deterministic)",
                proposed_fix=None,
            ),
            None, 0, 0, 0.0,
        )
