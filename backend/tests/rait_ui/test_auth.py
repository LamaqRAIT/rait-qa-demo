# INTENT: Authentication pages render correctly and login form is functional.
# JOURNEY: auth_flow
# PAGE: /login, /unauthorize, /maintenance
import pytest
from playwright.sync_api import Page, expect


def test_login_page_renders(page: Page, ui_base_url: str) -> None:
    """Login page shows email/password inputs and submit button."""
    page.goto(f"{ui_base_url}/login")
    page.wait_for_load_state("networkidle")

    expect(page.locator('input[type="email"], input[placeholder*="email" i]').first).to_be_visible()
    expect(page.locator('input[type="password"]').first).to_be_visible()
    expect(page.locator('button[type="submit"]').first).to_be_visible()


def test_login_page_has_brand_title(page: Page, ui_base_url: str) -> None:
    """Login page contains the product name / heading."""
    page.goto(f"{ui_base_url}/login")
    page.wait_for_load_state("networkidle")

    heading = page.locator("h1").first
    expect(heading).to_be_visible()
    # The heading says "Login to your account"
    expect(heading).to_contain_text("Login")


def test_login_logo_visible(page: Page, ui_base_url: str) -> None:
    """RAIT logo image is present on the login page."""
    page.goto(f"{ui_base_url}/login")
    page.wait_for_load_state("networkidle")

    logo = page.locator('img[src*="rait"], img[alt*="rait" i], img[alt*="logo" i]').first
    expect(logo).to_be_visible()


def test_login_submit_with_empty_fields_blocked(page: Page, ui_base_url: str) -> None:
    """Submitting empty credentials does not navigate away from /login."""
    page.goto(f"{ui_base_url}/login")
    page.wait_for_load_state("networkidle")

    page.locator('button[type="submit"]').first.click()
    # HTML5 required validation should prevent navigation
    expect(page).to_have_url(f"{ui_base_url}/login")


def test_login_with_mock_credentials(page: Page, ui_base_url: str) -> None:
    """Submitting credentials with MOCK_AUTH shows success or redirects."""
    page.goto(f"{ui_base_url}/login")
    page.wait_for_load_state("networkidle")

    page.locator('input[type="email"], input[placeholder*="email" i]').first.fill("demo@raitracker.com")
    page.locator('input[type="password"]').first.fill("password123")
    page.locator('button[type="submit"]').first.click()

    # Either redirected away from /login OR a success message appeared
    success = page.locator("text=Logged in successfully").is_visible() if page.url.endswith("/login") else True
    assert success or "/login" not in page.url, "Login did not succeed — no redirect or success message"


def test_passkey_button_visible(page: Page, ui_base_url: str) -> None:
    """'Login with Pass-key' alternative button is rendered."""
    page.goto(f"{ui_base_url}/login")
    page.wait_for_load_state("networkidle")

    expect(page.locator("text=Login with Pass-key")).to_be_visible()


def test_forgot_password_link_exists(page: Page, ui_base_url: str) -> None:
    """'Forgot your password?' link is present."""
    page.goto(f"{ui_base_url}/login")
    page.wait_for_load_state("networkidle")

    expect(page.locator("text=Forgot your password?")).to_be_visible()


def test_unauthorize_page_renders(page: Page, ui_base_url: str) -> None:
    """Unauthorize page loads without crashing."""
    page.goto(f"{ui_base_url}/unauthorize")
    page.wait_for_load_state("networkidle")
    expect(page.locator("body")).to_be_visible()


def test_maintenance_page_renders(page: Page, ui_base_url: str) -> None:
    """Maintenance page loads without crashing."""
    page.goto(f"{ui_base_url}/maintenance")
    page.wait_for_load_state("networkidle")
    expect(page.locator("body")).to_be_visible()
