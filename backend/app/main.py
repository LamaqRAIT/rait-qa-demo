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
from app.api.auth import router as auth_router

log = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # ── OTel instrumentation (before anything else) ────────────────────────────
    try:
        from app.telemetry import init_telemetry
        init_telemetry(app_instance)
    except Exception as exc:
        log.warning("telemetry.init_error", error=str(exc)[:100])

    for _attempt in range(5):
        try:
            await db.init_db()
            log.info("db.ready")
            break
        except Exception as exc:
            if _attempt < 4:
                log.warning("db.init_retry", attempt=_attempt, error=str(exc)[:200])
                await asyncio.sleep(2 ** _attempt)
            else:
                log.error("db.init_failed", error=str(exc)[:200])

    # ── Build selector index from test files ───────────────────────────────────
    try:
        from app.core.selector_index import build_selector_index
        await build_selector_index()
    except Exception as exc:
        log.warning("selector_index.error", error=str(exc)[:100])

    # ── Load NLI model (DeBERTa-v3-small, ~270MB, ~50ms inference) ────────────
    try:
        from app.services.nli import init_nli
        await asyncio.get_event_loop().run_in_executor(None, init_nli)
    except Exception as exc:
        log.warning("nli.startup_error", error=str(exc)[:100])

    # ── Load embedding model + pre-compute suite catalogue ────────────────────
    try:
        from app.services.embedding import init_embedding, precompute_catalogue
        from app.core.suite_selector import _get_all_intents
        await asyncio.get_event_loop().run_in_executor(None, init_embedding)
        intents = _get_all_intents()
        await precompute_catalogue(intents)
    except Exception as exc:
        log.warning("embedding.startup_error", error=str(exc)[:100])

    # ── Start APScheduler ──────────────────────────────────────────────────────
    try:
        from app.scheduler import start_scheduler
        start_scheduler()
    except Exception as exc:
        log.warning("scheduler.start_error", error=str(exc)[:100])

    yield

    # Shutdown scheduler
    try:
        from app.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    log.info("shutdown")


app = FastAPI(title="RAIT QA Agent", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_router)
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


# ── Scheduler trigger (demo convenience) ─────────────────────────────────────

@app.post("/scheduler/trigger-nightly")
async def trigger_nightly(background_tasks: BackgroundTasks):
    """Manually trigger the nightly suite run (for demo purposes)."""
    from app.scheduler import _run_nightly_suite
    background_tasks.add_task(_run_nightly_suite)
    return {"status": "triggered", "job": "nightly_run"}


@app.post("/scheduler/rebuild-index")
async def trigger_index_rebuild():
    """Manually trigger selector index rebuild."""
    from app.core.selector_index import build_selector_index
    count = await build_selector_index()
    return {"status": "done", "entries": count}
