"""
DOM Inspector node — dispatches inspector_worker subprocess.
Avoids event loop conflicts with FastAPI by using asyncio subprocess.
"""
import asyncio
import json
import os
import sys
import structlog
from app.config import get_settings

log = structlog.get_logger()


async def inspect_dom(run_id: str, failures: list[dict]) -> dict:
    if not failures:
        return {}

    settings = get_settings()
    url = settings.base_url.rstrip("/") + "/checkout.html"

    selectors = [
        f["selector"] for f in failures if f.get("selector")
    ]
    if not selectors:
        for f in failures:
            raw = f.get("raw", "")
            if "selector" in raw.lower() or ".btn" in raw or "locator" in raw.lower():
                selectors.append(raw[:100])
    if not selectors:
        # Return a structured result even without selectors so triage has context
        return {
            "inspected": True,
            "url": url,
            "changed_selectors": [],
            "note": "No DOM selectors in failures — possible URL/redirect or logic assertion failure",
        }

    # Auto-detect the page from the test name
    first_test = (failures[0].get("test") or "").lower()
    page_map = {
        "login":        "/login.html",
        "register":     "/register.html",
        "search":       "/search.html",
        "cart":         "/cart.html",
        "account":      "/account.html",
        "navigation":   "/products.html",
        "products":     "/products.html",
        "checkout":     "/checkout.html",
    }
    for key, path in page_map.items():
        if key in first_test:
            url = settings.base_url.rstrip("/") + path
            break

    payload = json.dumps({
        "url": url,
        "selectors": selectors,
        "timeout": settings.playwright_timeout,
    })

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.nodes.inspector_worker",
        payload,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ},
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        log.warning("inspector.worker_error", run_id=run_id, stderr=stderr.decode()[:500])
        return {"inspected": False, "error": stderr.decode()[:300]}

    try:
        result = json.loads(stdout.decode())
        all_candidates = []
        for sel, data in result.get("selectors", {}).items():
            for c in data.get("candidates", []):
                all_candidates.append({
                    "old": sel,
                    "found": c["selector"],
                    "element_text": "",
                    "confidence": c["confidence"],
                    "match_reason": c.get("match_reason", ""),
                })
        log.info("inspector.done", run_id=run_id, candidates=len(all_candidates))
        return {
            "inspected": True,
            "url": url,
            "changed_selectors": all_candidates,
        }
    except Exception as exc:
        log.error("inspector.parse_error", run_id=run_id, error=str(exc))
        return {"inspected": False, "error": str(exc)}
