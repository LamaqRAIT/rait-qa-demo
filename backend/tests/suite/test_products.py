# INTENT: Product listing renders correctly and add-to-cart works.
# JOURNEY: product_listing
import pytest
from playwright.sync_api import Page, expect


def test_product_grid_renders(page: Page, base_url: str) -> None:
    """Products page shows the product grid with at least 4 items."""
    page.goto(f"{base_url}/products.html")
    expect(page.locator(".product-grid")).to_be_visible()
    cards = page.locator(".product-card")
    assert cards.count() >= 4, f"Expected ≥4 product cards, got {cards.count()}"


def test_product_names_visible(page: Page, base_url: str) -> None:
    """Each product card shows a name and price."""
    page.goto(f"{base_url}/products.html")
    for card in page.locator(".product-card").all():
        expect(card.locator(".product-name")).to_be_visible()
        expect(card.locator(".product-price")).to_be_visible()


def test_add_to_cart_shows_toast(page: Page, base_url: str) -> None:
    """Clicking 'Add to Cart' shows the confirmation toast."""
    page.goto(f"{base_url}/products.html")
    page.locator("[data-testid='add-to-cart']").first.click()
    expect(page.locator("#cart-toast")).to_be_visible()


def test_product_search_filter(page: Page, base_url: str) -> None:
    """Product search input filters the visible product cards."""
    page.goto(f"{base_url}/products.html")
    page.fill("#product-search", "keyboard")
    visible = [c for c in page.locator(".product-card").all() if c.is_visible()]
    assert len(visible) >= 1, "Expected at least 1 product matching 'keyboard'"
