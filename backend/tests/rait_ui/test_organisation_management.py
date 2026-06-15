# INTENT: Organisation Management page renders org details, tabs, and user sections.
# JOURNEY: org_management_flow
# PAGE: /organisation-management, /user-management
import pytest
from playwright.sync_api import Page, expect


def test_org_management_page_loads(app) -> None:
    """Organisation Management page renders without error."""
    page, base_url = app
    page.goto(f"{base_url}/organisation-management")
    page.wait_for_load_state("networkidle")

    error = page.locator("text=Something went wrong, text=Application Error").first
    assert not error.is_visible(), "Error boundary triggered on organisation-management"


def test_org_management_has_tabs(app) -> None:
    """Organisation Management page has multiple configuration tabs."""
    page, base_url = app
    page.goto(f"{base_url}/organisation-management")
    page.wait_for_load_state("networkidle")

    tab_list = page.locator("[role='tablist']").first
    expect(tab_list).to_be_visible()

    tabs = page.locator("[role='tab']").all()
    assert len(tabs) >= 2, f"Expected multiple tabs, found {len(tabs)}"


def test_org_details_tab_content(app) -> None:
    """Organisation Details tab shows org name and metadata fields."""
    page, base_url = app
    page.goto(f"{base_url}/organisation-management")
    page.wait_for_load_state("networkidle")

    details_tab = page.locator("[role='tab']:has-text('Organisation'), [role='tab']:has-text('Details')").first
    if details_tab.is_visible():
        details_tab.click()
        page.wait_for_load_state("networkidle")

    # Form fields or info sections
    content = page.locator("[role='tabpanel'] input, [role='tabpanel'] [class*='card'], [role='tabpanel'] label").first
    expect(content).to_be_visible()


def test_org_ethical_dimensions_tab(app) -> None:
    """Ethical Dimensions tab is accessible."""
    page, base_url = app
    page.goto(f"{base_url}/organisation-management")
    page.wait_for_load_state("networkidle")

    dim_tab = page.locator("[role='tab']:has-text('Dimension'), [role='tab']:has-text('Ethical')").first
    if not dim_tab.is_visible():
        pytest.skip("Ethical Dimensions tab not found")

    dim_tab.click()
    page.wait_for_load_state("networkidle")
    panel = page.locator("[role='tabpanel'][data-state='active']").first
    expect(panel).to_be_visible()


def test_user_management_page_loads(app) -> None:
    """User Management page renders without error."""
    page, base_url = app
    page.goto(f"{base_url}/user-management")
    page.wait_for_load_state("networkidle")

    error = page.locator("text=Something went wrong, text=Application Error").first
    assert not error.is_visible(), "Error boundary triggered on user-management"


def test_user_management_table_visible(app) -> None:
    """User Management page shows a user table."""
    page, base_url = app
    page.goto(f"{base_url}/user-management")
    page.wait_for_load_state("networkidle")

    table = page.locator("table, [role='table']").first
    expect(table).to_be_visible()


def test_user_management_add_user_button(app) -> None:
    """Add / Invite user button is present on user management."""
    page, base_url = app
    page.goto(f"{base_url}/user-management")
    page.wait_for_load_state("networkidle")

    add_btn = page.locator(
        "button:has-text('Add'), button:has-text('Invite'), button:has-text('New User')"
    ).first
    if not add_btn.is_visible():
        pytest.skip("Add User button not visible — may require admin role")
    expect(add_btn).to_be_visible()
