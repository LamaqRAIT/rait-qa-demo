# INTENT: Cart page renders items, allows quantity adjustment, and has a checkout button.
# JOURNEY: cart_flow
# PAGE: /cart.html
import pytest
from playwright.sync_api import Page, expect


def test_cart_renders_items(page: Page, base_url: str) -> None:
    """Cart page loads with pre-seeded items visible."""
    page.goto(f"{base_url}/cart.html")
    expect(page.locator("#item-headphones")).to_be_visible()
    expect(page.locator("#item-cable")).to_be_visible()


def test_cart_qty_increase(page: Page, base_url: str) -> None:
    """Clicking qty+ increases the item quantity."""
    page.goto(f"{base_url}/cart.html")
    qty_el = page.locator("#headphones-qty")
    initial = int(qty_el.inner_text())
    page.locator("[data-testid='qty-increase-headphones']").click()
    new_qty = int(qty_el.inner_text())
    assert new_qty == initial + 1, f"Expected {initial + 1}, got {new_qty}"


def test_cart_qty_decrease_minimum(page: Page, base_url: str) -> None:
    """Qty cannot go below 1."""
    page.goto(f"{base_url}/cart.html")
    page.locator("[data-testid='qty-decrease-headphones']").click()
    qty = int(page.locator("#headphones-qty").inner_text())
    assert qty >= 1, f"Qty dropped below 1: {qty}"


def test_cart_checkout_button_present(page: Page, base_url: str) -> None:
    """Cart page has a checkout CTA button that links to checkout.html."""
    page.goto(f"{base_url}/cart.html")
    # Flow 4 target: class="btn-cart-checkout". If drifted to btn-proceed-checkout this FAILS.
    btn = page.locator(".btn-cart-checkout")
    expect(btn).to_be_visible()
    href = btn.get_attribute("href")
    assert "checkout" in (href or ""), f"Checkout button href unexpected: {href}"
