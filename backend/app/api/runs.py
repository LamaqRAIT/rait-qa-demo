from fastapi import APIRouter, HTTPException
import app.db as db

router = APIRouter()


@router.get("/runs")
async def list_runs():
    runs = await db.list_runs(limit=20)
    return [r.to_dict() for r in runs]


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.to_dict()
