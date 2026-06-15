from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel, field_validator
from typing import Optional
import structlog
import app.db as db
from app.core.state import RunStatus
from app.core.pipeline import resume_after_approval
from app.auth.deps import require_role

log = structlog.get_logger()
router = APIRouter()

_VALID_CLASSIFICATIONS = {"drift", "bug", "env"}


class ApprovalRequest(BaseModel):
    approved: bool
    reviewer_name: str
    override_reason: str = ""
    # C2: human can modify the proposed fix or reclassify before approving
    modified_fix: Optional[dict] = None
    reclassify_as: Optional[str] = None

    @field_validator("reclassify_as")
    @classmethod
    def valid_class(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_CLASSIFICATIONS:
            raise ValueError(f"reclassify_as must be one of {_VALID_CLASSIFICATIONS}")
        return v


@router.post("/approve/{run_id}")
async def approve_run(
    run_id: str,
    body: ApprovalRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_role("qa_manager", "super_admin")),
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
        body.modified_fix,
        body.reclassify_as,
    )
    log.info(
        "approval.received",
        run_id=run_id,
        approved=body.approved,
        reviewer=body.reviewer_name,
        approver_role=current_user.get("role"),
        modified_fix=bool(body.modified_fix),
        reclassify_as=body.reclassify_as,
    )
    return {
        "status": "resumed",
        "approved": body.approved,
        "approved_by": body.reviewer_name,
        "modified_fix_applied": bool(body.modified_fix),
        "reclassified_to": body.reclassify_as,
    }
