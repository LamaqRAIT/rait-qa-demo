from fastapi import APIRouter, HTTPException, Depends
import structlog
import app.db as db
from app.auth.deps import require_role

log = structlog.get_logger()
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


@router.post("/runs/{run_id}/replay")
async def replay_triage(
    run_id: str,
    current_user: dict = Depends(require_role("qa_engineer", "qa_manager", "super_admin")),
):
    """
    E2: Re-run triage on the stored prompt for a historical run.
    Zero side effects — no DB writes, no fix applied.
    Returns original result alongside the new result for comparison.
    """
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    stored_prompt, stored_response, prompt_hash = await db.get_run_triage_prompt(run_id)
    if not stored_prompt:
        raise HTTPException(
            status_code=422,
            detail="No stored triage prompt for this run (run may predate E1 auditing)",
        )

    try:
        from app.llm.client import call_llm
        from app.nodes.triage import _parse_and_validate

        raw, in_tok, out_tok, cost, model_used, trace_url = await call_llm(
            prompt=stored_prompt,
            run_id=f"{run_id}:replay",
            call_name="triage_replay",
            model_preference="sonnet",
            max_tokens=512,
        )

        new_result, _ = await _parse_and_validate(raw, stored_prompt, run_id, in_tok, out_tok, cost, model_used)

        original = {
            "classification": run.triage.classification,
            "confidence": run.triage.confidence,
            "evidence": run.triage.evidence,
            "stored_response": stored_response[:500] if stored_response else None,
        }

        new = new_result.model_dump() if new_result else None
        changed = (
            new is not None
            and new.get("classification") != run.triage.classification
        )

        log.info(
            "replay.done",
            run_id=run_id,
            replayed_by=current_user.get("email"),
            changed=changed,
            model=model_used,
        )

        return {
            "run_id": run_id,
            "prompt_hash": prompt_hash,
            "original": original,
            "replay": new,
            "changed": changed,
            "model_used": model_used,
            "tokens_used": in_tok + out_tok,
            "cost_usd": cost,
        }

    except Exception as exc:
        log.error("replay.error", run_id=run_id, error=str(exc)[:200])
        raise HTTPException(status_code=500, detail=f"Replay failed: {str(exc)[:200]}")
