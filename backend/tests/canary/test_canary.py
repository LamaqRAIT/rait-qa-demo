# INTENT: Two always-passing infra sanity checks that gate the full test suite.
# If either fails, the entire run is classified 'env' (not drift/bug).
import os
import pytest
from playwright.sync_api import Page, expect

BASE_URL = os.environ.get("BASE_URL", "https://lamaqrait.github.io/rait-qa-demo").rstrip("/")


def test_canary_page_reachable(page: Page) -> None:
    """Demo site base URL is reachable and returns a 200."""
    response = page.goto(BASE_URL + "/login.html")
    assert response is not None
    assert response.status == 200, f"Expected 200, got {response.status}"


def test_canary_login_form_present(page: Page) -> None:
    """Login page renders the expected form element."""
    page.goto(BASE_URL + "/login.html")
    form = page.locator("#email")
    expect(form).to_be_visible()
