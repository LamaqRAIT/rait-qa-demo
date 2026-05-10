"""
Test runner node — real pytest subprocess execution.
Runs tests/suite/ against BASE_URL, parses JUnit XML for failures.
"""
import asyncio
import os
import xml.etree.ElementTree as ET
from pathlib import Path
import structlog
from app.config import get_settings

log = structlog.get_logger()

RESULTS_PATH = "/tmp/qa_results.xml"


async def run_tests(run_id: str) -> list[dict]:
    settings = get_settings()
    base_url = settings.base_url.rstrip("/")

    if Path(RESULTS_PATH).exists():
        Path(RESULTS_PATH).unlink()

    env = {**os.environ, "BASE_URL": base_url}
    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "pytest",
        "tests/suite/",
        f"--junit-xml={RESULTS_PATH}",
        "-v", "--tb=short",
        "--timeout=30",
        "-p", "no:warnings",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=120,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.error("runner.timeout", run_id=run_id)
        return [{"test": "suite", "raw": "Test suite timed out after 120s", "selector": "", "expected": "", "actual": ""}]
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
                msg = node.get("message", "") or node.text or ""
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
        r"selector '([^']+)'",
        r'Locator\("([^"]+)"\)',
        r"locator\('([^']+)'\)",
        r"\.locator\(['\"]([^'\"]+)['\"]\)",
    ]
    for p in patterns:
        m = re.search(p, message)
        if m:
            return m.group(1)
    return ""
