"""
Auto-fixer node — PyGithub: read test file → patch selector → push branch → open PR.
Uses GitHub API only (no local git clone needed).
"""
import structlog
from app.config import get_settings
from app.core.state import RunRecord

log = structlog.get_logger()

TEST_FILE_PATH = "backend/tests/suite/test_checkout.py"
LOGIN_FILE_PATH = "backend/tests/suite/test_login.py"


_KNOWN_TEST_FILES = {
    "test_checkout.py": "backend/tests/suite/test_checkout.py",
    "test_login.py": "backend/tests/suite/test_login.py",
}


def _repo_path(local_path: str) -> str:
    """Normalize any LLM-generated test file path to the correct GitHub repo path."""
    import os
    basename = os.path.basename(local_path)
    if basename in _KNOWN_TEST_FILES:
        return _KNOWN_TEST_FILES[basename]
    if not local_path.startswith("backend/"):
        return f"backend/{local_path.lstrip('/')}"
    return local_path


async def auto_fix(run: RunRecord) -> str | None:
    settings = get_settings()
    proposed_fix = run.triage.proposed_fix
    if not proposed_fix:
        log.warning("auto_fixer.no_fix", run_id=run.id)
        return None

    file_path = _repo_path(proposed_fix.get("file", TEST_FILE_PATH))
    old_str = proposed_fix.get("old", "")
    new_str = proposed_fix.get("new", "")

    if not old_str or not new_str:
        log.warning("auto_fixer.missing_fix_fields", run_id=run.id, fix=proposed_fix)
        return None

    try:
        from github import Github, GithubException
        gh = Github(settings.github_token)
        repo = gh.get_repo(f"{settings.github_repo_owner}/{settings.github_repo_name}")

        file_obj = repo.get_contents(file_path)
        content = file_obj.decoded_content.decode("utf-8")

        if old_str not in content:
            log.warning(
                "auto_fixer.idempotency_guard",
                run_id=run.id,
                old=old_str,
                message="Target string not found — already fixed or file modified",
            )
            return None

        patched = content.replace(old_str, new_str, 1)
        branch_name = "qa-agent/auto-heal"
        main_sha = repo.get_branch("main").commit.sha

        # Close any previously open PRs from this branch so we always have exactly one
        for old_pr in repo.get_pulls(state="open", head=f"{settings.github_repo_owner}:{branch_name}", base="main"):
            old_pr.edit(state="closed")
            log.info("auto_fixer.old_pr_closed", run_id=run.id, pr=old_pr.number)

        # Reset (or create) the fixed branch to current main HEAD — clean base for every run
        try:
            ref = repo.get_git_ref(f"heads/{branch_name}")
            ref.edit(main_sha, force=True)
            log.info("auto_fixer.branch_reset", run_id=run.id, branch=branch_name)
        except GithubException as e:
            if e.status == 404:
                repo.create_git_ref(f"refs/heads/{branch_name}", main_sha)
                log.info("auto_fixer.branch_created", run_id=run.id, branch=branch_name)
            else:
                raise

        # Re-fetch file blob SHA from the freshly reset branch
        branch_file = repo.get_contents(file_path, ref=branch_name)

        repo.update_file(
            path=file_path,
            message=(
                f"fix(qa-agent): update selector '{old_str}' → '{new_str}'\n\n"
                f"Run ID: {run.id}\n"
                f"Confidence: {run.triage.confidence:.2f}\n"
                f"Evidence: {run.triage.evidence}"
            ),
            content=patched,
            sha=branch_file.sha,
            branch=branch_name,
        )

        # Open a fresh PR (old one was already closed above)
        pr = repo.create_pull(
            title=f"[QA Agent] Auto-heal: {old_str[:50]} → {new_str[:50]}",
            body=(
                f"**Automated fix by RAIT QA Agent**\n\n"
                f"| Field | Value |\n"
                f"|---|---|\n"
                f"| Run ID | `{run.id}` |\n"
                f"| Classification | `{run.triage.classification}` |\n"
                f"| Confidence | `{run.triage.confidence:.2f}` |\n"
                f"| Evidence | {run.triage.evidence} |\n\n"
                f"**Proposed change:**\n"
                f"```diff\n- {old_str}\n+ {new_str}\n```\n\n"
                f"Review and merge to apply the fix."
            ),
            head=branch_name,
            base="main",
        )

        log.info(
            "auto_fixer.pr_opened",
            run_id=run.id,
            pr_url=pr.html_url,
            branch=branch_name,
        )
        return pr.html_url

    except Exception as exc:
        log.error("auto_fixer.error", run_id=run.id, error=str(exc))
        return None
