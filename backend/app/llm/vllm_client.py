"""
vLLM client — OpenAI-compatible HTTP client for the self-hosted vLLM/SGLang GPU service.

This is the sole LLM interface after the rework. No managed API fallbacks.
If the GPU box is unreachable, the caller receives an empty response and falls
back to _deterministic_triage() (no LLM involved).

Supports:
  - Step A: single-token classification with logprobs
  - Step B: schema-enforced fix extraction via guided_json (xgrammar on vLLM side)
"""
import json
import hashlib
import time
import httpx
import structlog
from app.config import get_settings

log = structlog.get_logger()

# JSON schema for Step B fix extraction (enforced by vLLM xgrammar guided decoding)
FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "file":  {"type": "string"},
        "old":   {"type": "string"},
        "new":   {"type": "string"},
    },
    "required": ["file", "old", "new"],
    "additionalProperties": False,
}

_CLASS_TOKENS = ["drift", "bug", "env"]


def _get_base_url() -> str:
    return get_settings().vllm_base_url.rstrip("/")


def _get_model() -> str:
    return get_settings().vllm_model


async def classify(
    prompt: str,
    run_id: str,
) -> tuple[str | None, float | None, float | None, int, int]:
    """
    Step A: single-token classification with logprobs.
    Returns (classification, p_class, logprob_margin, input_tokens, output_tokens).
    Returns (None, None, None, 0, 0) if the GPU box is unreachable.
    """
    settings = get_settings()
    base_url = _get_base_url()
    if not base_url:
        log.warning("vllm.classify.no_url", run_id=run_id)
        return None, None, None, 0, 0

    payload = {
        "model": _get_model(),
        "prompt": prompt,
        "max_tokens": 1,
        "temperature": 0.0,
        "logprobs": 3,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.vllm_timeout_s) as client:
            t0 = time.time()
            resp = await client.post(f"{base_url}/v1/completions", json=payload)
            ttft_ms = int((time.time() - t0) * 1000)

        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        token = choice["text"].strip().lower()
        # Map token to known labels
        classification = token if token in _CLASS_TOKENS else _closest_label(token)

        # Extract logprobs
        logprobs_obj = choice.get("logprobs") or {}
        top_logprobs = logprobs_obj.get("top_logprobs", [{}])
        token_logprobs = top_logprobs[0] if top_logprobs else {}

        # Convert log-probabilities to probabilities then get p_class and margin
        if token_logprobs:
            import math
            probs = {k: math.exp(v) for k, v in token_logprobs.items()}
            sorted_probs = sorted(probs.values(), reverse=True)
            # Find probability for the winning token
            p_class = probs.get(token, sorted_probs[0] if sorted_probs else 0.5)
            margin = (sorted_probs[0] - sorted_probs[1]) if len(sorted_probs) >= 2 else p_class
        else:
            p_class = 0.75  # conservative fallback
            margin = 0.10

        usage = data.get("usage", {})
        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)

        log.info(
            "vllm.classify.ok",
            run_id=run_id,
            classification=classification,
            p_class=round(p_class, 3),
            margin=round(margin, 3),
            ttft_ms=ttft_ms,
        )
        return classification, p_class, margin, in_tok, out_tok

    except httpx.ConnectError:
        log.warning("vllm.classify.unreachable", run_id=run_id, url=base_url)
        return None, None, None, 0, 0
    except Exception as exc:
        log.error("vllm.classify.error", run_id=run_id, error=str(exc)[:200])
        return None, None, None, 0, 0


async def extract_fix(
    prompt: str,
    run_id: str,
) -> tuple[dict | None, int, int]:
    """
    Step B: extract selector fix as schema-enforced JSON (guided decoding via xgrammar).
    Returns (fix_dict, input_tokens, output_tokens).
    fix_dict: {"file": ..., "old": ..., "new": ...}
    Returns (None, 0, 0) on failure.
    """
    settings = get_settings()
    base_url = _get_base_url()
    if not base_url:
        return None, 0, 0

    payload = {
        "model": _get_model(),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 256,
        "temperature": 0.0,
        "guided_json": FIX_SCHEMA,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.vllm_timeout_s) as client:
            resp = await client.post(f"{base_url}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        raw = data["choices"][0]["message"]["content"].strip()
        fix = json.loads(raw)

        # Validate required keys
        if not all(k in fix for k in ("file", "old", "new")):
            log.warning("vllm.extract_fix.missing_keys", run_id=run_id, raw=raw[:100])
            return None, 0, 0

        usage = data.get("usage", {})
        log.info("vllm.extract_fix.ok", run_id=run_id, file=fix.get("file", "")[:60])
        return fix, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

    except httpx.ConnectError:
        log.warning("vllm.extract_fix.unreachable", run_id=run_id)
        return None, 0, 0
    except Exception as exc:
        log.error("vllm.extract_fix.error", run_id=run_id, error=str(exc)[:200])
        return None, 0, 0


def build_classify_prompt(
    failures: list[dict],
    test_intents: list[dict],
    dom_report: dict,
    recent_commits: list[dict],
    test_history: dict,
    suite_selection_method: str,
) -> tuple[str, str]:
    """
    Build the Step A classification prompt.
    Returns (prompt_text, prompt_hash).
    """
    import json
    body = f"""You are a QA triage agent. Classify the following test failures.

FAILURES:
{json.dumps(failures, indent=2)}

TEST INTENTS:
{json.dumps(test_intents, indent=2)}

DOM INSPECTION REPORT:
{json.dumps(dom_report, indent=2)}

RECENT COMMITS:
{json.dumps(recent_commits, indent=2)}

TEST HISTORY:
{json.dumps(test_history, indent=2)}

SUITE SELECTION METHOD: {suite_selection_method}

Classification rules:
0. If test intent explicitly states a required destination/value/behaviour and failure violates it → bug
1. If canary failed → env
2. If recent commit renames CSS class matching failing selector → drift
3. If DOM inspection found replacement candidate >0.80 confidence and intent still satisfied → drift
4. If selector still exists on page (found_on_page=True) → NOT drift
5. If URL/redirect assertion fails and intent names expected destination → bug
6. If multiple unrelated suites fail simultaneously → env
7. If TimeoutError with no matching commit → env
8. If assertion error with no matching commit → bug

Reply with exactly one word (no punctuation, no explanation):"""

    prompt_hash = hashlib.sha256(body.encode()).hexdigest()[:16]
    return body, prompt_hash


def build_fix_prompt(
    classification: str,
    failures: list[dict],
    dom_report: dict,
    test_intents: list[dict],
) -> str:
    """Build the Step B fix extraction prompt (drift only)."""
    import json
    return f"""The test failures below are classified as DRIFT (UI selector change).
Extract the exact selector fix as JSON.

FAILURES:
{json.dumps(failures, indent=2)}

DOM INSPECTION (best candidates):
{json.dumps(dom_report.get('changed_selectors', [])[:3], indent=2)}

TEST INTENTS:
{json.dumps(test_intents, indent=2)}

Rules:
- file: the test file path (e.g. backend/tests/suite/test_checkout.py)
- old: the exact old selector string as it appears in the test file
- new: the new selector confirmed by DOM inspection (highest-confidence candidate)
- old and new must be the literal strings to find/replace in the test file

Output ONLY the JSON object, nothing else:"""


def _closest_label(token: str) -> str:
    """Map an unexpected token to the nearest classification label."""
    token = token.lower()
    if any(x in token for x in ("drift", "select", "css", "class", "ui")):
        return "drift"
    if any(x in token for x in ("bug", "error", "fail", "broken", "logic")):
        return "bug"
    return "env"
