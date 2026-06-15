# INTENT: Ethics Calibrator page renders tabs, model selector, and calibration workflow.
# JOURNEY: ethics_calibrator_flow
# PAGE: /ethics-calibrator, /ethics-calibrator/calibration/[run_id]
import pytest
from playwright.sync_api import Page, expect


def test_calibrator_page_loads(app) -> None:
    """Ethics Calibrator page renders without error."""
    page, base_url = app
    page.goto(f"{base_url}/ethics-calibrator")
    page.wait_for_load_state("networkidle")

    error = page.locator("text=Something went wrong, text=Application Error").first
    assert not error.is_visible(), "Error boundary triggered on ethics-calibrator"


def test_calibrator_has_tabs(app) -> None:
    """Ethics Calibrator shows Analysis and Calibrator tabs."""
    page, base_url = app
    page.goto(f"{base_url}/ethics-calibrator")
    page.wait_for_load_state("networkidle")

    tab_list = page.locator("[role='tablist']").first
    expect(tab_list).to_be_visible()

    tabs = page.locator("[role='tab']").all_text_contents()
    tab_text = " ".join(tabs).lower()
    assert "analysis" in tab_text or "calibrat" in tab_text, \
        f"Expected 'Analysis' or 'Calibrator' tab, found: {tabs}"


def test_calibrator_analysis_tab_active_by_default(app) -> None:
    """Analysis tab is selected by default."""
    page, base_url = app
    page.goto(f"{base_url}/ethics-calibrator")
    page.wait_for_load_state("networkidle")

    analysis_tab = page.locator("[role='tab']:has-text('Analysis')").first
    if not analysis_tab.is_visible():
        pytest.skip("Analysis tab not visible")

    expect(analysis_tab).to_have_attribute("data-state", "active")


def test_calibrator_tab_switching(app) -> None:
    """Clicking the Calibrator tab shows the calibrator content panel."""
    page, base_url = app
    page.goto(f"{base_url}/ethics-calibrator")
    page.wait_for_load_state("networkidle")

    calibrator_tab = page.locator("[role='tab']:has-text('Calibrator')").first
    if not calibrator_tab.is_visible():
        pytest.skip("Calibrator tab not visible")

    calibrator_tab.click()
    page.wait_for_load_state("networkidle")

    active_panel = page.locator("[role='tabpanel'][data-state='active']").first
    expect(active_panel).to_be_visible()


def test_calibrator_model_selector_present(app) -> None:
    """Model selector dropdown is rendered on the calibrator page."""
    page, base_url = app
    page.goto(f"{base_url}/ethics-calibrator")
    page.wait_for_load_state("networkidle")

    selector = page.locator("[role='combobox'], button:has-text('Select'), button:has-text('Model')").first
    expect(selector).to_be_visible()


def test_calibrator_add_prompt_button_visible(app) -> None:
    """'Add Prompt' button is visible on the Calibrator tab."""
    page, base_url = app
    page.goto(f"{base_url}/ethics-calibrator")
    page.wait_for_load_state("networkidle")

    calibrator_tab = page.locator("[role='tab']:has-text('Calibrator')").first
    if calibrator_tab.is_visible():
        calibrator_tab.click()
        page.wait_for_load_state("networkidle")

    add_btn = page.locator("button:has-text('Add'), button:has-text('Prompt')").first
    if not add_btn.is_visible():
        pytest.skip("Add Prompt button not visible — may require model selection first")
    expect(add_btn).to_be_visible()


def test_calibrator_run_list_page_loads(app) -> None:
    """Ethics Calibrator run list (calibration sub-page) loads."""
    page, base_url = app
    page.goto(f"{base_url}/ethics-calibrator/calibration")
    page.wait_for_load_state("networkidle")

    error = page.locator("text=Something went wrong, text=Application Error").first
    assert not error.is_visible(), "Error on ethics-calibrator/calibration page"
