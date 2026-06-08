"""
Canary node — two always-passing infra checks that gate the entire suite.
If either fails, the run is immediately classified 'env' and no tests execute.
"""
import asyncio
import os
import sys
import structlog
from app.config import get_settings

log = structlog.get_logger()


async def _stream_lines(stream, lines: list, label: str, run_id: str) -> None:
    """Read subprocess output line by line; log each line immediately."""
    while True:
        raw = await stream.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip()
        lines.append(line)
        log.debug(f"canary.{label}", run_id=run_id, line=line)


async def run_canary(run_id: str) -> dict:
    settings = get_settings()
    base_url = settings.base_url.rstrip("/")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest",
        "tests/canary/",
        "-v", "--tb=short",
        "--timeout=60",
        env={**os.environ, "BASE_URL": base_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # put Chrome children in same session for clean kill
    )

    lines: list[str] = []
    try:
        await asyncio.wait_for(
            asyncio.gather(
                _stream_lines(proc.stdout, lines, "stdout", run_id),
                _stream_lines(proc.stderr, lines, "stderr", run_id),
                proc.wait(),
            ),
            timeout=180,
        )
    except asyncio.TimeoutError:
        # Kill the whole process group so Chrome children don't keep pipes open
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        await asyncio.sleep(0.5)
        log.error("canary.process_timeout", run_id=run_id, last_lines=lines[-10:])
        output = "\n".join(lines) + "\n[TIMEOUT: canary killed after 180s]"
        return {"passed": False, "output": output[:3000]}

    passed = proc.returncode == 0
    output = "\n".join(lines)
    if not passed:
        log.warning("canary.failed", run_id=run_id, returncode=proc.returncode, output=output[:3000])
    else:
        log.info("canary.done", run_id=run_id, passed=passed)
    return {"passed": passed, "output": output[:2000]}
