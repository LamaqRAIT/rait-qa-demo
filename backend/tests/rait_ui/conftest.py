"""
Shared fixtures for RAIT UI E2E tests.
TARGET: rait-demo-ui (Next.js) deployed on Cloud Run.
Set RAIT_UI_URL env var to override the default.
"""
import os
import pytest
from playwright.sync_api import Page, BrowserContext


RAIT_UI_URL = os.environ.get(
    "RAIT_UI_URL",
    "https://rait-demo-ui-1097873447958.us-central1.run.app",
).rstrip("/")

# Default nav timeout for Next.js client-side transitions (ms)
NAV_TIMEOUT = 15000


@pytest.fixture(scope="session")
def ui_base_url() -> str:
    return RAIT_UI_URL


@pytest.fixture
def app(page: Page, ui_base_url: str):
    """
    Pre-authenticated app fixture.
    With MOCK_AUTH=true the app auto-accepts the login API call,
    so we POST to /api/login once and re-use the session cookie.
    """
    page.set_default_timeout(NAV_TIMEOUT)
    page.set_default_navigation_timeout(NAV_TIMEOUT)

    # Attempt login; with mock auth the server accepts any credentials.
    page.goto(f"{ui_base_url}/login")
    page.wait_for_load_state("networkidle")

    email_input = page.locator('input[type="email"], input[placeholder*="email" i]').first
    password_input = page.locator('input[type="password"]').first

    if email_input.is_visible():
        email_input.fill("demo@raitracker.com")
        password_input.fill("password123")
        page.locator('button[type="submit"]').first.click()
        # Wait for either redirect or success indicator
        try:
            page.wait_for_url(f"{ui_base_url}/dashboard", timeout=8000)
        except Exception:
            # Mock auth may redirect differently — proceed anyway
            pass

    return page, ui_base_url
