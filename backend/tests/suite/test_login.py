# INTENT: User can log in with valid credentials and reach the dashboard.
# JOURNEY: login_flow
# PAGE: /login.html
import pytest
from playwright.sync_api import Page, expect


def test_login_with_valid_credentials(page: Page, base_url: str) -> None:
    """Valid credentials redirect to /dashboard."""
    # INTENT: Post-login destination is /dashboard — not any other page.
    page.goto(f"{base_url}/login.html")
    page.fill("#email", "demo@rait.ai")
    page.fill("#password", "password123")
    page.click("#login-btn")

    # Flow 3 target: URL assertion. If redirect goes to /products.html this FAILS.
    page.wait_for_url("**/dashboard.html", timeout=30000)
    expect(page).to_have_url(f"{base_url}/dashboard.html")


def test_login_form_renders(page: Page, base_url: str) -> None:
    """Login page renders email and password inputs."""
    # INTENT: Login form is accessible with correct input IDs.
    page.goto(f"{base_url}/login.html")
    expect(page.locator("#email")).to_be_visible()
    expect(page.locator("#password")).to_be_visible()
    expect(page.locator("#login-btn")).to_be_visible()


def test_login_invalid_credentials_shows_error(page: Page, base_url: str) -> None:
    """Wrong credentials show an error message, no redirect."""
    # INTENT: Invalid login does not proceed and shows an error.
    page.goto(f"{base_url}/login.html")
    page.fill("#email", "wrong@example.com")
    page.fill("#password", "wrongpassword")
    page.click("#login-btn")
    expect(page.locator("#error-msg")).to_be_visible()
