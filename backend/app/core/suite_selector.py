"""
Smart suite selector — deterministic-first, LLM fallback.

Steps:
1. Get changed files from GitHub API for the trigger commit.
2. For each changed demo-site/*.html|css|js file, find tests that visit that page.
3. Also grep changed-file content for indexed selector strings.
4. If ≥1 confident match → use deterministic list (method="deterministic").
5. If 0 matches OR matches > 50% of total suite → use LLM (method="llm_fallback").
6. If LLM fails → full suite (method="fallback_all").
"""
import json
import os
import glob
import structlog
import app.db as db
from app.config import get_settings
from app.llm.client import call_llm, strip_json_fences

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

    # Sort by confidence descending
    ranked = sorted(matched.items(), key=lambda x: x[1], reverse=True)
    files = [k for k, v in ranked if v >= 0.70]
    max_conf = ranked[0][1] if ranked else 0.0
    return files, max_conf


async def _llm_select(changed_files: list[dict], all_suites: list[str]) -> tuple[list[str], bool]:
    """
    LLM call to map changed files to test suites.
    Returns (selected_suites, hitl_recommended).
    """
    docstrings = _get_all_docstrings()
    suite_descriptions = "\n".join(
        f"- {f}: {docstrings.get(f, 'no description')}"
        for f in all_suites
    )
    changed_summary = "\n".join(
        f"- {c['filename']} ({c['status']}): {c['patch'][:150]}"
        for c in changed_files[:10]
    )

    prompt = f"""You are a QA engineer. Given the following changed files from a git commit, 
determine which Playwright test suites should be run to catch potential regressions.

CHANGED FILES:
{changed_summary}

AVAILABLE TEST SUITES:
{suite_descriptions}

Rules:
- Select only the suites most likely to be affected by these changes.
- If changes span multiple pages/components, select all relevant suites.
- If the commit is clearly infrastructure/config (not UI), select none and recommend running all.
- Set hitl_recommended=true if the commit is ambiguous or touches many files, 
  meaning the triage classification is likely to have lower confidence.

Reply with ONLY valid JSON:
{{"suites": ["test_checkout.py", ...], "reason": "one sentence", "hitl_recommended": false}}"""

    raw, _, _, _, _, _ = await call_llm(
        prompt=prompt,
        run_id="suite-selector",
        call_name="suite_selection",
        model_preference="haiku",
        max_tokens=256,
    )

    if not raw:
        return [], False

    try:
        result = json.loads(strip_json_fences(raw))
        suites = [s for s in result.get("suites", []) if s in all_suites]
        hitl = bool(result.get("hitl_recommended", False))
        log.info("suite_selector.llm", suites=suites, hitl=hitl)
        return suites, hitl
    except Exception as exc:
        log.warning("suite_selector.llm_parse_error", error=str(exc)[:100])
        return [], False


async def select_suites(commit_sha: str) -> tuple[list[str], str, bool]:
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

    # Step 1: deterministic
    matched, confidence = await _deterministic_select(changed_files)

    if matched and confidence >= 0.70:
        # Check if we're selecting > 50% of all suites (ambiguous → LLM)
        if len(matched) <= len(all_suites) * 0.5:
            log.info("suite_selector.deterministic", suites=matched, confidence=confidence)
            return matched, "deterministic", False

    # Step 2: LLM fallback
    log.info("suite_selector.using_llm", sha=commit_sha[:7], deterministic_matches=len(matched))
    try:
        llm_suites, hitl = await _llm_select(changed_files, all_suites)
        if llm_suites:
            return llm_suites, "llm_fallback", hitl
    except Exception as exc:
        log.error("suite_selector.llm_error", error=str(exc)[:100])

    # Step 3: full suite fallback
    return all_suites, "fallback_all", False
