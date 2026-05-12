"""
Jira integration — persisted mock with optional real Jira Cloud API hook.
When JIRA_BASE_URL + JIRA_EMAIL + JIRA_API_TOKEN + JIRA_PROJECT_KEY are all set,
the ticket is also POSTed to the real Jira REST API v3.
Otherwise the ticket is stored locally in the tickets table and looks identical in the UI.
"""
import base64
import httpx
import structlog
import app.db as db
from app.config import get_settings
from app.core.state import RunRecord

log = structlog.get_logger()


def _jira_configured() -> bool:
    s = get_settings()
    return all([s.jira_base_url, s.jira_email, s.jira_api_token, s.jira_project_key])


async def _post_to_jira(title: str, description: str, issue_type: str = "Bug") -> tuple[str, str] | None:
    """Returns (jira_key, jira_url) or None on failure."""
    s = get_settings()
    token = base64.b64encode(f"{s.jira_email}:{s.jira_api_token}".encode()).decode()
    payload = {
        "fields": {
            "project": {"key": s.jira_project_key},
            "summary": title[:255],
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
            },
            "issuetype": {"name": issue_type},
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{s.jira_base_url.rstrip('/')}/rest/api/3/issue",
                json=payload,
                headers={
                    "Authorization": f"Basic {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                key = data.get("key", "")
                url = f"{s.jira_base_url.rstrip('/')}/browse/{key}"
                log.info("jira.created", key=key)
                return key, url
            else:
                log.warning("jira.api_error", status=resp.status_code, body=resp.text[:200])
                return None
    except Exception as exc:
        log.error("jira.error", error=str(exc)[:200])
        return None


async def file_bug_ticket(run: RunRecord) -> dict:
    """Create a bug ticket (persisted locally + optionally pushed to Jira)."""
    cls = run.triage.classification
    confidence = run.triage.confidence
    evidence = run.triage.evidence
    severity = "HIGH" if confidence >= 0.9 else "MEDIUM" if confidence >= 0.7 else "LOW"

    title = f"[QA Agent] Bug detected in run {run.id[:8]} — {evidence[:80]}"
    body = (
        f"**Automated bug report from RAIT QA Agent**\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Run ID | `{run.id}` |\n"
        f"| Classification | `{cls}` |\n"
        f"| Confidence | `{confidence:.2f}` |\n"
        f"| Severity | `{severity}` |\n"
        f"| Evidence | {evidence} |\n"
        f"| Branch | `{run.trigger_branch}` |\n"
        f"| Failures | {len(run.failures)} test(s) failed |\n"
    )

    ticket = await db.create_ticket(
        run_id=run.id,
        ticket_type="bug",
        classification=cls,
        severity=severity.lower(),
        title=title,
        body=body,
        team_id=run.team_id,
    )

    if _jira_configured():
        result = await _post_to_jira(title=title, description=body, issue_type="Bug")
        if result:
            jira_key, jira_url = result
            await db.update_ticket_jira(ticket["id"], jira_key, jira_url)
            ticket["jira_remote_id"] = jira_key
            ticket["jira_url"] = jira_url
            log.info("jira.bug_filed", key=jira_key, run_id=run.id)

    return ticket


async def file_env_ticket(run: RunRecord) -> dict:
    """Create an environment alert ticket."""
    evidence = run.triage.evidence
    title = f"[QA Agent] ENV alert in run {run.id[:8]} — {evidence[:80]}"
    body = (
        f"**Environment alert from RAIT QA Agent**\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Run ID | `{run.id}` |\n"
        f"| Evidence | {evidence} |\n"
        f"| Branch | `{run.trigger_branch}` |\n"
    )

    ticket = await db.create_ticket(
        run_id=run.id,
        ticket_type="env",
        classification="env",
        severity="medium",
        title=title,
        body=body,
        team_id=run.team_id,
    )

    if _jira_configured():
        result = await _post_to_jira(title=title, description=body, issue_type="Task")
        if result:
            jira_key, jira_url = result
            await db.update_ticket_jira(ticket["id"], jira_key, jira_url)
            ticket["jira_remote_id"] = jira_key
            ticket["jira_url"] = jira_url

    return ticket
