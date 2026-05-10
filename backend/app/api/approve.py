from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import structlog
import app.db as db
from app.core.state import RunStatus
from app.core.pipeline import resume_after_approval

log = structlog.get_logger()
router = APIRouter()


class ApprovalRequest(BaseModel):
    approved: bool
    reviewer_name: str
    override_reason: str = ""


@router.post("/approve/{run_id}")
async def approve_run(
    run_id: str,
    body: ApprovalRequest,
    background_tasks: BackgroundTasks,
):
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != RunStatus.AWAITING_HUMAN:
        raise HTTPException(
            status_code=409,
            detail=f"Run is in status '{run.status}', not awaiting_human",
        )

    if body.override_reason:
        run.human_override = True
        run.override_reason = body.override_reason
        await db.update_run(run)

    background_tasks.add_task(
        resume_after_approval,
        run_id,
        body.approved,
        body.reviewer_name,
    )
    log.info(
        "approval.received",
        run_id=run_id,
        approved=body.approved,
        reviewer=body.reviewer_name,
    )
    return {"status": "resumed", "approved": body.approved}
