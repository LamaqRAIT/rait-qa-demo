"""
Auto-fixer node — PyGithub: read test file → patch selector → push branch → open PR.
Uses GitHub API only (no local git clone needed).
"""
import structlog
from app.config import get_settings
from app.core.state import RunRecord

log = structlog.get_logger()

TEST_FILE_PATH = "tests/suite/test_checkout.py"
LOGIN_FILE_PATH = "tests/suite/test_login.py"


async def auto_fix(run: RunRecord) -> str | None:
    settings = get_settings()
    proposed_fix = run.triage.proposed_fix
    if not proposed_fix:
        log.warning("auto_fixer.no_fix", run_id=run.id)
        return None

    file_path = proposed_fix.get("file", TEST_FILE_PATH)
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
        branch_name = f"qa-agent/heal-{run.id[:8]}"

        try:
            main_sha = repo.get_branch("main").commit.sha
            repo.create_git_ref(f"refs/heads/{branch_name}", main_sha)
        except GithubException as e:
            if e.status == 422:
                log.info("auto_fixer.branch_exists", run_id=run.id, branch=branch_name)
            else:
                raise

        repo.update_file(
            path=file_path,
            message=(
                f"fix(qa-agent): update selector '{old_str}' → '{new_str}'\n\n"
                f"Run ID: {run.id}\n"
                f"Confidence: {run.triage.confidence:.2f}\n"
                f"Evidence: {run.triage.evidence}"
            ),
            content=patched,
            sha=file_obj.sha,
            branch=branch_name,
        )

        pr = repo.create_pull(
            title=f"[QA Agent] Auto-heal selector: {old_str[:40]} → {new_str[:40]}",
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
