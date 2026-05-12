"""
Selector index builder — scans all test files at startup and indexes:
- page_path: which URL each test visits (extracted from page.goto())
- selector_value: every CSS/text/role selector used
- selector_kind: css | text | role | testid | url

Results are stored in selector_test_index table and reused by suite_selector.py.
"""
import re
import glob
import os
import structlog
import app.db as db

log = structlog.get_logger()


_GOTO_RE        = re.compile(r'page\.goto\(.*?["\'](?:.*?)([a-z_-]+\.html)["\']')
_LOCATOR_RE     = re.compile(r'page\.locator\(["\']([^"\']+)["\']\)')
_HAS_TEXT_RE    = re.compile(r':has-text\(["\']([^"\']+)["\']\)')
_TEXT_RE        = re.compile(r':text\(["\']([^"\']+)["\']\)')
_ROLE_RE        = re.compile(r'getByRole\(["\']([^"\']+)["\']\)')
_TESTID_RE      = re.compile(r'getByTestId\(["\']([^"\']+)["\']\)')
_ARIA_RE        = re.compile(r'\[aria-label=["\']([^"\']+)["\']\]')
_CSS_CLASS_RE   = re.compile(r'\.([a-zA-Z][a-zA-Z0-9_-]+)')
_DATA_TESTID_RE = re.compile(r'\[data-testid=["\']([^"\']+)["\']\]')


def _parse_test_file(filepath: str) -> list[dict]:
    filename = os.path.basename(filepath)
    entries = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            lines = content.splitlines()
    except Exception:
        return entries

    current_page_path = ""

    for lineno, line in enumerate(lines, start=1):
        # Extract page URL (page.goto patterns)
        m = _GOTO_RE.search(line)
        if m:
            current_page_path = m.group(1)
            entries.append({
                "test_file": filename,
                "selector_kind": "url",
                "selector_value": current_page_path,
                "page_path": current_page_path,
                "line_number": lineno,
            })

        # CSS locators
        for m in _LOCATOR_RE.finditer(line):
            val = m.group(1)
            entries.append({
                "test_file": filename,
                "selector_kind": "css",
                "selector_value": val,
                "page_path": current_page_path,
                "line_number": lineno,
            })
            # Also extract class names from the selector
            for cls_m in _CSS_CLASS_RE.finditer(val):
                entries.append({
                    "test_file": filename,
                    "selector_kind": "css_class",
                    "selector_value": cls_m.group(1),
                    "page_path": current_page_path,
                    "line_number": lineno,
                })
            # has-text sub-extraction
            for ht_m in _HAS_TEXT_RE.finditer(val):
                entries.append({
                    "test_file": filename,
                    "selector_kind": "text",
                    "selector_value": ht_m.group(1),
                    "page_path": current_page_path,
                    "line_number": lineno,
                })

        # Standalone has-text
        for m in _HAS_TEXT_RE.finditer(line):
            entries.append({
                "test_file": filename,
                "selector_kind": "text",
                "selector_value": m.group(1),
                "page_path": current_page_path,
                "line_number": lineno,
            })

        # getByRole
        for m in _ROLE_RE.finditer(line):
            entries.append({
                "test_file": filename,
                "selector_kind": "role",
                "selector_value": m.group(1),
                "page_path": current_page_path,
                "line_number": lineno,
            })

        # getByTestId
        for m in _TESTID_RE.finditer(line):
            entries.append({
                "test_file": filename,
                "selector_kind": "testid",
                "selector_value": m.group(1),
                "page_path": current_page_path,
                "line_number": lineno,
            })

    return entries


async def build_selector_index(test_dir: str = "tests/suite") -> int:
    """Scan all test files in test_dir and rebuild the selector index. Returns count."""
    pattern = os.path.join(test_dir, "test_*.py")
    files = glob.glob(pattern)
    if not files:
        log.warning("selector_index.no_files", dir=test_dir)
        return 0

    all_entries = []
    for filepath in files:
        entries = _parse_test_file(filepath)
        all_entries.extend(entries)
        log.info("selector_index.parsed", file=os.path.basename(filepath), entries=len(entries))

    if all_entries:
        await db.upsert_selector_index(all_entries)

    log.info("selector_index.built", total_entries=len(all_entries), files=len(files))
    return len(all_entries)
