"""
Unified LLM client: vLLM (self-hosted) → Claude → Groq → Gemini.
vLLM is the primary provider when VLLM_BASE_URL is set.
Falls back to managed APIs in order if vLLM is unreachable or returns an error.

Returns (text, input_tokens, output_tokens, model_used, cost_usd, trace_url).
Langfuse trace is created for every call.
"""
import asyncio
import json
import re
import structlog
from app.config import get_settings

log = structlog.get_logger()

# Per-model cost per token (USD) — vLLM is free (self-hosted)
_COST_TABLE = {
    "vllm/gemma-4-26B-A4B":            (0.0, 0.0),
    "claude-sonnet-4-6":               (3e-6, 15e-6),
    "claude-haiku-4-5-20251001":       (0.25e-6, 1.25e-6),
    "groq/llama-3.3-70b-versatile":    (5.9e-8, 7.9e-8),
    "gemini-2.0-flash":                (7.5e-8, 3e-7),
    "gemini-1.5-flash":                (7.5e-8, 3e-7),
}


def _compute_cost(model: str, in_tok: int, out_tok: int) -> float:
    in_rate, out_rate = _COST_TABLE.get(model, (1e-6, 2e-6))
    return round(in_tok * in_rate + out_tok * out_rate, 6)


_langfuse_client = None


def _get_langfuse():
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client
    settings = get_settings()
    if not settings.langfuse_public_key:
        return None
    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.get_langfuse_host(),
        )
        return _langfuse_client
    except Exception:
        return None


async def _call_vllm(prompt: str, max_tokens: int = 512) -> tuple[str, int, int]:
    """Call self-hosted vLLM via OpenAI-compatible chat completions endpoint."""
    import httpx
    settings = get_settings()
    url = f"{settings.vllm_base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": settings.vllm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=settings.vllm_timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    choice = data["choices"][0]
    text = choice["message"]["content"] or ""
    usage = data.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    return text, in_tok, out_tok


async def _call_vllm_with_logprobs(
    prompt: str,
    max_tokens: int = 5,
    top_logprobs: int = 3,
) -> tuple[str, int, int, list[dict]]:
    """
    Call vLLM requesting logprobs. Used by the two-step triage (Step A).
    Returns (text, in_tok, out_tok, logprob_entries) where each entry is
    {"token": str, "logprob": float} for the top candidates at position 0.
    """
    import httpx
    settings = get_settings()
    url = f"{settings.vllm_base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": settings.vllm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "logprobs": True,
        "top_logprobs": top_logprobs,
    }
    async with httpx.AsyncClient(timeout=settings.vllm_timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    choice = data["choices"][0]
    text = choice["message"]["content"] or ""
    usage = data.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)

    # Extract per-token logprob candidates from first token position
    logprob_entries: list[dict] = []
    lp_content = (choice.get("logprobs") or {}).get("content") or []
    if lp_content:
        for candidate in lp_content[0].get("top_logprobs", []):
            logprob_entries.append({
                "token": candidate.get("token", ""),
                "logprob": candidate.get("logprob", -999.0),
            })
    return text, in_tok, out_tok, logprob_entries


async def _call_claude(prompt: str, model: str = "claude-sonnet-4-6", max_tokens: int = 1024) -> tuple[str, int, int]:
    import anthropic
    settings = get_settings()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text if msg.content else ""
    return text, msg.usage.input_tokens, msg.usage.output_tokens


async def _call_groq(prompt: str, max_tokens: int = 512) -> tuple[str, int, int]:
    from groq import AsyncGroq
    settings = get_settings()
    client = AsyncGroq(api_key=settings.groq_api_key)
    resp = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=max_tokens,
    )
    text = resp.choices[0].message.content or ""
    usage = resp.usage
    return text, (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0)


async def _call_gemini(prompt: str, model_name: str = "gemini-2.0-flash") -> tuple[str, int, int]:
    import google.generativeai as genai
    settings = get_settings()
    genai.configure(api_key=settings.google_api_key)
    for attempt in range(2):
        try:
            model = genai.GenerativeModel(model_name, generation_config={"temperature": 0})
            response = await model.generate_content_async(prompt)
            raw = response.text
            usage = getattr(response, "usage_metadata", None)
            in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
            out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
            return raw, in_tok, out_tok
        except Exception as e:
            if "429" in str(e) and attempt == 0:
                await asyncio.sleep(15)
            else:
                raise


async def call_llm(
    prompt: str,
    run_id: str,
    call_name: str = "llm_call",
    model_preference: str = "sonnet",  # "sonnet" | "haiku" | "fast"
    max_tokens: int = 512,
) -> tuple[str, int, int, float, str, str | None]:
    """
    Returns (text, input_tokens, output_tokens, cost_usd, model_used, trace_url).
    Provider order: vLLM (self-hosted) → Claude → Groq → Gemini.
    """
    settings = get_settings()
    lf = _get_langfuse()
    trace = lf.trace(name=call_name, metadata={"run_id": run_id}) if lf else None

    raw = ""
    model_used = "none"
    in_tok = out_tok = 0

    claude_model = "claude-haiku-4-5-20251001" if model_preference == "haiku" else "claude-sonnet-4-6"

    try:
        # 1. vLLM (self-hosted Gemma 4 — primary when VLLM_BASE_URL is set)
        if settings.vllm_base_url:
            try:
                raw, in_tok, out_tok = await _call_vllm(prompt, max_tokens=max_tokens)
                model_used = "vllm/gemma-4-26B-A4B"
                log.info("llm.vllm_ok", run_id=run_id, call=call_name, tokens=in_tok + out_tok)
            except Exception as e:
                log.warning("llm.vllm_failed", run_id=run_id, error=str(e)[:120])

        # 2. Claude (managed API fallback)
        if not raw and settings.anthropic_api_key:
            try:
                raw, in_tok, out_tok = await _call_claude(prompt, model=claude_model, max_tokens=max_tokens)
                model_used = claude_model
                log.info("llm.claude_ok", run_id=run_id, call=call_name, tokens=in_tok + out_tok)
            except Exception as e:
                log.warning("llm.claude_failed", run_id=run_id, error=str(e)[:100])

        # 3. Groq
        if not raw and settings.groq_api_key:
            try:
                raw, in_tok, out_tok = await _call_groq(prompt, max_tokens=max_tokens)
                model_used = "groq/llama-3.3-70b-versatile"
                log.info("llm.groq_ok", run_id=run_id, call=call_name)
            except Exception as e:
                log.warning("llm.groq_failed", run_id=run_id, error=str(e)[:100])

        # 4. Gemini
        if not raw and settings.google_api_key:
            for gm in ["gemini-2.0-flash", "gemini-1.5-flash"]:
                try:
                    raw, in_tok, out_tok = await _call_gemini(prompt, model_name=gm)
                    model_used = gm
                    log.info("llm.gemini_ok", run_id=run_id, call=call_name, model=gm)
                    break
                except Exception as e:
                    log.warning("llm.gemini_failed", run_id=run_id, model=gm, error=str(e)[:100])

        if not raw:
            raise RuntimeError("No LLM provider available or all failed")

        cost = _compute_cost(model_used, in_tok, out_tok)

        trace_url = None
        if trace and lf:
            trace.generation(
                name=call_name,
                model=model_used,
                input=prompt[:2000],
                output=raw[:1000],
                usage={"input": in_tok, "output": out_tok},
                metadata={"run_id": run_id, "cost_usd": cost},
            )
            await asyncio.get_event_loop().run_in_executor(None, lf.flush)
            try:
                trace_url = trace.get_trace_url()
            except Exception:
                host = settings.get_langfuse_host().rstrip("/")
                trace_url = f"{host}/traces/{trace.id}"

        return raw, in_tok, out_tok, cost, model_used, trace_url

    except Exception as exc:
        log.error("llm.error", run_id=run_id, call=call_name, error=str(exc)[:200])
        if trace and lf:
            await asyncio.get_event_loop().run_in_executor(None, lf.flush)
        return "", 0, 0, 0.0, "none", None


def strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()
