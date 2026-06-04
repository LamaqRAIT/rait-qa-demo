"""
NLI (Natural Language Inference) service using DeBERTa-v3-small.
Model: cross-encoder/nli-deberta-v3-small (~86MB, ~50ms CPU inference).
Loaded once at startup, kept in memory as a singleton.

Used as Signal 3 in the confidence gate: checks whether the evidence text
actually entails the proposed classification label.
"""
import asyncio
import structlog

log = structlog.get_logger()

_pipeline = None


def init_nli() -> None:
    """Load the NLI model. Call from FastAPI lifespan startup."""
    global _pipeline
    try:
        from sentence_transformers import CrossEncoder
        _pipeline = CrossEncoder(
            "cross-encoder/nli-deberta-v3-small",
            max_length=512,
        )
        log.info("nli.loaded", model="cross-encoder/nli-deberta-v3-small")
    except Exception as exc:
        log.warning("nli.load_failed", error=str(exc)[:100])
        _pipeline = None


# Fixed hypothesis templates per classification label
_HYPOTHESES = {
    "drift":  "The test failure is caused by a UI selector change where the element still exists with a different identifier.",
    "bug":    "The test failure is caused by a functional application bug where the application behaves incorrectly.",
    "env":    "The test failure is caused by an infrastructure or environment problem unrelated to the application code.",
}


async def score_entailment(premise: str, classification: str) -> float:
    """
    Return the NLI entailment score (0.0–1.0) for the premise→hypothesis pair.
    Uses the fixed hypothesis for the given classification label.
    Returns 0.5 (neutral) if the model is not loaded.
    """
    if _pipeline is None:
        log.warning("nli.pipeline_not_loaded")
        return 0.5

    hypothesis = _HYPOTHESES.get(classification, _HYPOTHESES["env"])

    def _infer():
        scores = _pipeline.predict(
            [(premise, hypothesis)],
            apply_softmax=True,
        )
        # CrossEncoder NLI returns [contradiction, neutral, entailment]
        entailment_score = float(scores[0][2])
        return entailment_score

    try:
        from app.telemetry import get_tracer
        tracer = get_tracer()
        with tracer.start_as_current_span("nli.verify") as span:
            span.set_attribute("gen_ai.system", "deberta-nli")
            span.set_attribute("nli.model", "cross-encoder/nli-deberta-v3-small")
            span.set_attribute("nli.premise_length", len(premise))
            span.set_attribute("nli.classification", classification)
            loop = asyncio.get_event_loop()
            score = await loop.run_in_executor(None, _infer)
            span.set_attribute("nli.entailment_score", score)
        return score
    except Exception as exc:
        log.warning("nli.inference_error", error=str(exc)[:100])
        return 0.5
