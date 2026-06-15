# INTENT: Search functionality returns relevant products and handles empty results.
# JOURNEY: search
# PAGE: /search.html
import pytest
from playwright.sync_api import Page, expect


def test_search_page_renders(page: Page, base_url: str) -> None:
    """Search page renders with input and search button."""
    page.goto(f"{base_url}/search.html")
    # Flow 5 target: id="search-input". If drifted to "search-query" this FAILS.
    expect(page.locator("#search-input")).to_be_visible()
    expect(page.locator("#search-btn")).to_be_visible()


def test_search_returns_results(page: Page, base_url: str) -> None:
    """Typing a keyword returns matching products."""
    page.goto(f"{base_url}/search.html")
    page.fill("#search-input", "headphones")
    page.locator("#search-btn").click()
    expect(page.locator(".product-grid")).to_be_visible()
    expect(page.locator(".product-card").first).to_be_visible()


def test_search_no_results_shows_message(page: Page, base_url: str) -> None:
    """Searching for a non-existent product shows the no-results message."""
    page.goto(f"{base_url}/search.html")
    page.fill("#search-input", "xyznonexistentproduct12345")
    page.locator("#search-btn").click()
    expect(page.locator("#no-results")).to_be_visible()
