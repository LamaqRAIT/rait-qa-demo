# INTENT: Account settings page renders profile form and password change form.
# JOURNEY: account_settings
# PAGE: /account.html
import pytest
from playwright.sync_api import Page, expect


def test_account_page_renders(page: Page, base_url: str) -> None:
    """Account settings page loads with profile form visible."""
    page.goto(f"{base_url}/account.html")
    expect(page.locator("#profile-form")).to_be_visible()
    expect(page.locator("#profile-name")).to_be_visible()
    expect(page.locator("#profile-email")).to_be_visible()


def test_account_save_profile(page: Page, base_url: str) -> None:
    """Saving profile shows a success confirmation."""
    page.goto(f"{base_url}/account.html")
    page.fill("#profile-name", "Updated Name")
    page.locator("#save-profile-btn").click()
    expect(page.locator("#profile-saved")).to_be_visible()


def test_account_signout_link(page: Page, base_url: str) -> None:
    """Sign out link is present and links to login page."""
    page.goto(f"{base_url}/account.html")
    link = page.locator("#signout-link")
    expect(link).to_be_visible()
    href = link.get_attribute("href")
    assert "login" in (href or ""), f"Sign-out link href unexpected: {href}"
