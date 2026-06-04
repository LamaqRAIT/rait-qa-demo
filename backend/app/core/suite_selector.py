"""
Smart suite selector — deterministic-first, embedding cosine similarity fallback.

Tier 1 (deterministic): match changed file paths against selector_test_index.
Tier 2 (embedding): Qwen3-Embedding-0.6B cosine similarity vs pre-computed suite catalogue.
Tier 3 (fallback_all): infrastructure changes, errors, or no commit info.

The LLM fallback has been removed. Suite selection is now fully deterministic and inspectable.
The same changed files always produce the same suite scores.
"""
import json
import os
import glob
import structlog
import app.db as db
from app.config import get_settings

log = structlog.get_logger()

SUITE_DIR = "tests/suite"
EMBEDDING_THRESHOLD = 0.35   # minimum cosine similarity to include a suite


def _all_suite_files() -> list[str]:
    pattern = os.path.join(SUITE_DIR, "test_*.py")
    return [os.path.basename(f) for f in glob.glob(pattern)]


def _get_all_intents() -> dict[str, str]:
    """Returns {filename: intent_text} from # INTENT: comments in each test file."""
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
        result[fname] = intent or fname.replace("test_", "").replace(".py", "").replace("_", " ")
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
    Confidence: 0.90 if page_path exact match, 0.80 if selector text found in diff.
    """
    if not changed_files:
        return [], 0.0

    matched: dict[str, float] = {}

    for cf in changed_files:
        fname = cf["filename"]
        patch = cf.get("patch", "")
        basename = os.path.basename(fname)

        if basename.endswith((".html", ".css", ".js")):
            rows = await db.query_selector_index_by_path(basename)
            for row in rows:
                tf = row["test_file"]
                matched[tf] = max(matched.get(tf, 0), 0.90)

        if patch:
            removed = " ".join(line[1:] for line in patch.splitlines() if line.startswith("-"))
            for word in set(removed.split()):
                if len(word) > 3:
                    rows = await db.query_selector_index_by_value(word)
                    for row in rows:
                        tf = row["test_file"]
                        matched[tf] = max(matched.get(tf, 0), 0.80)

    if not matched:
        return [], 0.0

    ranked = sorted(matched.items(), key=lambda x: x[1], reverse=True)
    files = [k for k, v in ranked if v >= 0.70]
    max_conf = ranked[0][1] if ranked else 0.0
    return files, max_conf


async def _embedding_select(
    changed_files: list[dict],
    all_suites: list[str],
) -> tuple[list[str], dict[str, float]]:
    """
    Tier 2: Qwen3-Embedding-0.6B cosine similarity.
    Returns (selected_suites, {suite: score}) where score >= EMBEDDING_THRESHOLD.
    Falls back to ([], {}) if the embedding model is not loaded.
    """
    try:
        from app.services.embedding import encode, cosine_similarity, get_catalogue_embeddings
    except ImportError:
        log.warning("suite_selector.embedding.import_failed")
        return [], {}

    catalogue = get_catalogue_embeddings()
    if not catalogue:
        log.warning("suite_selector.embedding.catalogue_empty")
        return [], {}

    # Build query: summary of changed file names + diff snippets
    changed_summary_parts = []
    for cf in changed_files[:10]:
        changed_summary_parts.append(cf["filename"])
        patch = cf.get("patch", "")
        if patch:
            removed = " ".join(
                line[1:].strip() for line in patch.splitlines()
                if line.startswith("-") and len(line) > 2
            )[:200]
            if removed:
                changed_summary_parts.append(removed)

    query_text = " ".join(changed_summary_parts)
    if not query_text:
        return [], {}

    query_vec = await encode(query_text)
    if query_vec is None:
        return [], {}

    scores: dict[str, float] = {}
    for suite_name, suite_vec in catalogue.items():
        if suite_name in all_suites:
            scores[suite_name] = cosine_similarity(query_vec, suite_vec)

    selected = sorted(
        [name for name, score in scores.items() if score >= EMBEDDING_THRESHOLD],
        key=lambda n: scores[n],
        reverse=True,
    )

    log.info("suite_selector.embedding", selected=selected, scores={k: round(v, 3) for k, v in scores.items()})
    return selected, scores


async def select_suites(commit_sha: str) -> tuple[list[str], str, bool, dict[str, float]]:
    """
    Main entry point.
    Returns (suites_to_run, method, force_hitl, embedding_scores).
    method = "deterministic" | "embedding" | "fallback_all"
    embedding_scores = {suite: cosine_score} — empty for non-embedding paths.
    """
    all_suites = _all_suite_files()
    if not all_suites:
        return [], "fallback_all", False, {}

    if commit_sha in ("manual", "inject-drift", "scheduled", ""):
        return all_suites, "fallback_all", False, {}

    changed_files = _fetch_changed_files(commit_sha)
    if not changed_files:
        log.info("suite_selector.no_changes", sha=commit_sha[:7])
        return all_suites, "fallback_all", False, {}

    # Tier 1: deterministic
    matched, confidence = await _deterministic_select(changed_files)
    if matched and confidence >= 0.70 and len(matched) <= len(all_suites) * 0.5:
        log.info("suite_selector.deterministic", suites=matched, confidence=confidence)
        return matched, "deterministic", False, {}

    # Tier 2: embedding cosine similarity
    log.info("suite_selector.using_embedding", sha=commit_sha[:7], deterministic_matches=len(matched))
    try:
        embedding_suites, scores = await _embedding_select(changed_files, all_suites)
        if embedding_suites:
            return embedding_suites, "embedding", False, scores
    except Exception as exc:
        log.error("suite_selector.embedding_error", error=str(exc)[:100])

    # Tier 3: full suite fallback
    return all_suites, "fallback_all", False, {}
