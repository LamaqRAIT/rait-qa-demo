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
    Query qa_runs for the last 30 days to build a real history object.
    Gives the triage LLM genuine recency signal instead of a static counter.
    Falls back to counter-only if the DB query fails.
    """
    import app.db as db
    from datetime import datetime, timedelta, timezone

    base = {
        "consecutive_failures": consecutive_failures,
        "flakiness_score_7d": 0.0,
    }

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        recent_runs = await db.get_recent_runs_since(cutoff, limit=50)

        if not recent_runs:
            base["last_5_results"] = (["fail"] * min(consecutive_failures, 5)) + (["pass"] * max(0, 5 - consecutive_failures))
            return base

        total = len(recent_runs)
        counts: dict[str, int] = {"drift": 0, "bug": 0, "env": 0, "unknown": 0}
        auto_fixed = 0
        human_overrides = 0
        recent_classes: list[str] = []

        for r in recent_runs:
            cls = r.get("classification") or "unknown"
            counts[cls] = counts.get(cls, 0) + 1
            if r.get("pr_url"):
                auto_fixed += 1
            if r.get("human_override"):
                human_overrides += 1
            if len(recent_classes) < 10:
                recent_classes.append(cls)

        # Flakiness: fraction of non-drift, non-unknown runs in last 7 days
        cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
        runs_7d = [r for r in recent_runs if r.get("created_at", datetime.min.replace(tzinfo=timezone.utc)) >= cutoff_7d]
        if runs_7d:
            flaky = sum(1 for r in runs_7d if r.get("classification") in ("bug", "env"))
            base["flakiness_score_7d"] = round(flaky / len(runs_7d), 2)

        return {
            **base,
            "last_30_days": {
                "total_runs": total,
                "drift": counts.get("drift", 0),
                "bug": counts.get("bug", 0),
                "env": counts.get("env", 0),
                "auto_fixed": auto_fixed,
                "human_override_rate": round(human_overrides / total, 2) if total else 0.0,
            },
            "recent_classifications": recent_classes,
            "last_5_results": recent_classes[:5] if recent_classes else (
                (["fail"] * min(consecutive_failures, 5)) + (["pass"] * max(0, 5 - consecutive_failures))
            ),
        }

    except Exception as exc:
        log.warning("evidence.test_history.error", error=str(exc)[:120])
        base["last_5_results"] = (["fail"] * min(consecutive_failures, 5)) + (["pass"] * max(0, 5 - consecutive_failures))
        return base
