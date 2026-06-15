from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import structlog
import app.db as db
from app.core.circuit_breaker import get_effective_threshold
from app.auth.deps import require_role

log = structlog.get_logger()
router = APIRouter()

@router.get("/metrics/summary")
async def metrics_summary(days: int = 7, group_by: Optional[str] = None):
    """M4: Optional group_by splits summary by team_id | classification | suite_selection_method."""
    try:
        if group_by:
            return await db.get_metrics_summary_grouped(days=days, group_by=group_by)
        return await db.get_metrics_summary(days=days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        return {"total_runs": 0, "success_runs": 0, "success_rate": 0.0, "avg_cost_usd": 0.0, "total_cost_usd": 0.0, "avg_confidence": 0.0}


@router.get("/metrics/trends")
async def metrics_trends(days: int = 30, bucket: str = "day"):
    """M3: Time-series of run counts, auto-fix vs HITL, cost, and confidence by day/week."""
    try:
        return await db.get_metrics_trends(days=days, bucket=bucket)
    except Exception as e:
        log.warning("metrics.trends_error", error=str(e)[:100])
        return []


@router.get("/metrics/classifications")
async def metrics_classifications(days: int = 30, group_by: Optional[str] = None):
    """M4: Optional group_by for multi-dimensional classification breakdown."""
    try:
        if group_by:
            return await db.get_metrics_summary_grouped(days=days, group_by=group_by)
        return await db.get_classification_distribution(days=days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
async def reset_circuit_breakers(
    current_user: dict = Depends(require_role("qa_manager", "super_admin")),
):
    """Admin endpoint — resets any runtime threshold overrides to base values."""
    from app.core.circuit_breaker import reset_threshold_override, get_effective_threshold
    from app.config import get_settings
    reset_threshold_override()
    return {
        "status": "reset",
        "effective_threshold": get_settings().auto_fix_threshold,
        "message": "Circuit breaker threshold override cleared.",
        "reset_by": current_user.get("email"),
    }


# ── M2: Config overrides admin API ─────────────────────────────────────────────

class ConfigOverrideRequest(BaseModel):
    value: str


@router.get("/admin/config")
async def get_config(
    current_user: dict = Depends(require_role("qa_manager", "super_admin")),
):
    """M2: Returns effective settings — env defaults merged with DB overrides."""
    from app.config import get_settings
    settings = get_settings()
    overrides = await db.get_config_overrides()
    base = {
        "auto_fix_threshold": settings.auto_fix_threshold,
        "max_run_cost_usd": settings.max_run_cost_usd,
        "error_rate_ceiling": settings.error_rate_ceiling,
        "fpr_ceiling": settings.fpr_ceiling,
        "confidence_drift_pp_ceiling": settings.confidence_drift_pp_ceiling,
        "quarantine_threshold": settings.quarantine_threshold,
        "calibration_mode": settings.calibration_mode,
        "reflector_min_sample": settings.reflector_min_sample,
        "dom_inspector_max_elements": settings.dom_inspector_max_elements,
        "suite_selector_use_llm": settings.suite_selector_use_llm,
    }
    return {"base": base, "overrides": overrides, "effective": {**base, **overrides}}


@router.post("/admin/config/{key}")
async def set_config(
    key: str,
    body: ConfigOverrideRequest,
    current_user: dict = Depends(require_role("qa_manager", "super_admin")),
):
    """M2: Set a runtime config override (stored in DB, survives redeploy)."""
    await db.set_config_override(key, body.value, updated_by=current_user.get("email", "unknown"))
    log.info("admin.config_override", key=key, value=body.value, by=current_user.get("email"))
    return {"status": "set", "key": key, "value": body.value}


@router.delete("/admin/config/{key}")
async def delete_config(
    key: str,
    current_user: dict = Depends(require_role("super_admin")),
):
    """M2: Remove a config override (key reverts to env/default value)."""
    await db.delete_config_override(key)
    log.info("admin.config_override_deleted", key=key, by=current_user.get("email"))
    return {"status": "deleted", "key": key}


# ── C3: Failure patterns list ─────────────────────────────────────────────────

@router.get("/failure-patterns")
async def list_failure_patterns(
    limit: int = 50,
    verified_only: bool = True,
    current_user: dict = Depends(require_role("qa_engineer", "qa_manager", "super_admin")),
):
    """Returns human-verified (and optionally auto-confirmed) triage outcomes."""
    try:
        return await db.list_failure_patterns(limit=limit, verified_only=verified_only)
    except Exception:
        return []
