"""
Test runner node — real pytest subprocess execution.
Runs tests/suite/ against BASE_URL, parses JUnit XML for failures.
"""
import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import structlog
from app.config import get_settings

log = structlog.get_logger()

RESULTS_PATH = "/tmp/qa_results.xml"


async def run_tests(run_id: str, selected_suites: list[str] | None = None) -> list[dict]:
    settings = get_settings()
    base_url = settings.base_url.rstrip("/")

    if Path(RESULTS_PATH).exists():
        Path(RESULTS_PATH).unlink()

    # Build the test paths: specific suite files or the whole suite dir
    if selected_suites:
        test_paths = [f"tests/suite/{s}" for s in selected_suites if s.endswith(".py")]
    else:
        test_paths = ["tests/suite/"]

    env = {**os.environ, "BASE_URL": base_url, "PLAYWRIGHT_DRIVER_TIMEOUT": "120000"}

    # Pre-warm the Node.js Playwright driver: Cloud Run Gen2 lazily loads
    # container image layers from GCS, so first access to the driver binary
    # can take 60-180s.  Starting+stopping playwright here warms the OS page
    # cache so the pytest session fixture starts in ~2s instead of >90s.
    warmup = await asyncio.create_subprocess_exec(
        sys.executable, "-c",
        "from playwright.sync_api import sync_playwright; pw=sync_playwright().start(); pw.stop(); print('WARMUP_OK')",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        wu_out, wu_err = await asyncio.wait_for(warmup.communicate(), timeout=200)
        log.info("runner.warmup", run_id=run_id, ok=b"WARMUP_OK" in wu_out)
    except asyncio.TimeoutError:
        warmup.kill()
        await warmup.wait()
        log.error("runner.warmup_timeout", run_id=run_id)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest",
        *test_paths,
        f"--junit-xml={RESULTS_PATH}",
        "-v", "--tb=short",
        "--timeout=180",
        "-p", "no:warnings",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=600,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.error("runner.timeout", run_id=run_id)
        return [{"test": "suite", "raw": "Test suite timed out after 600s", "selector": "", "expected": "", "actual": ""}]
    output = stdout.decode() + stderr.decode()
    if proc.returncode != 0:
        log.warning("runner.failures", run_id=run_id, returncode=proc.returncode, output=output[:2000])
    log.info("runner.done", run_id=run_id, returncode=proc.returncode)

    return _parse_junit(RESULTS_PATH, output)


def _parse_junit(results_path: str, raw_output: str) -> list[dict]:
    failures: list[dict] = []
    try:
        if not Path(results_path).exists():
            return []
        tree = ET.parse(results_path)
        root = tree.getroot()
        for testcase in root.iter("testcase"):
            failure = testcase.find("failure")
            error = testcase.find("error")
            node = failure if failure is not None else error
            if node is not None:
                name = f"{testcase.get('classname', '')}.{testcase.get('name', '')}"
                # Combine message attribute AND full text body for richer signal
                msg_attr = node.get("message", "") or ""
                msg_text = (node.text or "")
                msg = (msg_attr + " " + msg_text).strip()
                selector = _extract_selector(msg)
                failures.append({
                    "test": name,
                    "raw": msg[:500],
                    "selector": selector,
                    "expected": "",
                    "actual": "",
                })
    except Exception as exc:
        log.error("runner.parse_error", error=str(exc))
        if raw_output:
            failures.append({
                "test": "unknown",
                "raw": raw_output[:500],
                "selector": "",
                "expected": "",
                "actual": "",
            })
    return failures


def _extract_selector(message: str) -> str:
    import re
    patterns = [
        # double-quoted outer: allows single quotes inside (e.g. button:has-text('X'))
        r'waiting for locator\("([^"]+)"\)',
        # single-quoted outer: allows double quotes inside
        r"waiting for locator\('([^']+)'\)",
        r"selector '([^']+)'",
        r'[Ll]ocator\("([^"]+)"\)',
        r"[Ll]ocator\('([^']+)'\)",
    ]
    for p in patterns:
        m = re.search(p, message)
        if m:
            return m.group(1)
    return ""
