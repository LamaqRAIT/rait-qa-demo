"""
Evidence bundle builder.
Aggregates failures + DOM report + recent commits + test history before any LLM call.
test_history is now populated from the DB (last 30 days), not from a simple counter.
"""
import structlog
from app.config import get_settings

log = structlog.get_logger()


def build_evidence_bundle(
    failures: list[dict],
    dom_report: dict,
    recent_commits: list[dict],
    test_history: dict,
) -> dict:
    return {
        "failures": failures,
        "dom_report": dom_report,
        "recent_commits": recent_commits,
        "test_history": test_history,
    }


def get_recent_commits(run_id: str) -> list[dict]:
    settings = get_settings()
    try:
        from github import Github
        gh = Github(settings.github_token)
        repo = gh.get_repo(f"{settings.github_repo_owner}/{settings.github_repo_name}")
        commits = list(repo.get_commits()[:5])
        return [
            {
                "sha": c.sha[:7],
                "message": c.commit.message.strip().split("\n")[0][:80],
                "changed_files": [f.filename for f in c.files[:10]],
                "hours_ago": round(
                    (
                        __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                        - c.commit.author.date
                    ).total_seconds() / 3600,
                    1,
                ),
            }
            for c in commits
        ]
    except Exception as exc:
        log.warning("evidence.commits.error", run_id=run_id, error=str(exc))
        return []


async def build_test_history(failures: list[dict], consecutive_failures: int) -> dict:
    """
    Query DB for last 30 days of run history, grouped by failing test name.
    Returns {test_name: {consecutive_failures, last_classification, last_confirmed_outcome, total_runs_30d}}
    for each test found in failures. Falls back to a simple counter when DB is unavailable.
    """
    failing_test_names = list({f.get("test", "") for f in failures if f.get("test")})
    if not failing_test_names:
        return _simple_history(consecutive_failures)

    try:
        import app.db as db
        history = {}
        for test_name in failing_test_names[:5]:
            record = await db.get_test_history(test_name, days=30)
            if record:
                history[test_name] = record

        if history:
            return history
    except Exception as exc:
        log.warning("evidence.test_history.db_error", error=str(exc)[:100])

    return _simple_history(consecutive_failures)


def _simple_history(consecutive_failures: int) -> dict:
    return {
        "last_5_results": (["fail"] * min(consecutive_failures, 5))
        + (["pass"] * max(0, 5 - consecutive_failures)),
        "consecutive_failures": consecutive_failures,
        "flakiness_score_7d": 0.0,
    }
