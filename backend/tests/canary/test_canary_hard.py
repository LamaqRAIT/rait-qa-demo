# INTENT: Core site infrastructure is reachable — homepage loads within the hard timeout.
# JOURNEY: canary_hard
# PAGE: /index.html
# TIER: hard — failure here means complete site outage (env confidence 0.97)
import pytest
from playwright.sync_api import Page, expect


def test_homepage_reachable(page: Page, base_url: str) -> None:
    """Homepage must return HTTP 200 and render within 5 seconds."""
    response = page.goto(f"{base_url}/index.html", timeout=5000, wait_until="domcontentloaded")
    assert response is not None, "No HTTP response received"
    assert response.status == 200, f"Expected HTTP 200, got {response.status}"


def test_homepage_not_blank(page: Page, base_url: str) -> None:
    """Homepage must not be blank — any visible content signals the CDN is serving."""
    page.goto(f"{base_url}/index.html", timeout=5000, wait_until="domcontentloaded")
    body = page.locator("body")
    expect(body).not_to_be_empty()
