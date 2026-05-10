"""
Self-regulation circuit breakers.
Checked before each pipeline run and after each stage.
"""
import structlog
from app.config import get_settings

log = structlog.get_logger()

QUARANTINE_THRESHOLD = 3


def check_cost_limit(cost_usd: float) -> bool:
    settings = get_settings()
    if cost_usd >= settings.max_run_cost_usd:
        log.warning(
            "circuit_breaker.cost_limit",
            cost=cost_usd,
            limit=settings.max_run_cost_usd,
        )
        return False
    return True


def should_quarantine(consecutive_failures: int) -> bool:
    if consecutive_failures >= QUARANTINE_THRESHOLD:
        log.warning(
            "circuit_breaker.quarantine",
            consecutive_failures=consecutive_failures,
            threshold=QUARANTINE_THRESHOLD,
        )
        return True
    return False
