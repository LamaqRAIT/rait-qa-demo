"""
Smart suite selector — deterministic-first, Qwen3-Embedding-0.6B fallback.

Steps:
1. Get changed files from GitHub API for the trigger commit.
2. For each changed demo-site/*.html|css|js file, find tests that visit that page.
3. Also grep changed-file content for indexed selector strings.
4. If ≥1 confident match → use deterministic list (method="deterministic").
5. If 0 matches OR matches > 50% of total suite → Qwen3-Embedding-0.6B cosine similarity (method="embedding_fallback").
6. If embedding fails → full suite (method="fallback_all").

Embedding model replaces the LLM call: deterministic, inspectable scores, no API cost.
Model weights are baked into the Docker image at build time.
"""
import json
import os
import glob
import structlog
import app.db as db
from app.config import get_settings

log = structlog.get_logger()

SUITE_DIR = "tests/suite"


def _all_suite_files() -> list[str]:
    pattern = os.path.join(SUITE_DIR, "test_*.py")
    return [os.path.basename(f) for f in glob.glob(pattern)]


def _get_all_docstrings() -> dict[str, str]:
    """Returns {filename: first_docstring_or_comment}."""
    result = {}
    for fp in glob.glob(os.path.join(SUITE_DIR, "test_*.py")):
        fname = os.path.basename(fp)
        intent = ""
        try:
            with open(fp) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("# INTENT:"):
                        intent = stripped[len("# INTENT:"):].strip()
                        break
                    if stripped.startswith("# JOURNEY:"):
                        intent = stripped
                        break
        except Exception:
            pass
        result[fname] = intent
    return result


def _fetch_changed_files(commit_sha: str) -> list[dict]:
    """Returns list of {filename, patch} dicts from GitHub API."""
    settings = get_settings()
    if not settings.github_token or commit_sha in ("manual", "inject-drift", "scheduled"):
        return []
    try:
        from github import Github
        gh = Github(settings.github_token)
        repo = gh.get_repo(f"{settings.github_repo_owner}/{settings.github_repo_name}")
        commit = repo.get_commit(commit_sha)
        result = []
        for f in commit.files[:20]:
            result.append({
                "filename": f.filename,
                "status": f.status,
                "patch": (f.patch or "")[:400],
            })
        return result
    except Exception as exc:
        log.warning("suite_selector.github_error", sha=commit_sha[:7], error=str(exc)[:100])
        return []


async def _deterministic_select(changed_files: list[dict]) -> tuple[list[str], float]:
    """
    Returns (matched_test_files, max_confidence).
    Confidence: 1.0 if page_path exact match, 0.8 if selector text found in diff patch.
    """
    if not changed_files:
        return [], 0.0

    matched: dict[str, float] = {}

    for cf in changed_files:
        fname = cf["filename"]
        patch = cf.get("patch", "")
        # Extract filename from path: demo-site/checkout.html → checkout.html
        basename = os.path.basename(fname)

        # Query by page path
        if basename.endswith((".html", ".css", ".js")):
            rows = await db.query_selector_index_by_path(basename)
            for row in rows:
                tf = row["test_file"]
                matched[tf] = max(matched.get(tf, 0), 0.90)

        # Check if patch contains indexed selector strings (e.g. renamed CSS class)
        if patch:
            # Extract removed lines from diff (-lines)
            removed = " ".join(line[1:] for line in patch.splitlines() if line.startswith("-"))
            # Get all words from removed lines and check against selector index
            for word in set(removed.split()):
                if len(word) > 3:
                    rows = await db.query_selector_index_by_value(word)
                    for row in rows:
                        tf = row["test_file"]
                        matched[tf] = max(matched.get(tf, 0), 0.80)

    if not matched:
        return [], 0.0

    settings = get_settings()
    min_conf = settings.suite_selector_det_confidence
    # Sort by confidence descending
    ranked = sorted(matched.items(), key=lambda x: x[1], reverse=True)
    files = [k for k, v in ranked if v >= min_conf]
    max_conf = ranked[0][1] if ranked else 0.0
    return files, max_conf


_embedding_model = None


def _get_embedding_model():
    """Lazy-load Qwen3-Embedding-0.6B once, reuse across requests."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    try:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(
            "Qwen/Qwen3-Embedding-0.6B",
            trust_remote_code=True,
        )
        log.info("suite_selector.embedding_model_loaded")
    except Exception as exc:
        log.warning("suite_selector.embedding_model_load_failed", error=str(exc)[:120])
    return _embedding_model


async def _embedding_select(
    changed_files: list[dict],
    all_suites: list[str],
) -> tuple[list[str], bool, dict[str, float]]:
    """
    Qwen3-Embedding-0.6B cosine similarity to map changed files to suites.
    Replaces the LLM call — deterministic, inspectable, no API cost.
    Returns (selected_suites, hitl_recommended, scores_dict).
    """
    import asyncio
    import numpy as np

    model = _get_embedding_model()
    if model is None:
        return [], False, {}

    docstrings = _get_all_docstrings()

    # Build query from changed file paths + diff snippets
    query = " ".join(
        f"{c['filename']} {c.get('patch', '')[:200]}"
        for c in changed_files[:10]
    )

    # Suite documents: filename + intent comment
    suite_docs = [
        f"{fname}: {docstrings.get(fname, fname.replace('test_', '').replace('.py', ''))}"
        for fname in all_suites
    ]

    try:
        # Run CPU inference in executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        query_emb, doc_embs = await loop.run_in_executor(
            None,
            lambda: (
                model.encode(query, normalize_embeddings=True),
                model.encode(suite_docs, normalize_embeddings=True),
            ),
        )

        scores = np.dot(doc_embs, query_emb).tolist()
        scores_dict = {all_suites[i]: round(float(s), 4) for i, s in enumerate(scores)}

        settings = get_settings()
        threshold = getattr(settings, "suite_selector_embedding_threshold", 0.35)
        selected = [fname for fname, score in scores_dict.items() if score >= threshold]

        # Sort by score descending
        selected.sort(key=lambda f: scores_dict[f], reverse=True)

        # hitl if top two scores are very close (ambiguous which suite matters more)
        sorted_scores = sorted(scores, reverse=True)
        hitl = len(sorted_scores) >= 2 and (sorted_scores[0] - sorted_scores[1]) < 0.05

        log.info(
            "suite_selector.embedding",
            selected=selected,
            top_score=sorted_scores[0] if sorted_scores else 0,
            hitl=hitl,
        )
        return selected, hitl, scores_dict

    except Exception as exc:
        log.warning("suite_selector.embedding_failed", error=str(exc)[:120])
        return [], False, {}


async def select_suites(commit_sha: str, run_id: str = "") -> tuple[list[str], str, bool]:
    """
    Main entry point.
    Returns (suites_to_run, method, force_hitl).
    method = "deterministic" | "llm_fallback" | "fallback_all"
    """
    all_suites = _all_suite_files()
    if not all_suites:
        return [], "fallback_all", False

    # Always run all if no commit info
    if commit_sha in ("manual", "inject-drift", "scheduled", ""):
        return all_suites, "fallback_all", False

    changed_files = _fetch_changed_files(commit_sha)

    if not changed_files:
        log.info("suite_selector.no_changes", sha=commit_sha[:7])
        return all_suites, "fallback_all", False

    settings = get_settings()
    det_conf_floor = settings.suite_selector_det_confidence
    max_fraction = settings.suite_selector_max_fraction
    use_llm = settings.suite_selector_use_llm

    # Step 1: deterministic
    matched, confidence = await _deterministic_select(changed_files)

    if matched and confidence >= det_conf_floor:
        # Check if we're selecting > max_fraction of all suites (ambiguous → LLM)
        if len(matched) <= len(all_suites) * max_fraction:
            log.info("suite_selector.deterministic", suites=matched, confidence=confidence)
            return matched, "deterministic", False

    # Step 2: embedding cosine similarity (Qwen3-Embedding-0.6B)
    log.info("suite_selector.using_embedding", sha=commit_sha[:7], deterministic_matches=len(matched))
    try:
        emb_suites, hitl, scores_dict = await _embedding_select(changed_files, all_suites)
        if emb_suites:
            if run_id:
                await db.store_suite_selection_scores(run_id, scores_dict)
            return emb_suites, "embedding_fallback", hitl
    except Exception as exc:
        log.error("suite_selector.embedding_error", error=str(exc)[:100])

    # Step 3: full suite fallback
    return all_suites, "fallback_all", False
