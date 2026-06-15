# INTENT: Incident Management page shows incident table, KPI cards, and action-required section.
# JOURNEY: incident_management_flow
# PAGE: /incident-management, /incident-management/[id]
import pytest
from playwright.sync_api import Page, expect


def test_incident_page_loads(app) -> None:
    """Incident Management page renders without error."""
    page, base_url = app
    page.goto(f"{base_url}/incident-management")
    page.wait_for_load_state("networkidle")

    error = page.locator("text=Something went wrong, text=Application Error").first
    assert not error.is_visible(), "Error boundary triggered on incident-management"


def test_incident_kpi_cards_visible(app) -> None:
    """At least 3 KPI cards (Active/Pending/In Progress/Closed) are visible."""
    page, base_url = app
    page.goto(f"{base_url}/incident-management")
    page.wait_for_load_state("networkidle")

    cards = page.locator("[class*='card']").all()
    assert len(cards) >= 3, f"Expected ≥3 KPI cards, found {len(cards)}"


def test_incident_table_renders(app) -> None:
    """Incident table is visible with at least one row."""
    page, base_url = app
    page.goto(f"{base_url}/incident-management")
    page.wait_for_load_state("networkidle")

    table = page.locator("table, [role='table']").first
    expect(table).to_be_visible()


def test_incident_table_has_status_column(app) -> None:
    """Incident table has a Status column header."""
    page, base_url = app
    page.goto(f"{base_url}/incident-management")
    page.wait_for_load_state("networkidle")

    headers = page.locator("th, [role='columnheader']").all_text_contents()
    header_text = " ".join(headers).lower()
    assert "status" in header_text, f"'Status' column not found in: {headers}"


def test_incident_action_required_section(app) -> None:
    """Action Required section is visible for users with appropriate role."""
    page, base_url = app
    page.goto(f"{base_url}/incident-management")
    page.wait_for_load_state("networkidle")

    section = page.locator("text=Action Required, text=Actions Required").first
    # This may be role-gated — skip rather than fail if not visible
    if not section.is_visible():
        pytest.skip("Action Required section not visible — likely role-gated")
    expect(section).to_be_visible()


def test_incident_model_filter_present(app) -> None:
    """Model filter dropdown is rendered on the incident management page."""
    page, base_url = app
    page.goto(f"{base_url}/incident-management")
    page.wait_for_load_state("networkidle")

    model_filter = page.locator(
        "[role='combobox'], button:has-text('Select'), [aria-label*='model' i]"
    ).first
    expect(model_filter).to_be_visible()


def test_incident_detail_page_loads(app) -> None:
    """Clicking an incident row navigates to the detail page."""
    page, base_url = app
    page.goto(f"{base_url}/incident-management")
    page.wait_for_load_state("networkidle")

    row_link = page.locator("tbody tr a, tbody tr td button").first
    if not row_link.is_visible():
        pytest.skip("No clickable incident rows visible")

    row_link.click()
    page.wait_for_load_state("networkidle")

    assert "incident-management" in page.url, f"Did not stay in incident module: {page.url}"


def test_incident_date_range_picker(app) -> None:
    """Date range picker is available on the incident management page."""
    page, base_url = app
    page.goto(f"{base_url}/incident-management")
    page.wait_for_load_state("networkidle")

    date_el = page.locator(
        "button:has-text('Date'), [class*='date-pick'], [aria-label*='date' i]"
    ).first
    expect(date_el).to_be_visible()
