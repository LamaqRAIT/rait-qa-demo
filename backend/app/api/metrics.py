from fastapi import APIRouter
import app.db as db

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
