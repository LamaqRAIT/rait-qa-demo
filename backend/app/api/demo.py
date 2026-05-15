"""
Demo drift injection endpoint.
Pushes a real git commit to demo-site/checkout.html (or login.html) via PyGithub API,
then immediately starts the QA pipeline.
POST /demo/inject-drift?flow=flow1|flow2|flow3
POST /demo/reset — reverts any pending drift commit
"""
import asyncio
import uuid
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, HTTPException
import structlog
import app.db as db
from app.config import get_settings
from app.core.state import RunRecord, RunStatus
from app.core.pipeline import run_pipeline

log = structlog.get_logger()
router = APIRouter()

FLOW_CONFIGS = {
    "flow1": {
        "file": "demo-site/checkout.html",
        "description": "CSS class rename: btn-checkout → btn-place-order",
        "find": 'class="btn btn-checkout"',
        "replace": 'class="btn btn-place-order"',
        "revert_find": 'class="btn btn-place-order"',
        "revert_replace": 'class="btn btn-checkout"',
        "commit_msg": "refactor(ui): rename btn-checkout to btn-place-order [inject-drift]",
        "revert_msg": "revert(ui): restore btn-checkout class name [qa-agent-reset]",
    },
    "flow2": {
        "file": "demo-site/checkout.html",
        "description": "Button text copy change: Submit Order → Place Order",
        "find": "          Submit Order",
        "replace": "          Place Order",
        "revert_find": "          Place Order",
        "revert_replace": "          Submit Order",
        "commit_msg": "copy(ui): update checkout button text to Place Order [inject-drift]",
        "revert_msg": "revert(ui): restore checkout button text [qa-agent-reset]",
    },
    "flow3": {
        "file": "demo-site/login.html",
        "description": "Login redirect bug: dashboard.html → products.html",
        "find": "const REDIRECT_NORMAL  = 'dashboard.html'",
        "replace": "const REDIRECT_NORMAL  = 'products.html'",
        "revert_find": "const REDIRECT_NORMAL  = 'products.html'",
        "revert_replace": "const REDIRECT_NORMAL  = 'dashboard.html'",
        "commit_msg": "fix(auth): update login redirect destination [inject-drift]",
        "revert_msg": "revert(auth): restore correct login redirect [qa-agent-reset]",
    },
    "flow4": {
        "file": "demo-site/cart.html",
        "description": "Cart checkout button class rename: btn-cart-checkout → btn-proceed-checkout",
        "find": 'class="btn btn-cart-checkout"',
        "replace": 'class="btn btn-proceed-checkout"',
        "revert_find": 'class="btn btn-proceed-checkout"',
        "revert_replace": 'class="btn btn-cart-checkout"',
        "commit_msg": "refactor(cart): rename btn-cart-checkout to btn-proceed-checkout [inject-drift]",
        "revert_msg": "revert(cart): restore btn-cart-checkout class [qa-agent-reset]",
    },
    "flow5": {
        "file": "demo-site/search.html",
        "description": "Search input ID rename: search-input → search-query (tests HITL path)",
        "find": 'id="search-input"',
        "replace": 'id="search-query"',
        "revert_find": 'id="search-query"',
        "revert_replace": 'id="search-input"',
        "commit_msg": "refactor(search): rename search-input id to search-query [inject-drift]",
        "revert_msg": "revert(search): restore search-input id [qa-agent-reset]",
    },
    "flow6": {
        "file": "demo-site/register.html",
        "description": "Registration email field ID rename: reg-email → register-email",
        "find": 'id="reg-email"',
        "replace": 'id="register-email"',
        "revert_find": 'id="register-email"',
        "revert_replace": 'id="reg-email"',
        "commit_msg": "refactor(auth): standardise registration field IDs [inject-drift]",
        "revert_msg": "revert(auth): restore registration field IDs [qa-agent-reset]",
    },
}

_active_drift: dict[str, str | None] = {"flow": None}


def _get_gh_repo():
    from github import Github
    settings = get_settings()
    gh = Github(settings.github_token)
    return gh.get_repo(f"{settings.github_repo_owner}/{settings.github_repo_name}")


async def _push_change(flow: str, direction: str) -> str:
    config = FLOW_CONFIGS[flow]
    find = config["find"] if direction == "inject" else config["revert_find"]
    replace = config["replace"] if direction == "inject" else config["revert_replace"]
    msg = config["commit_msg"] if direction == "inject" else config["revert_msg"]
    file_path = config["file"]

    def _do_push():
        repo = _get_gh_repo()
        file_obj = repo.get_contents(file_path)
        content = file_obj.decoded_content.decode("utf-8")
        if find not in content:
            return None
        patched = content.replace(find, replace, 1)
        result = repo.update_file(
            path=file_path,
            message=msg,
            content=patched,
            sha=file_obj.sha,
        )
        return result["commit"].sha

    sha = await asyncio.get_running_loop().run_in_executor(None, _do_push)
    return sha or ""


async def _wait_for_pages_deployment(expected_sha: str, timeout: int = 120) -> bool:
    """
    Wait for GitHub Pages CDN to serve the drift commit.
    Strategy: poll the raw HTML for evidence of the patched content.
    Falls back to a fixed 90-second wait if verification can't be done.
    """
    settings = get_settings()
    import httpx

    config = FLOW_CONFIGS.get(_active_drift.get("flow") or "", {})
    expected_fragment = config.get("replace", "") if config else ""

    file_path = config.get("file", "demo-site/checkout.html")
    page_path = file_path.replace("demo-site/", "", 1)
    probe_url = f"{settings.base_url.rstrip('/')}/{page_path}" if config else None

    deadline = asyncio.get_event_loop().time() + timeout
    await asyncio.sleep(15)  # initial wait — Pages build takes at least 15s

    while asyncio.get_event_loop().time() < deadline:
        if probe_url and expected_fragment:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(probe_url, follow_redirects=True)
                    if expected_fragment in resp.text:
                        log.info("demo.pages_deployed", sha=expected_sha[:7])
                        return True
            except Exception as exc:
                log.warning("demo.pages_poll_error", error=str(exc))
        await asyncio.sleep(10)

    log.warning("demo.pages_timeout", timeout=timeout)
    return False


@router.post("/demo/inject-drift")
async def inject_drift(
    flow: str = "flow1",
    hitl: bool = False,
    background_tasks: BackgroundTasks = None,
):
    if flow not in FLOW_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Unknown flow: {flow}. Choose flow1, flow2, or flow3.")

    config = FLOW_CONFIGS[flow]
    run_id = str(uuid.uuid4())

    run = RunRecord(
        id=run_id,
        status=RunStatus.PLANNING,
        trigger_commit="inject-drift",
        trigger_branch="main",
        force_hitl=hitl,
    )
    await db.create_run(run)

    _active_drift["flow"] = flow

    background_tasks.add_task(_inject_and_run, run_id, flow)

    log.info("demo.inject_drift", flow=flow, run_id=run_id, hitl=hitl, description=config["description"])
    return {
        "run_id": run_id,
        "status": "started",
        "flow": flow,
        "hitl": hitl,
        "description": config["description"],
    }


async def _inject_and_run(run_id: str, flow: str) -> None:
    run = await db.get_run(run_id)
    if not run:
        return
    from app.core.pipeline import _set_node
    await _set_node(run, "git_watcher", "running", f"Pushing drift commit for {flow}…")

    try:
        sha = await _push_change(flow, "inject")
        if not sha:
            await _set_node(run, "git_watcher", "failed", "Drift already applied or find string not found")
            run.status = RunStatus.FAILED
            await db.update_run(run)
            return

        run.trigger_commit = sha[:7]
        await db.update_run(run)
        await _set_node(run, "git_watcher", "success", f"Drift pushed (sha: {sha[:7]})")

    except Exception as exc:
        log.error("demo.inject_error", run_id=run_id, error=str(exc))
        await _set_node(run, "git_watcher", "failed", f"Inject error: {exc}")
        run.status = RunStatus.FAILED
        await db.update_run(run)
        return

    await run_pipeline(run_id)

    await _schedule_revert(flow)


async def _schedule_revert(flow: str) -> None:
    await asyncio.sleep(5)
    try:
        await _push_change(flow, "revert")
        _active_drift["flow"] = None
        log.info("demo.reverted", flow=flow)
    except Exception as exc:
        log.warning("demo.revert_error", flow=flow, error=str(exc))


@router.post("/demo/reset")
async def reset_demo():
    flow = _active_drift.get("flow")
    if not flow:
        return {"status": "nothing_to_reset"}
    try:
        await _push_change(flow, "revert")
        _active_drift["flow"] = None
        return {"status": "reset", "flow": flow}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
