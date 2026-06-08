"""
Canary node — two always-passing infra checks that gate the entire suite.
If either fails, the run is immediately classified 'env' and no tests execute.

Runs directly in FastAPI's event loop (no subprocess) to avoid gVisor
seccomp restrictions that cause child Python processes to hang silently.
"""
import asyncio
import structlog
import httpx
from app.config import get_settings

log = structlog.get_logger()


async def _check_http(base_url: str) -> tuple[bool, str]:
    """Verify /login.html returns HTTP 200."""
    url = base_url + "/login.html"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            return True, f"GET {url} → {resp.status_code}"
        return False, f"GET {url} → {resp.status_code} (expected 200)"
    except Exception as exc:
        return False, f"GET {url} failed: {exc}"


async def _check_dom(base_url: str) -> tuple[bool, str]:
    """Verify #email input is present on the login page using async Playwright."""
    url = base_url + "/login.html"
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-zygote",
            ])
            page = await browser.new_page()
            await page.goto(url, timeout=20_000)
            locator = page.locator("#email")
            visible = await locator.is_visible()
            await browser.close()
        if visible:
            return True, "#email is visible on login page"
        return False, "#email not visible on login page"
    except Exception as exc:
        return False, f"DOM check failed: {exc}"


async def run_canary(run_id: str) -> dict:
    settings = get_settings()
    base_url = settings.base_url.rstrip("/")

    lines: list[str] = []

    # Check 1: HTTP reachability (fast, no browser)
    try:
        http_ok, http_msg = await asyncio.wait_for(_check_http(base_url), timeout=35.0)
    except asyncio.TimeoutError:
        http_ok, http_msg = False, "HTTP check timed out after 35s"
    lines.append(f"[http] {http_msg}")
    log.info("canary.http", run_id=run_id, ok=http_ok, msg=http_msg)

    if not http_ok:
        log.warning("canary.failed", run_id=run_id, output="\n".join(lines))
        return {"passed": False, "output": "\n".join(lines)}

    # Check 2: DOM form element (browser)
    try:
        dom_ok, dom_msg = await asyncio.wait_for(_check_dom(base_url), timeout=60.0)
    except asyncio.TimeoutError:
        dom_ok, dom_msg = False, "DOM check timed out after 60s"
    lines.append(f"[dom]  {dom_msg}")
    log.info("canary.dom", run_id=run_id, ok=dom_ok, msg=dom_msg)

    passed = http_ok and dom_ok
    output = "\n".join(lines)

    if passed:
        log.info("canary.done", run_id=run_id, passed=True)
    else:
        log.warning("canary.failed", run_id=run_id, output=output)

    return {"passed": passed, "output": output}
