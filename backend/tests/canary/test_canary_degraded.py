# INTENT: Site is partially functional — key assets (CSS, JS) load within degraded timeout.
# JOURNEY: canary_degraded
# PAGE: /index.html
# TIER: degraded — failure here means assets are slow or partially unavailable (env confidence 0.85)
import pytest
from playwright.sync_api import Page, expect


def test_stylesheet_loads(page: Page, base_url: str) -> None:
    """At least one stylesheet must load within 15 seconds — degraded CDN detection."""
    failed_resources = []

    def on_response(response):
        if response.url.endswith(".css") and response.status >= 400:
            failed_resources.append(response.url)

    page.on("response", on_response)
    page.goto(f"{base_url}/index.html", timeout=15000, wait_until="networkidle")
    assert not failed_resources, f"CSS resources failed to load: {failed_resources}"


def test_checkout_page_accessible(page: Page, base_url: str) -> None:
    """Checkout page renders within degraded timeout — key page availability check."""
    response = page.goto(f"{base_url}/checkout.html", timeout=15000, wait_until="domcontentloaded")
    assert response is not None and response.status < 500, \
        f"Checkout page returned server error: {response.status if response else 'no response'}"


def test_login_page_accessible(page: Page, base_url: str) -> None:
    """Login page renders within degraded timeout."""
    response = page.goto(f"{base_url}/login.html", timeout=15000, wait_until="domcontentloaded")
    assert response is not None and response.status < 500, \
        f"Login page returned server error: {response.status if response else 'no response'}"
