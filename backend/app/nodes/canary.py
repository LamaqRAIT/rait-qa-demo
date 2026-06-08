"""
Canary node — two always-passing infra checks that gate the entire suite.
If either fails, the run is immediately classified 'env' and no tests execute.

Uses httpx + stdlib HTML parsing only — no browser, no subprocess — because
Cloud Run (both gVisor and Gen2) has Chrome launch issues that cause hangs.
"""
import asyncio
from html.parser import HTMLParser
import structlog
import httpx
from app.config import get_settings

log = structlog.get_logger()


class _IdFinder(HTMLParser):
    """Scan HTML for a tag whose id attribute matches a target."""
    def __init__(self, target_id: str) -> None:
        super().__init__()
        self.found = False
        self._target = target_id

    def handle_starttag(self, tag: str, attrs: list) -> None:
        for name, value in attrs:
            if name == "id" and value == self._target:
                self.found = True


async def run_canary(run_id: str) -> dict:
    settings = get_settings()
    base_url = settings.base_url.rstrip("/")
    url = base_url + "/login.html"
    lines: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await asyncio.wait_for(client.get(url), timeout=30.0)
    except asyncio.TimeoutError:
        msg = f"GET {url} timed out after 30s"
        lines.append(f"[http] FAIL: {msg}")
        log.warning("canary.failed", run_id=run_id, output="\n".join(lines))
        return {"passed": False, "output": "\n".join(lines)}
    except Exception as exc:
        msg = f"GET {url} error: {exc}"
        lines.append(f"[http] FAIL: {msg}")
        log.warning("canary.failed", run_id=run_id, output="\n".join(lines))
        return {"passed": False, "output": "\n".join(lines)}

    # Check 1: HTTP 200
    if resp.status_code != 200:
        msg = f"GET {url} → {resp.status_code} (expected 200)"
        lines.append(f"[http] FAIL: {msg}")
        log.warning("canary.failed", run_id=run_id, output="\n".join(lines))
        return {"passed": False, "output": "\n".join(lines)}

    lines.append(f"[http] PASS: GET {url} → {resp.status_code}")
    log.info("canary.http", run_id=run_id, ok=True, msg=lines[-1])

    # Check 2: #email input present in HTML (no browser needed)
    finder = _IdFinder("email")
    finder.feed(resp.text)
    if finder.found:
        lines.append("[dom]  PASS: #email input found in HTML")
        log.info("canary.dom", run_id=run_id, ok=True, msg=lines[-1])
    else:
        lines.append("[dom]  FAIL: #email input not found in login page HTML")
        log.warning("canary.failed", run_id=run_id, output="\n".join(lines))
        return {"passed": False, "output": "\n".join(lines)}

    log.info("canary.done", run_id=run_id, passed=True)
    return {"passed": True, "output": "\n".join(lines)}
