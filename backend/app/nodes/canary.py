"""
Canary node — two always-passing infra checks that gate the entire suite.
If either fails, the run is immediately classified 'env' and no tests execute.
"""
import asyncio
import structlog
from app.config import get_settings

log = structlog.get_logger()


async def run_canary(run_id: str) -> dict:
    settings = get_settings()
    base_url = settings.base_url.rstrip("/")

    proc = await asyncio.create_subprocess_exec(
        "python", "-m", "pytest",
        "tests/canary/",
        "-v", "--tb=short",
        "--timeout=30",
        env={**__import__("os").environ, "BASE_URL": base_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    passed = proc.returncode == 0
    output = stdout.decode() + stderr.decode()
    if not passed:
        log.warning("canary.failed", run_id=run_id, returncode=proc.returncode, output=output[:3000])
    else:
        log.info("canary.done", run_id=run_id, passed=passed)
    return {"passed": passed, "output": output[:2000]}
