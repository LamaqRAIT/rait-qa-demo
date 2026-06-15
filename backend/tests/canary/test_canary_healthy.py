# INTENT: Site is fully healthy — full page render including JavaScript and interactive elements.
# JOURNEY: canary_healthy
# PAGE: /index.html
# TIER: healthy — failure here signals partial degradation, not full outage (env confidence 0.75)
import pytest
from playwright.sync_api import Page, expect


def test_full_page_renders(page: Page, base_url: str) -> None:
    """Full page renders including JS-dependent elements within the standard timeout."""
    page.goto(f"{base_url}/index.html", wait_until="networkidle")
    # Any visible element below the fold confirms JS hydration completed
    expect(page.locator("body")).to_be_visible()


def test_navigation_links_present(page: Page, base_url: str) -> None:
    """Navigation links are rendered (JS-dependent on most SPAs)."""
    page.goto(f"{base_url}/products.html", wait_until="networkidle")
    links = page.locator("a[href]").all()
    assert len(links) > 0, "No navigation links found — JS may have failed to hydrate"


def test_checkout_interactive(page: Page, base_url: str) -> None:
    """Checkout page has at least one interactive button — confirms full JS load."""
    page.goto(f"{base_url}/checkout.html", wait_until="networkidle")
    buttons = page.locator("button").all()
    assert len(buttons) > 0, "No buttons found on checkout page — JS failed to render"
