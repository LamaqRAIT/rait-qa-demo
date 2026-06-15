"""
Canary node — three-tier infra checks that gate the full suite.
Tier ordering: hard → degraded → healthy (progressive confidence calibration).
  - hard fails   → env confidence 0.97 (complete outage)
  - degraded fails → env confidence 0.85 (partial/slow)
  - healthy fails  → env confidence 0.75 (JS degradation)
  - all pass     → env gated, suite runs normally
"""
import asyncio
import os
import sys
import structlog
from app.config import get_settings

log = structlog.get_logger()

# Tiers ordered from most critical to least — first failure short-circuits
_TIERS = [
    ("hard",     "tests/canary/test_canary_hard.py",     0.97),
    ("degraded", "tests/canary/test_canary_degraded.py", 0.85),
    ("healthy",  "tests/canary/test_canary_healthy.py",  0.75),
]


async def _run_tier(tier_file: str, base_url: str, timeout_sec: int = 30) -> tuple[bool, str]:
    """Run a single canary tier. Returns (passed, output)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest",
        tier_file,
        "-v", "--tb=short",
        f"--timeout={timeout_sec}",
        env={**os.environ, "BASE_URL": base_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    passed = proc.returncode == 0
    return passed, (stdout.decode() + stderr.decode())[:2000]


async def run_canary(run_id: str) -> dict:
    settings = get_settings()
    base_url = settings.base_url.rstrip("/")

    for tier_name, tier_file, env_confidence in _TIERS:
        passed, output = await _run_tier(tier_file, base_url)
        if not passed:
            log.warning(
                "canary.tier_failed",
                run_id=run_id,
                tier=tier_name,
                env_confidence=env_confidence,
            )
            return {
                "passed": False,
                "tier_failed": tier_name,
                "env_confidence": env_confidence,
                "output": output,
            }
        log.info("canary.tier_passed", run_id=run_id, tier=tier_name)

    log.info("canary.all_passed", run_id=run_id)
    return {"passed": True, "tier_failed": None, "env_confidence": None, "output": ""}
