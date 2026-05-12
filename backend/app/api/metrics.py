from fastapi import APIRouter, HTTPException
from typing import Optional
import app.db as db
from app.core.circuit_breaker import get_effective_threshold

router = APIRouter()


@router.get("/metrics/summary")
async def metrics_summary(days: int = 7):
    try:
        return await db.get_metrics_summary(days=days)
    except Exception:
        return {"total_runs": 0, "success_runs": 0, "success_rate": 0.0, "avg_cost_usd": 0.0, "total_cost_usd": 0.0, "avg_confidence": 0.0}


@router.get("/metrics/classifications")
async def metrics_classifications(days: int = 30):
    try:
        return await db.get_classification_distribution(days=days)
    except Exception:
        return []


@router.get("/metrics/accuracy")
async def metrics_accuracy(days: int = 30):
    try:
        return await db.get_override_rate(days=days)
    except Exception:
        return {"total": 0, "overrides": 0, "override_rate": 0.0}


@router.get("/metrics/cost")
async def metrics_cost(since: int = 30):
    try:
        return await db.get_cost_stats(days=since)
    except Exception:
        return {"total": 0.0, "p50": 0.0, "p95": 0.0, "count": 0, "days": since}


@router.get("/metrics/confidence")
async def metrics_confidence(days: int = 7):
    try:
        return await db.get_confidence_stats(days=days)
    except Exception:
        return {"mean_7d": 0.0, "mean_30d": 0.0, "shift_pp": 0.0, "distribution": []}


@router.get("/metrics/circuit_breakers")
async def metrics_circuit_breakers():
    try:
        events = await db.get_circuit_breaker_events(limit=20)
        effective_threshold = get_effective_threshold()
        from app.config import get_settings
        base_threshold = get_settings().auto_fix_threshold
        return {
            "effective_threshold": effective_threshold,
            "base_threshold": base_threshold,
            "threshold_overridden": effective_threshold != base_threshold,
            "recent_events": events,
        }
    except Exception:
        return {"effective_threshold": 0.80, "base_threshold": 0.80, "threshold_overridden": False, "recent_events": []}


# ── Tickets ───────────────────────────────────────────────────────────────────

@router.get("/tickets")
async def list_tickets(team_id: Optional[str] = None, limit: int = 50):
    try:
        return await db.list_tickets(team_id=team_id, limit=limit)
    except Exception:
        return []


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    ticket = await db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


# ── Notifications ─────────────────────────────────────────────────────────────

@router.get("/notifications")
async def list_notifications(team_id: Optional[str] = None, limit: int = 20):
    try:
        return await db.list_notifications(team_id=team_id, limit=limit)
    except Exception:
        return []


# ── System events ─────────────────────────────────────────────────────────────

@router.get("/system-events")
async def list_system_events(limit: int = 50):
    try:
        return await db.list_system_events(limit=limit)
    except Exception:
        return []


@router.post("/admin/reset-circuit-breakers")
async def reset_circuit_breakers():
    """Admin endpoint — resets any runtime threshold overrides to base values."""
    from app.core.circuit_breaker import reset_threshold_override, get_effective_threshold
    from app.config import get_settings
    reset_threshold_override()
    return {
        "status": "reset",
        "effective_threshold": get_settings().auto_fix_threshold,
        "message": "Circuit breaker threshold override cleared.",
    }
