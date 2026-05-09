import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import app.db as db
from app.config import get_settings
from app.models import QARun, WebhookPayload, ApprovalRequest
from app.agents.pipeline import get_graph
from app.agents.state import QAState
from langgraph.types import Command

log = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    log.info("db.ready")
    yield
    log.info("shutdown")


app = FastAPI(title="RAIT QA Agent — Demo", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.post("/webhook")
async def github_webhook(payload: WebhookPayload, background_tasks: BackgroundTasks):
    run_id = str(uuid.uuid4())
    commit_sha = payload.after or "manual"
    branch = payload.ref.replace("refs/heads/", "")

    changed_files: list[str] = []
    for commit in payload.commits:
        changed_files.extend(commit.get("modified", []))
        changed_files.extend(commit.get("added", []))

    run = QARun(
        id=run_id,
        status="planning",
        trigger_commit=commit_sha,
        trigger_branch=branch,
    )
    await db.create_run(run)
    background_tasks.add_task(_run_pipeline, run_id, commit_sha, branch, changed_files)
    log.info("webhook.received", run_id=run_id, commit=commit_sha)
    return {"run_id": run_id, "status": "started"}


@app.post("/webhook/manual")
async def manual_trigger(
    background_tasks: BackgroundTasks,
    scenario: str = "flow1",
    changed_files: list[str] | None = None,
    commit_sha: str = "manual",
    branch: str = "main",
):
    run_id = str(uuid.uuid4())
    run = QARun(id=run_id, status="planning",
                trigger_commit=commit_sha, trigger_branch=branch)
    await db.create_run(run)
    background_tasks.add_task(_run_pipeline, run_id, commit_sha, branch,
                              changed_files or ["checkout.html"], scenario)
    return {"run_id": run_id, "status": "started", "scenario": scenario}


async def _run_pipeline(
    run_id: str,
    commit_sha: str,
    branch: str,
    changed_files: list[str],
    scenario: str = "flow1",
) -> None:
    run = await db.get_run(run_id)
    if run:
        run.status = "running"
        await db.update_run(run)

    initial_state: QAState = {
        "run_id": run_id,
        "trigger_commit": commit_sha,
        "trigger_branch": branch,
        "changed_files": changed_files,
        "scenario": scenario,
        "suites_to_run": [],
        "junit_xml": "",
        "failures": [],
        "dom_report": {},
        "classification": "",
        "confidence": 0.0,
        "evidence": "",
        "proposed_fix": None,
        "approved": False,
        "approved_by": "",
        "auto_fixed": False,
        "commit_sha": "",
        "report_path": "",
        "error": None,
    }

    graph = get_graph()
    config = {"configurable": {"thread_id": run_id}}
    try:
        await graph.ainvoke(initial_state, config=config)
    except Exception as exc:
        log.error("pipeline.error", run_id=run_id, error=str(exc))
        run = await db.get_run(run_id)
        if run:
            run.status = "failed"
            await db.update_run(run)


# ── SSE Stream ────────────────────────────────────────────────────────────────

@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        terminal = {"complete", "failed"}
        while True:
            run = await db.get_run(run_id)
            if run is None:
                yield f"data: {json.dumps({'error': 'run not found'})}\n\n"
                break
            yield f"data: {json.dumps(run.to_dict())}\n\n"
            if run.status in terminal:
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── Runs List ─────────────────────────────────────────────────────────────────

@app.get("/runs")
async def list_runs():
    runs = await db.list_runs(limit=20)
    return [r.to_dict() for r in runs]


@app.get("/runs/{run_id}")
async def get_run_detail(run_id: str):
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.to_dict()


# ── Human Approval ────────────────────────────────────────────────────────────

@app.post("/approve/{run_id}")
async def approve_run(
    run_id: str,
    body: ApprovalRequest,
    background_tasks: BackgroundTasks,
):
    run = await db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "awaiting_human":
        raise HTTPException(status_code=409,
                            detail=f"Run is in status '{run.status}', not awaiting_human")

    run.approved_by = body.reviewer_name
    run.status = "healing"
    await db.update_run(run)

    background_tasks.add_task(_resume_pipeline, run_id, body)
    log.info("approval.received", run_id=run_id, approved=body.approved,
             reviewer=body.reviewer_name)
    return {"status": "resumed", "approved": body.approved}


async def _resume_pipeline(run_id: str, body: ApprovalRequest) -> None:
    graph = get_graph()
    config = {"configurable": {"thread_id": run_id}}
    try:
        await graph.ainvoke(
            Command(resume={"approved": body.approved,
                            "reviewer_name": body.reviewer_name}),
            config=config,
        )
    except Exception as exc:
        log.error("resume.error", run_id=run_id, error=str(exc))


# ── Git Log (stub for Phase 1) ────────────────────────────────────────────────

@app.get("/git/log")
async def git_log():
    try:
        import git
        repo = git.Repo(search_parent_directories=True)
        commits = list(repo.iter_commits(max_count=5))
        return [
            {
                "sha": c.hexsha[:7],
                "message": c.message.strip().split("\n")[0][:80],
                "author": c.author.name,
                "ago": _time_ago(c.committed_datetime),
            }
            for c in commits
        ]
    except Exception:
        return []


def _time_ago(dt: datetime) -> str:
    diff = datetime.now(dt.tzinfo) - dt
    if diff.seconds < 60:
        return "just now"
    if diff.seconds < 3600:
        return f"{diff.seconds // 60}m ago"
    if diff.days < 1:
        return f"{diff.seconds // 3600}h ago"
    return f"{diff.days}d ago"
