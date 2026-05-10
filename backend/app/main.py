import asyncio
import hashlib
import hmac
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
from app.core.state import RunRecord, RunStatus
from app.core.pipeline import run_pipeline
from app.api.runs import router as runs_router
from app.api.approve import router as approve_router
from app.api.metrics import router as metrics_router
from app.api.demo import router as demo_router

log = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    log.info("db.ready")
    yield
    log.info("shutdown")


app = FastAPI(title="RAIT QA Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(runs_router)
app.include_router(approve_router)
app.include_router(metrics_router)
app.include_router(demo_router)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "version": "2.0"}


# ── GitHub Webhook ────────────────────────────────────────────────────────────

@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()

    if settings.github_webhook_secret:
        sig = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            settings.github_webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()  # type: ignore[attr-defined]
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    commit_sha = payload.get("after", "")
    ref = payload.get("ref", "refs/heads/main")
    branch = ref.replace("refs/heads/", "")

    head_commit = payload.get("head_commit") or {}
    commit_msg = head_commit.get("message", "")
    if "[inject-drift]" in commit_msg or "[qa-agent-" in commit_msg:
        log.info("webhook.skip_agent_commit", sha=commit_sha[:7], msg=commit_msg[:80])
        return {"status": "skipped", "reason": "agent-generated commit"}

    run_id = str(uuid.uuid4())
    run = RunRecord(
        id=run_id,
        status=RunStatus.PLANNING,
        trigger_commit=commit_sha[:7] if commit_sha else "webhook",
        trigger_branch=branch,
    )
    await db.create_run(run)
    background_tasks.add_task(run_pipeline, run_id)
    log.info("webhook.received", run_id=run_id, commit=commit_sha[:7], branch=branch)
    return {"run_id": run_id, "status": "started"}


@app.post("/webhook/manual")
async def manual_trigger(
    background_tasks: BackgroundTasks,
    scenario: str = "flow1",
    commit_sha: str = "manual",
    branch: str = "main",
):
    run_id = str(uuid.uuid4())
    run = RunRecord(
        id=run_id,
        status=RunStatus.PLANNING,
        trigger_commit=commit_sha,
        trigger_branch=branch,
    )
    await db.create_run(run)
    background_tasks.add_task(run_pipeline, run_id)
    log.info("manual_trigger", run_id=run_id, scenario=scenario)
    return {"run_id": run_id, "status": "started", "scenario": scenario}


# ── SSE Stream ────────────────────────────────────────────────────────────────

@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        terminal = {RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.QUARANTINED,
                    "complete", "failed", "quarantined"}
        while True:
            run = await db.get_run(run_id)
            if run is None:
                yield f"data: {json.dumps({'error': 'run not found'})}\n\n"
                break
            yield f"data: {json.dumps(run.to_dict())}\n\n"
            if run.status in terminal:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Git Log ───────────────────────────────────────────────────────────────────

@app.get("/git/log")
async def git_log():
    settings = get_settings()
    try:
        from github import Github
        gh = Github(settings.github_token)
        repo = gh.get_repo(f"{settings.github_repo_owner}/{settings.github_repo_name}")
        commits = list(repo.get_commits()[:5])
        return [
            {
                "sha": c.sha[:7],
                "message": c.commit.message.strip().split("\n")[0][:80],
                "author": c.commit.author.name,
                "ago": _time_ago(c.commit.author.date),
            }
            for c in commits
        ]
    except Exception:
        return []


def _time_ago(dt: datetime) -> str:
    from datetime import timezone
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    if diff.seconds < 60:
        return "just now"
    if diff.seconds < 3600:
        return f"{diff.seconds // 60}m ago"
    if diff.days < 1:
        return f"{diff.seconds // 3600}h ago"
    return f"{diff.days}d ago"
