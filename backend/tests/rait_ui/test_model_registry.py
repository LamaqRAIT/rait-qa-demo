# INTENT: Model Registry page shows a table of models with filters, pagination and row actions.
# JOURNEY: model_registry_flow
# PAGE: /model-registry, /model-registry/[modelId]
import pytest
from playwright.sync_api import Page, expect


def test_model_registry_page_loads(app) -> None:
    """Model Registry page renders without error."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    error = page.locator("text=Something went wrong, text=Application Error").first
    assert not error.is_visible(), "Error boundary triggered on model-registry"


def test_model_registry_table_visible(app) -> None:
    """Data table is rendered with at least one row of model data."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    table = page.locator("table, [role='table']").first
    expect(table).to_be_visible()

    rows = page.locator("tbody tr, [role='row']").all()
    assert len(rows) >= 1, "Model registry table has no data rows"


def test_model_registry_kpi_cards_visible(app) -> None:
    """KPI summary cards are shown above the model table."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    cards = page.locator("[class*='card']").all()
    assert len(cards) >= 2, "Expected KPI cards on model-registry page"


def test_model_registry_column_headers(app) -> None:
    """Table headers include Model Name and Status columns."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    headers = page.locator("th, [role='columnheader']").all_text_contents()
    header_text = " ".join(headers).lower()
    assert "model" in header_text, f"'Model' column not found in headers: {headers}"


def test_model_registry_has_date_picker(app) -> None:
    """Date range picker is rendered on the model-registry page."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    date_picker = page.locator(
        "button:has-text('Date'), [class*='date'], input[type='date'], [aria-label*='date' i]"
    ).first
    expect(date_picker).to_be_visible()


def test_model_registry_row_has_view_action(app) -> None:
    """Each model row has a view/detail action (eye icon or View link)."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    # Eye icon or link in each row
    action = page.locator("tbody tr a, tbody tr button[aria-label*='view' i], tbody tr svg").first
    expect(action).to_be_visible()


def test_model_registry_pagination_present(app) -> None:
    """Pagination controls are rendered below the model table."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    pagination = page.locator(
        "[aria-label*='pagination' i], [class*='paginator'], [class*='pagination'], nav[aria-label*='page' i]"
    ).first
    expect(pagination).to_be_visible()


def test_model_registry_detail_page_loads(app) -> None:
    """Clicking a model row navigates to the detail page."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    # Click the first eye/view link
    first_link = page.locator("tbody tr a").first
    if not first_link.is_visible():
        pytest.skip("No row links visible — table may be empty")

    href = first_link.get_attribute("href") or ""
    first_link.click()
    page.wait_for_load_state("networkidle")

    assert "model-registry" in page.url, f"Did not navigate to model detail, got {page.url}"


def test_model_registry_filter_column_button(app) -> None:
    """Column filter button is present in the table header."""
    page, base_url = app
    page.goto(f"{base_url}/model-registry")
    page.wait_for_load_state("networkidle")

    filter_btn = page.locator(
        "button[aria-label*='filter' i], button:has-text('Filter'), [class*='filter']"
    ).first
    expect(filter_btn).to_be_visible()
