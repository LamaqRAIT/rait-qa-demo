"""
Self-regulation circuit breakers — all 4 mechanisms from the architecture doc.
Every breaker fires → writes to system_events + Slack alert.

1. Cost circuit breaker:     run cost > $0.50 → abort
2. Error rate circuit breaker: >20% fails in last 10 runs → suspend auto-fix (threshold → 1.01)
3. FPR circuit breaker:     human_override rate > 15% over 30d → raise threshold to 0.90
4. Confidence drift:        7d mean shifts >15pp from 30d baseline → alert
5. Quarantine:              3+ consecutive failures → quarantine test
"""
import structlog
import app.db as db
from app.config import get_settings

log = structlog.get_logger()

QUARANTINE_THRESHOLD = 3
_THRESHOLD_OVERRIDE: float | None = None  # runtime override set by circuit breakers


def get_effective_threshold() -> float:
    """Return the current auto-fix confidence threshold, potentially overridden by circuit breakers."""
    if _THRESHOLD_OVERRIDE is not None:
        return _THRESHOLD_OVERRIDE
    return get_settings().auto_fix_threshold


def _set_threshold_override(value: float) -> None:
    global _THRESHOLD_OVERRIDE
    _THRESHOLD_OVERRIDE = value
    log.warning("circuit_breaker.threshold_override", new_threshold=value)


def reset_threshold_override() -> None:
    global _THRESHOLD_OVERRIDE
    _THRESHOLD_OVERRIDE = None


# ── 1. Cost limit ─────────────────────────────────────────────────────────────

def check_cost_limit(cost_usd: float) -> bool:
    settings = get_settings()
    if cost_usd >= settings.max_run_cost_usd:
        log.warning("circuit_breaker.cost_limit", cost=cost_usd, limit=settings.max_run_cost_usd)
        return False
    return True


async def check_cost_and_record(cost_usd: float, run_id: str) -> bool:
    settings = get_settings()
    if cost_usd >= settings.max_run_cost_usd:
        msg = f"Run {run_id[:8]} exceeded cost limit ${settings.max_run_cost_usd:.2f} (actual ${cost_usd:.4f}) — aborted"
        log.warning("circuit_breaker.cost_limit", run_id=run_id, cost=cost_usd)
        await db.record_system_event("cost_limit_exceeded", msg, severity="critical", run_id=run_id)
        try:
            from app.integrations.slack import notify_circuit_breaker
            await notify_circuit_breaker("cost_limit_exceeded", msg, {"cost_usd": cost_usd, "run_id": run_id})
        except Exception:
            pass
        return False
    return True


# ── 2. Error rate ─────────────────────────────────────────────────────────────

async def check_error_rate(run_id: str | None = None) -> float:
    """
    Check error rate over last 10 runs.
    If > 20%, suspend auto-fix (threshold → 1.01) and fire event.
    Returns the current error rate.
    """
    rate = await db.get_recent_error_rate(window=10)
    if rate > 0.20:
        msg = f"Error rate {rate:.0%} over last 10 runs exceeds 20% threshold — auto-fix suspended"
        log.warning("circuit_breaker.error_rate", rate=rate, run_id=run_id)
        _set_threshold_override(1.01)  # effectively forces all fixes to HITL
        await db.record_system_event(
            "error_rate_exceeded",
            msg,
            severity="critical",
            run_id=run_id,
            meta={"error_rate": rate},
        )
        try:
            from app.integrations.slack import notify_circuit_breaker
            await notify_circuit_breaker("error_rate_exceeded", msg, {"error_rate": rate})
        except Exception:
            pass
    elif _THRESHOLD_OVERRIDE == 1.01 and rate <= 0.10:
        # Reset threshold once error rate recovers below 10%
        reset_threshold_override()
        log.info("circuit_breaker.error_rate_recovered", rate=rate)
        await db.record_system_event(
            "error_rate_recovered",
            f"Error rate recovered to {rate:.0%} — auto-fix threshold restored",
            severity="info",
            meta={"error_rate": rate},
        )
    return rate


# ── 3. False positive rate ─────────────────────────────────────────────────────

async def check_false_positive_rate(run_id: str | None = None) -> float:
    """
    Check human override rate over 30d as FPR proxy.
    If > 15%, raise auto-fix threshold to 0.90.
    Returns the current FPR.
    """
    stats = await db.get_override_rate(days=30)
    fpr = stats.get("override_rate", 0.0)
    if fpr > 0.15 and stats.get("total", 0) >= 5:
        msg = f"Human override rate {fpr:.0%} over 30d exceeds 15% — confidence threshold raised to 0.90"
        log.warning("circuit_breaker.fpr", fpr=fpr, run_id=run_id)
        if (_THRESHOLD_OVERRIDE or 0) < 0.90:
            _set_threshold_override(0.90)
        await db.record_system_event(
            "false_positive_rate_exceeded",
            msg,
            severity="warning",
            run_id=run_id,
            meta={"fpr": fpr, "overrides": stats.get("overrides"), "total": stats.get("total")},
        )
        try:
            from app.integrations.slack import notify_circuit_breaker
            await notify_circuit_breaker("false_positive_rate_exceeded", msg, {"fpr": fpr})
        except Exception:
            pass
    return fpr


# ── 4. Confidence distribution shift ─────────────────────────────────────────

async def check_confidence_drift(run_id: str | None = None) -> float:
    """
    Alert if 7d mean confidence shifts > 15pp from 30d baseline.
    Returns the shift in pp.
    """
    stats = await db.get_confidence_stats(days=7)
    shift = stats.get("shift_pp", 0.0)
    if shift > 15.0:
        msg = (
            f"Confidence distribution shifted {shift:.1f}pp from 30d baseline "
            f"(7d mean: {stats['mean_7d']:.2f}, 30d mean: {stats['mean_30d']:.2f}) — "
            "classifier may be seeing a new failure class or prompt drift"
        )
        log.warning("circuit_breaker.confidence_drift", shift=shift, run_id=run_id)
        await db.record_system_event(
            "confidence_distribution_shifted",
            msg,
            severity="warning",
            run_id=run_id,
            meta={"shift_pp": shift, "mean_7d": stats["mean_7d"], "mean_30d": stats["mean_30d"]},
        )
        try:
            from app.integrations.slack import notify_circuit_breaker
            await notify_circuit_breaker("confidence_distribution_shifted", msg, stats)
        except Exception:
            pass
    return shift


# ── 5. Quarantine ─────────────────────────────────────────────────────────────

def should_quarantine(consecutive_failures: int) -> bool:
    if consecutive_failures >= QUARANTINE_THRESHOLD:
        log.warning("circuit_breaker.quarantine", consecutive_failures=consecutive_failures, threshold=QUARANTINE_THRESHOLD)
        return True
    return False


async def record_quarantine(run_id: str, test_name: str = "") -> None:
    msg = f"Test{' ' + test_name if test_name else ''} quarantined after {QUARANTINE_THRESHOLD}+ consecutive failures"
    await db.record_system_event("quarantine_triggered", msg, severity="warning", run_id=run_id)
    try:
        from app.integrations.slack import notify_circuit_breaker
        await notify_circuit_breaker("quarantine_triggered", msg, {"run_id": run_id, "test": test_name})
    except Exception:
        pass


# ── Full post-run check ────────────────────────────────────────────────────────

async def run_all_checks(run_id: str) -> None:
    """Run all circuit breakers after a pipeline run completes (or fails)."""
    await check_error_rate(run_id)
    await check_false_positive_rate(run_id)
    await check_confidence_drift(run_id)
