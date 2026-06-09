import os
import pytest
from playwright.sync_api import Page, BrowserContext, expect

# Base URL of the demo site. Override with BASE_URL env var.
BASE_URL = os.environ.get(
    "BASE_URL",
    "https://lamaqrait.github.io/rait-qa-demo"
).rstrip("/")


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    # Cloud Run seccomp blocks user namespaces; disable all sandbox layers
    # and GPU/zygote processes that require elevated kernel privileges.
    # --single-process removed: causes silent hangs in Linux containers.
    return {
        **browser_type_launch_args,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-zygote",
            "--no-proxy-server",            # prevent WPAD/proxy auto-detect hang in Cloud Run
            "--disable-background-networking",  # suppress startup network calls to Google services
            # Google Fonts DNS → loopback: font requests fail-fast instead of hanging 30s+.
            # Pages render with fallback fonts; load event fires in <1s from Cloud Run.
            "--host-resolver-rules=MAP fonts.googleapis.com 127.0.0.1,MAP fonts.gstatic.com 127.0.0.1",
        ],
    }


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(autouse=True)
def fast_timeouts(page: Page) -> None:
    page.set_default_timeout(30_000)
    page.set_default_navigation_timeout(30_000)
    # pytest-playwright uses a separate default (5000ms) for expect assertions;
    # page.set_default_timeout alone does not override it.
    expect.set_options(timeout=30_000)


@pytest.fixture
def page_with_base(page: Page, base_url: str):
    """Page fixture pre-navigated to the demo site base."""
    return page, base_url
