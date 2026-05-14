# INTENT: User can complete the checkout flow by clicking the submit button.
# JOURNEY: checkout_flow
import pytest
from playwright.sync_api import Page, expect


def test_checkout_submit_button_exists(page: Page, base_url: str) -> None:
    """Checkout page renders a submit button with expected class."""
    # INTENT: Submit button is present and has the correct CSS class.
    page.goto(f"{base_url}/checkout.html")

    # Flow 1 target: class selector. If drifted to btn-place-order this FAILS.
    submit_btn = page.locator(".btn-place-order")
    expect(submit_btn).to_be_visible()
    expect(submit_btn).to_be_enabled()


def test_checkout_submit_button_text(page: Page, base_url: str) -> None:
    """Checkout page submit button has correct text label."""
    # INTENT: Submit button text matches expected copy.
    page.goto(f"{base_url}/checkout.html")

    # Flow 2 target: text selector. If drifted to "Place Order" this FAILS.
    submit_btn = page.locator("button:has-text('Submit Order')")
    expect(submit_btn).to_be_visible()


def test_checkout_submit_completes(page: Page, base_url: str) -> None:
    """Clicking submit shows a success confirmation."""
    # INTENT: Submit button triggers visible confirmation.
    page.goto(f"{base_url}/checkout.html")
    page.locator(".btn-checkout").click()
    success = page.locator("#success-msg")
    expect(success).to_be_visible()
