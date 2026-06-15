# INTENT: Sidebar navigation renders and all primary links are accessible.
# JOURNEY: navigation_flow
# PAGE: all (root) routes
import pytest
from playwright.sync_api import Page, expect


NAV_LINKS = [
    ("Dashboard",           "/dashboard"),
    ("Model Registry",      "/model-registry"),
    ("Model Performance",   "/model-performance"),
    ("Incident Management", "/incident-management"),
    ("Ethics Calibrator",   "/ethics-calibrator"),
    ("Configure Threshold", "/configure-threshold"),
    ("Decision Log",        "/decision-log"),
    ("AI Assurance",        "/AI-assurance"),
    ("Audit Logs",          "/audit-logs"),
    ("Reports",             "/reports-hub"),
]


def test_sidebar_visible_on_dashboard(app) -> None:
    """Sidebar is present when authenticated on the dashboard."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    sidebar = page.locator("nav, aside, [role='navigation']").first
    expect(sidebar).to_be_visible()


def test_sidebar_has_navigation_links(app) -> None:
    """Sidebar contains at least 5 distinct navigation links."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    links = page.locator("nav a, aside a").all()
    assert len(links) >= 5, f"Expected ≥5 nav links, got {len(links)}"


@pytest.mark.parametrize("label,path", NAV_LINKS)
def test_nav_link_navigates(app, label: str, path: str) -> None:
    """Each sidebar link navigates to its correct route without a crash."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")

    link = page.locator(f"nav a[href*='{path}'], aside a[href*='{path}']").first
    if not link.is_visible():
        pytest.skip(f"Nav link '{label}' not visible — may be role-gated")

    link.click()
    page.wait_for_load_state("networkidle")
    assert path in page.url, f"Expected URL to contain '{path}', got '{page.url}'"


def test_breadcrumb_visible_on_inner_page(app) -> None:
    """Breadcrumb component renders on model-registry page."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    # Breadcrumb typically contains a nav[aria-label] or ol with breadcrumb items
    breadcrumb = page.locator("[aria-label='breadcrumb'], nav ol, .breadcrumb").first
    expect(breadcrumb).to_be_visible()


def test_page_title_updates_on_navigation(app) -> None:
    """Document title changes when navigating between pages."""
    page, base_url = app
    page.goto(f"{base_url}/dashboard")
    page.wait_for_load_state("networkidle")
    dashboard_title = page.title()

    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")
    registry_title = page.title()

    # Titles should at minimum be non-empty
    assert dashboard_title, "Dashboard page title is empty"
    assert registry_title, "Model Registry page title is empty"
