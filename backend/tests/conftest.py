import os
import pytest
from playwright.sync_api import Page, BrowserContext

# Base URL of the demo site. Override with BASE_URL env var.
BASE_URL = os.environ.get(
    "BASE_URL",
    "https://lamaqrait.github.io/rait-qa-demo"
).rstrip("/")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    """Add --no-sandbox for containerised Linux environments (Cloud Run, Docker)."""
    return {**browser_type_launch_args, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}


@pytest.fixture
def page_with_base(page: Page, base_url: str):
    """Page fixture pre-navigated to the demo site base."""
    return page, base_url
