"""
Embedding service using Qwen3-Embedding-0.6B.
Model: Qwen/Qwen3-Embedding-0.6B (~600MB RAM, ~20-50ms CPU inference).
Loaded once at startup as a singleton.

Used for suite selection (cosine similarity between changed-files query and
pre-computed suite catalogue embeddings).
"""
import asyncio
import numpy as np
import structlog

log = structlog.get_logger()

_model = None
_catalogue_embeddings: dict[str, np.ndarray] = {}


def init_embedding() -> None:
    """Load the embedding model. Call from FastAPI lifespan startup."""
    global _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
        log.info("embedding.loaded", model="Qwen/Qwen3-Embedding-0.6B")
    except Exception as exc:
        log.warning("embedding.load_failed", error=str(exc)[:100])
        _model = None


def encode_sync(text: str) -> np.ndarray | None:
    """Synchronous encode — call inside run_in_executor."""
    if _model is None:
        return None
    return _model.encode(text, normalize_embeddings=True)


async def encode(text: str) -> np.ndarray | None:
    """Async encode using thread executor to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, encode_sync, text)
    except Exception as exc:
        log.warning("embedding.encode_error", error=str(exc)[:100])
        return None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two normalised vectors (dot product since they're unit vectors)."""
    return float(np.dot(a, b))


async def precompute_catalogue(suite_descriptions: dict[str, str]) -> None:
    """
    Pre-compute embeddings for the suite catalogue and cache in memory.
    Call once at startup (or after index rebuild).
    suite_descriptions: {filename: intent_text}
    """
    global _catalogue_embeddings
    if _model is None:
        log.warning("embedding.catalogue.model_not_loaded")
        return

    def _compute():
        result = {}
        for name, desc in suite_descriptions.items():
            if desc:
                vec = encode_sync(desc)
                if vec is not None:
                    result[name] = vec
        return result

    loop = asyncio.get_event_loop()
    try:
        _catalogue_embeddings = await loop.run_in_executor(None, _compute)
        log.info("embedding.catalogue.computed", suites=len(_catalogue_embeddings))
    except Exception as exc:
        log.warning("embedding.catalogue.error", error=str(exc)[:100])


def get_catalogue_embeddings() -> dict[str, np.ndarray]:
    return _catalogue_embeddings
