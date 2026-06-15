# INTENT: Model Performance page renders comparison charts, ethical dimension cards, and filter controls.
# JOURNEY: model_performance_flow
# PAGE: /model-performance
import pytest
from playwright.sync_api import Page, expect


def test_model_performance_page_loads(app) -> None:
    """Model Performance page renders without error."""
    page, base_url = app
    page.goto(f"{base_url}/model-performance")
    page.wait_for_load_state("networkidle")

    error = page.locator("text=Something went wrong, text=Application Error").first
    assert not error.is_visible(), "Error boundary triggered on model-performance"


def test_model_performance_has_model_cards(app) -> None:
    """Model performance page shows model registry cards."""
    page, base_url = app
    page.goto(f"{base_url}/model-performance")
    page.wait_for_load_state("networkidle")

    cards = page.locator("[class*='card']").all()
    assert len(cards) >= 1, "No model cards found on model-performance page"


def test_model_performance_ethical_dimension_cards(app) -> None:
    """Ethical dimension score cards are visible."""
    page, base_url = app
    page.goto(f"{base_url}/model-performance")
    page.wait_for_load_state("networkidle")

    dim_section = page.locator(
        "text=Fairness, text=Transparency, text=Accuracy, text=Robustness, [class*='dimension']"
    ).first
    expect(dim_section).to_be_visible()


def test_model_performance_comparison_view(app) -> None:
    """Page renders a comparison chart or table when models are selected."""
    page, base_url = app
    page.goto(f"{base_url}/model-performance")
    page.wait_for_load_state("networkidle")

    # With dummy data the comparison view should show a chart or table
    chart_or_table = page.locator("table, svg, canvas, [class*='chart'], [class*='comparison']").first
    expect(chart_or_table).to_be_visible()


def test_model_performance_has_scatter_or_radar_chart(app) -> None:
    """Scatter or radar chart element is rendered."""
    page, base_url = app
    page.goto(f"{base_url}/model-performance")
    page.wait_for_load_state("networkidle")

    chart = page.locator("svg[class*='recharts'], canvas, [class*='scatter'], [class*='radar']").first
    expect(chart).to_be_visible()
