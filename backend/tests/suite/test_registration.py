# INTENT: New users can register with valid credentials and are redirected to login.
# JOURNEY: registration
# PAGE: /register.html
import pytest
from playwright.sync_api import Page, expect


def test_registration_form_renders(page: Page, base_url: str) -> None:
    """Registration page renders all required form fields."""
    page.goto(f"{base_url}/register.html")
    expect(page.locator("#full-name")).to_be_visible()
    expect(page.locator("#reg-email")).to_be_visible()
    expect(page.locator("#reg-password")).to_be_visible()
    expect(page.locator("#confirm-password")).to_be_visible()
    expect(page.locator("#reg-btn")).to_be_visible()


def test_registration_password_mismatch_shows_error(page: Page, base_url: str) -> None:
    """Mismatched passwords show an error message."""
    page.goto(f"{base_url}/register.html")
    page.fill("#full-name", "Test User")
    # Flow 6 target: id="reg-email". If drifted to "register-email" this FAILS.
    page.fill("#reg-email", "test@example.com")
    page.fill("#reg-password", "password123")
    page.fill("#confirm-password", "different456")
    page.locator("#reg-btn").click()
    expect(page.locator("#reg-error")).to_be_visible()


def test_registration_success_redirects(page: Page, base_url: str) -> None:
    """Valid registration shows success and redirects to login."""
    page.goto(f"{base_url}/register.html")
    page.fill("#full-name", "Demo User")
    page.fill("#reg-email", "newuser@example.com")
    page.fill("#reg-password", "validpass123")
    page.fill("#confirm-password", "validpass123")
    page.locator("#reg-btn").click()
    expect(page.locator("#reg-success")).to_be_visible()
