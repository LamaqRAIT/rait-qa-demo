# INTENT: Dashboard page loads with KPI cards, model selector, and charts.
# JOURNEY: dashboard_flow
# PAGE: /dashboard
import pytest
from playwright.sync_api import Page, expect


def test_dashboard_page_loads(app) -> None:
    """Dashboard page renders without error."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    # No error boundary should be shown
    error = page.locator("text=Something went wrong, text=Application Error").first
    assert not error.is_visible(), "Error boundary triggered on dashboard"


def test_dashboard_has_kpi_cards(app) -> None:
    """KPI summary cards are visible on the dashboard."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    # KPI cards are rendered inside Card components — look for multiple card elements
    cards = page.locator("[class*='card'], [data-testid*='kpi'], [class*='kpi']").all()
    assert len(cards) >= 3, f"Expected ≥3 KPI cards, found {len(cards)}"


def test_dashboard_model_selector_present(app) -> None:
    """Model selector dropdown is rendered on the dashboard."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    # SearchableVirtualSelect renders a combobox or button-like trigger
    selector = page.locator(
        "[role='combobox'], [aria-haspopup='listbox'], button:has-text('Select'), button:has-text('Model')"
    ).first
    expect(selector).to_be_visible()


def test_dashboard_has_chart_or_graph(app) -> None:
    """At least one chart/graph element is rendered on the dashboard."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    # Recharts renders SVG; ECharts renders canvas
    chart = page.locator("svg[class*='recharts'], canvas, [class*='chart']").first
    expect(chart).to_be_visible()


def test_dashboard_status_banner_present(app) -> None:
    """Status banner component is visible on the dashboard."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    # StatusBanner renders with a pulsing circle indicator
    banner = page.locator("[class*='status'], [class*='banner'], [class*='pulse']").first
    expect(banner).to_be_visible()


def test_dashboard_dimension_cards_visible(app) -> None:
    """Ethical dimension cards section is present."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    # EthicalDimensionCards renders metric dimension sections
    section = page.locator("[class*='dimension'], text=Fairness, text=Transparency, text=Accuracy").first
    expect(section).to_be_visible()


def test_dashboard_tabs_present(app) -> None:
    """Dashboard tab bar renders (single model / comparison tabs)."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    tabs = page.locator("[role='tablist']").first
    expect(tabs).to_be_visible()
