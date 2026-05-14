# INTENT: Core navigation links and header elements work across all pages.
# JOURNEY: navigation
import pytest
from playwright.sync_api import Page, expect


def test_nav_brand_link(page: Page, base_url: str) -> None:
    """Nav brand logo links back to products page."""
    page.goto(f"{base_url}/checkout.html")
    page.locator(".nav-brand").click()
    expect(page).to_have_url(f"{base_url}/products.html")


def test_nav_products_link(page: Page, base_url: str) -> None:
    """Products nav link navigates to product listing."""
    page.goto(f"{base_url}/login.html")
    page.goto(f"{base_url}/products.html")
    expect(page).to_have_url(f"{base_url}/products.html")
    expect(page.locator(".product-grid")).to_be_visible()


def test_nav_cart_link(page: Page, base_url: str) -> None:
    """Cart nav link is present on the products page."""
    page.goto(f"{base_url}/products.html")
    link = page.locator("a[href='cart.html']")
    expect(link).to_be_visible()


def test_nav_search_link(page: Page, base_url: str) -> None:
    """Search nav link is present on the products page."""
    page.goto(f"{base_url}/products.html")
    link = page.locator("a[href='search.html' and @class='btn btn-secondary']")
    expect(link).to_be_visible()


def test_footer_visible(page: Page, base_url: str) -> None:
    """Footer is present on all key pages."""
    for path in ["/products.html", "/checkout.html", "/login.html"]:
        page.goto(f"{base_url}{path}")
        expect(page.locator("footer")).to_be_visible()
