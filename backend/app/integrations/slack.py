"""
Slack notification dispatcher using incoming webhooks.
Falls back to in-app notification if no webhook URL is configured.
All notifications are also persisted to the notifications table.
"""
import json
import structlog
import httpx
import app.db as db
from app.config import get_settings

log = structlog.get_logger()


def _ui_run_url(run_id: str) -> str:
    settings = get_settings()
    base = settings.agent_ui_url.rstrip("/") or "http://localhost:3000"
    return f"{base}?run_id={run_id}"


async def _post_to_slack(blocks: list, text: str) -> bool:
    settings = get_settings()
    if not settings.slack_webhook_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                settings.slack_webhook_url,
                json={"text": text, "blocks": blocks},
            )
            if resp.status_code != 200:
                log.warning("slack.send_failed", status=resp.status_code, body=resp.text[:200])
                return False
        return True
    except Exception as exc:
        log.error("slack.error", error=str(exc)[:200])
        return False


async def notify_bug(run_id: str, ticket_key: str, evidence: str, confidence: float, team_id: str = "core-platform") -> None:
    severity = "HIGH" if confidence >= 0.9 else "MEDIUM"
    title = f":red_circle: Bug Detected — {ticket_key}"
    message = f"Run `{run_id[:8]}` classified as BUG ({severity}) — {evidence}"
    run_url = _ui_run_url(run_id)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🔴 Bug Detected — {ticket_key}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Severity:* {severity}"},
            {"type": "mrkdwn", "text": f"*Run:* `{run_id[:8]}`"},
            {"type": "mrkdwn", "text": f"*Confidence:* {confidence:.0%}"},
            {"type": "mrkdwn", "text": f"*Evidence:* {evidence[:120]}"},
        ]},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "View Run →"}, "url": run_url, "style": "danger"},
        ]},
    ]

    sent = await _post_to_slack(blocks, text=message)
    status = "sent" if sent else ("disabled" if not get_settings().slack_webhook_url else "failed")
    await db.create_notification(
        channel="slack" if sent else "inapp",
        event_type="bug_filed",
        title=title,
        message=message,
        run_id=run_id,
        team_id=team_id,
        status=status,
        payload={"ticket_key": ticket_key, "confidence": confidence},
    )
    log.info("notify.bug", run_id=run_id, ticket=ticket_key, sent_slack=sent)


async def notify_env(run_id: str, evidence: str, team_id: str = "core-platform") -> None:
    title = ":warning: Environment Alert"
    message = f"Run `{run_id[:8]}` failed canary — environment issue detected: {evidence}"
    run_url = _ui_run_url(run_id)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "⚠️ Environment Failure Detected"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Run:* `{run_id[:8]}`"},
            {"type": "mrkdwn", "text": f"*Cause:* {evidence[:150]}"},
        ]},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "View Run →"}, "url": run_url},
        ]},
    ]

    sent = await _post_to_slack(blocks, text=message)
    status = "sent" if sent else ("disabled" if not get_settings().slack_webhook_url else "failed")
    await db.create_notification(
        channel="slack" if sent else "inapp",
        event_type="env_alert",
        title=title,
        message=message,
        run_id=run_id,
        team_id=team_id,
        status=status,
    )
    log.info("notify.env", run_id=run_id, sent_slack=sent)


async def notify_hitl(run_id: str, confidence: float, proposed_fix: dict | None, team_id: str = "core-platform") -> None:
    title = ":eyes: Human Approval Required"
    fix_info = f"`{proposed_fix.get('old')}` → `{proposed_fix.get('new')}`" if proposed_fix else "no proposed fix"
    message = f"Run `{run_id[:8]}` requires human approval (confidence {confidence:.0%}). Fix: {fix_info}"
    run_url = _ui_run_url(run_id)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "👀 Human Approval Required"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Run:* `{run_id[:8]}`"},
            {"type": "mrkdwn", "text": f"*Confidence:* {confidence:.0%} (below threshold)"},
            {"type": "mrkdwn", "text": f"*Proposed fix:* {fix_info[:150]}"},
        ]},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Review & Approve →"}, "url": run_url, "style": "primary"},
        ]},
    ]

    sent = await _post_to_slack(blocks, text=message)
    status = "sent" if sent else ("disabled" if not get_settings().slack_webhook_url else "failed")
    await db.create_notification(
        channel="slack" if sent else "inapp",
        event_type="hitl_requested",
        title=title,
        message=message,
        run_id=run_id,
        team_id=team_id,
        status=status,
        payload={"confidence": confidence},
    )
    log.info("notify.hitl", run_id=run_id, sent_slack=sent)


async def notify_pr_opened(run_id: str, pr_url: str, team_id: str = "core-platform") -> None:
    title = ":white_check_mark: Auto-Fix PR Opened"
    message = f"QA Agent opened a healing PR for run `{run_id[:8]}`: {pr_url}"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "✅ Auto-Fix PR Opened"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Run:* `{run_id[:8]}`\n*PR:* {pr_url}"}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Review PR →"}, "url": pr_url},
        ]},
    ]

    sent = await _post_to_slack(blocks, text=message)
    status = "sent" if sent else ("disabled" if not get_settings().slack_webhook_url else "failed")
    await db.create_notification(
        channel="slack" if sent else "inapp",
        event_type="pr_opened",
        title=title,
        message=message,
        run_id=run_id,
        team_id=team_id,
        status=status,
        payload={"pr_url": pr_url},
    )
    log.info("notify.pr_opened", run_id=run_id, pr=pr_url, sent_slack=sent)


async def notify_circuit_breaker(event_type: str, message_text: str, meta: dict | None = None) -> None:
    title = f":rotating_light: Circuit Breaker: {event_type}"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🚨 Circuit Breaker: {event_type}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": message_text}},
    ]
    sent = await _post_to_slack(blocks, text=message_text)
    status = "sent" if sent else ("disabled" if not get_settings().slack_webhook_url else "failed")
    await db.create_notification(
        channel="slack" if sent else "inapp",
        event_type="circuit_breaker_fired",
        title=title,
        message=message_text,
        status=status,
        payload=meta or {},
    )
    log.info("notify.circuit_breaker", event=event_type, sent_slack=sent)
