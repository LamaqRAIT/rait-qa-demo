"""
Confidence gate — replaces the model-generated confidence number with 5 independent signals.

Signal 1: p_class          — normalised logprob for the winning classification token
Signal 2: logprob_margin   — p(top1) − p(top2), catches a model torn between two labels
Signal 3: nli_entailment   — DeBERTa-v3-small: does the evidence entail the classification?
Signal 4: fix_grounded     — does proposed_fix.old exist verbatim in the test file?
Signal 5: dom_corroboration — inspector confidence score for the best DOM candidate

Route: auto_fix if ALL 5 signals pass their thresholds; human_review otherwise.
When logprobs are None (deterministic fallback), route unconditionally to human_review.
"""
import asyncio
import time
import structlog
from dataclasses import dataclass, field

log = structlog.get_logger()

# Thresholds (conservative starting values — calibrate after 50+ labelled runs)
_P_CLASS_MIN    = 0.75
_MARGIN_MIN     = 0.15
_NLI_MIN        = 0.50
_DOM_CORR_MIN   = 0.60


@dataclass
class GateResult:
    route: str                     # "auto_fix" | "human_review"
    p_class: float | None
    logprob_margin: float | None
    nli_entailment: float
    fix_grounded: bool | None
    dom_corroboration: float
    held_checks: list[str] = field(default_factory=list)   # names of failed signals
    nli_latency_ms: int = 0


async def evaluate(
    classification: str,
    p_class: float | None,
    logprob_margin: float | None,
    evidence_text: str,
    proposed_fix: dict | None,
    dom_report: dict,
) -> GateResult:
    """
    Evaluate all 5 signals and return a GateResult.
    Always routes to human_review when logprobs are None (GPU box unavailable).
    """
    held: list[str] = []

    # ── Signal 1: logprob level ───────────────────────────────────────────────
    if p_class is None:
        # No logprobs → GPU unavailable → always escalate
        return GateResult(
            route="human_review",
            p_class=None,
            logprob_margin=None,
            nli_entailment=0.5,
            fix_grounded=None,
            dom_corroboration=0.0,
            held_checks=["p_class_unavailable"],
            nli_latency_ms=0,
        )

    if p_class < _P_CLASS_MIN:
        held.append(f"p_class={p_class:.3f}<{_P_CLASS_MIN}")

    # ── Signal 2: logprob margin ──────────────────────────────────────────────
    if logprob_margin is None or logprob_margin < _MARGIN_MIN:
        held.append(f"margin={logprob_margin:.3f}<{_MARGIN_MIN}" if logprob_margin is not None else "margin_unavailable")

    # ── Signal 3: NLI entailment ──────────────────────────────────────────────
    t0 = time.time()
    try:
        from app.services.nli import score_entailment
        nli_score = await score_entailment(premise=evidence_text, classification=classification)
    except Exception as exc:
        log.warning("gate.nli_error", error=str(exc)[:100])
        nli_score = 0.5
    nli_latency_ms = int((time.time() - t0) * 1000)

    if nli_score < _NLI_MIN:
        held.append(f"nli={nli_score:.3f}<{_NLI_MIN}")

    # ── Signal 4: fix groundedness ────────────────────────────────────────────
    fix_grounded: bool | None = None
    if classification == "drift" and proposed_fix:
        fix_grounded = _check_fix_groundedness(proposed_fix)
        if fix_grounded is False:
            held.append("fix_not_grounded")
    elif classification == "drift" and not proposed_fix:
        held.append("no_proposed_fix")
        fix_grounded = False

    # ── Signal 5: DOM corroboration ───────────────────────────────────────────
    dom_corroboration = _get_dom_corroboration(dom_report)
    if classification == "drift" and dom_corroboration < _DOM_CORR_MIN:
        held.append(f"dom_corr={dom_corroboration:.3f}<{_DOM_CORR_MIN}")

    route = "auto_fix" if not held else "human_review"

    log.info(
        "gate.evaluated",
        classification=classification,
        route=route,
        p_class=round(p_class, 3),
        margin=round(logprob_margin, 3) if logprob_margin else None,
        nli=round(nli_score, 3),
        fix_grounded=fix_grounded,
        dom_corr=round(dom_corroboration, 3),
        held=held,
    )

    # OTel span with all 5 signal values as attributes
    try:
        import json as _json
        from app.telemetry import get_tracer
        tracer = get_tracer()
        with tracer.start_as_current_span("confidence.gate") as span:
            span.set_attribute("gate.p_class", p_class)
            span.set_attribute("gate.margin", logprob_margin or 0.0)
            span.set_attribute("gate.nli_entailment", nli_score)
            span.set_attribute("gate.fix_grounded", bool(fix_grounded))
            span.set_attribute("gate.dom_corroboration", dom_corroboration)
            span.set_attribute("gate.route", route)
            span.set_attribute("gate.held_checks", _json.dumps(held))
    except Exception:
        pass

    return GateResult(
        route=route,
        p_class=p_class,
        logprob_margin=logprob_margin,
        nli_entailment=nli_score,
        fix_grounded=fix_grounded,
        dom_corroboration=dom_corroboration,
        held_checks=held,
        nli_latency_ms=nli_latency_ms,
    )


def _check_fix_groundedness(proposed_fix: dict) -> bool:
    """Signal 4: confirm proposed_fix.old exists verbatim in the test file."""
    test_file = proposed_fix.get("file", "")
    old_str = proposed_fix.get("old", "")
    if not test_file or not old_str:
        return False
    try:
        with open(test_file, "r") as f:
            content = f.read()
        return old_str in content
    except FileNotFoundError:
        # Try relative path variants
        for prefix in ("", "backend/", "../"):
            try:
                with open(prefix + test_file, "r") as f:
                    content = f.read()
                return old_str in content
            except FileNotFoundError:
                continue
        log.warning("gate.fix_grounded.file_not_found", file=test_file)
        return False
    except Exception as exc:
        log.warning("gate.fix_grounded.error", error=str(exc)[:100])
        return False


def _get_dom_corroboration(dom_report: dict) -> float:
    """Signal 5: return the highest confidence score from DOM candidates."""
    candidates = dom_report.get("changed_selectors", [])
    if not candidates:
        return 0.0
    return max((c.get("confidence", 0.0) for c in candidates), default=0.0)
