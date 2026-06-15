"""
DOM Inspector node — dispatches inspector_worker subprocess.
Avoids event loop conflicts with FastAPI by using asyncio subprocess.

Page URL resolution priority:
1. # PAGE: comment at the top of the test file  (I2 — explicit, most reliable)
2. test name keyword heuristic                   (fallback for legacy tests)
"""
import asyncio
import json
import os
import sys
import structlog
from app.config import get_settings

log = structlog.get_logger()


def _page_from_intent_comment(test_name: str, base_url: str) -> str | None:
    """
    Read # PAGE: comment from the test file identified by test_name.
    Returns a full URL or None if the comment is absent.
    """
    t = test_name.lower()
    basenames = {
        "login": "test_login.py", "cart": "test_cart.py",
        "search": "test_search.py", "registr": "test_registration.py",
        "account": "test_account.py", "product": "test_products.py",
        "nav": "test_navigation.py", "checkout": "test_checkout.py",
    }
    fname = next((v for k, v in basenames.items() if k in t), None)
    if not fname:
        return None
    file_path = os.path.join("tests", "suite", fname)
    try:
        with open(file_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("def ") or stripped.startswith("async def "):
                    break
                if stripped.startswith("# PAGE:"):
                    path = stripped[len("# PAGE:"):].strip()
                    return base_url.rstrip("/") + path
    except Exception:
        pass
    return None


async def inspect_dom(run_id: str, failures: list[dict]) -> dict:
    if not failures:
        return {}

    settings = get_settings()
    base_url = settings.base_url.rstrip("/")
    first_test = (failures[0].get("test") or "").lower()

    # I2: try PAGE: comment first, then keyword heuristic
    url = _page_from_intent_comment(first_test, base_url)
    if not url:
        page_map = {
            "login": "/login.html", "register": "/register.html",
            "search": "/search.html", "cart": "/cart.html",
            "account": "/account.html", "navigation": "/products.html",
            "products": "/products.html", "checkout": "/checkout.html",
        }
        page_path = next((p for k, p in page_map.items() if k in first_test), "/checkout.html")
        url = base_url + page_path

    selectors = [f["selector"] for f in failures if f.get("selector")]
    if not selectors:
        for f in failures:
            raw = f.get("raw", "")
            if "selector" in raw.lower() or ".btn" in raw or "locator" in raw.lower():
                selectors.append(raw[:100])
    if not selectors:
        return {
            "inspected": True,
            "url": url,
            "changed_selectors": [],
            "full_scan": [],
            "note": "No DOM selectors in failures — possible URL/redirect or logic assertion failure",
        }

    payload = json.dumps({
        "url": url,
        "selectors": selectors,
        "timeout": settings.playwright_timeout,
        "max_elements": settings.dom_inspector_max_elements,
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
        changed_selectors = []   # top 3 per selector — used by auto_fixer
        full_scan = []           # top 20 per selector — used for explainability (E4)

        for sel, data in result.get("selectors", {}).items():
            for c in data.get("candidates", [])[:3]:
                changed_selectors.append({
                    "old": sel, "found": c["selector"], "element_text": "",
                    "confidence": c["confidence"], "match_reason": c.get("match_reason", ""),
                })
            for c in data.get("full_scan", [])[:20]:
                full_scan.append({
                    "old": sel, "found": c["selector"], "element_text": "",
                    "confidence": c["confidence"], "match_reason": c.get("match_reason", ""),
                })

        log.info("inspector.done", run_id=run_id, candidates=len(changed_selectors), full_scan=len(full_scan))
        return {
            "inspected": True,
            "url": url,
            "changed_selectors": changed_selectors,
            "full_scan": full_scan,
        }
    except Exception as exc:
        log.error("inspector.parse_error", run_id=run_id, error=str(exc))
        return {"inspected": False, "error": str(exc)}
