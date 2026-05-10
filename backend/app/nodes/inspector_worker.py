"""
Playwright DOM inspector subprocess worker.
Called as: python -m app.nodes.inspector_worker '<json_payload>'
Returns JSON to stdout.

Deterministic fingerprint scan — zero LLM tokens for common cases.
"""
import json
import sys
import difflib


def _class_prefix_overlap(old_cls: str, new_cls: str) -> float:
    if not old_cls or not new_cls:
        return 0.0
    ratio = difflib.SequenceMatcher(None, old_cls, new_cls).ratio()
    return ratio


def inspect_selectors(url: str, failing_selectors: list[str], timeout: int = 20000) -> dict:
    from playwright.sync_api import sync_playwright

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        except Exception as exc:
            browser.close()
            return {"error": str(exc), "selectors": {}}

        for selector in failing_selectors:
            if not selector:
                continue
            # URL assertions (wait_for_url type failures) — no DOM to inspect
            if selector.startswith("http") or "wait_for_url" in selector:
                results[selector] = {"found_on_page": False, "candidates": [], "note": "URL assertion — no DOM selector"}
                continue
            try:
                found_count = page.locator(selector).count()
                if found_count > 0:
                    results[selector] = {
                        "found_on_page": True,
                        "candidates": [],
                        "note": "Selector still resolves — failure is NOT selector-related",
                    }
                    continue

                candidates = _fingerprint_scan(page, selector)
                results[selector] = {
                    "found_on_page": False,
                    "candidates": candidates[:3],
                }
            except Exception as exc:
                results[selector] = {"error": str(exc), "candidates": []}

        browser.close()

    return {"url": url, "selectors": results}


def _fingerprint_scan(page, old_selector: str) -> list[dict]:
    tag = _guess_tag(old_selector)
    old_classes = _extract_classes(old_selector)
    old_text = _extract_text(old_selector)

    candidates = []
    try:
        elements = page.locator(tag or "*").all()[:50]
    except Exception:
        return []

    for el in elements:
        score = 0.0
        reasons = []

        try:
            el_text = (el.inner_text() or "").strip()[:100]
            el_cls = el.get_attribute("class") or ""
            el_role = el.get_attribute("role") or ""
            el_aria = el.get_attribute("aria-label") or ""
            el_testid = el.get_attribute("data-testid") or ""
        except Exception:
            continue

        if old_text and el_text:
            sim = difflib.SequenceMatcher(None, old_text.lower(), el_text.lower()).ratio()
            if sim >= 0.9:
                score += 0.45
                reasons.append("text_match")
            elif sim >= 0.6:
                score += 0.25
                reasons.append("text_partial")

        if old_classes and el_cls:
            for old_c in old_classes:
                prefix = old_c[:4] if len(old_c) > 4 else old_c
                if any(prefix in c for c in el_cls.split()):
                    score += 0.30
                    reasons.append("class_prefix")
                    break

        if old_aria_label := _extract_aria(old_selector):
            if old_aria_label.lower() in el_aria.lower():
                score += 0.30
                reasons.append("aria_label")

        if score < 0.10:
            continue

        new_selector = _build_selector(el_cls, el_text, el_role, el_aria, el_testid, tag, old_classes)
        if new_selector:
            candidates.append({
                "selector": new_selector,
                "confidence": min(round(score, 2), 0.97),
                "match_reason": " + ".join(reasons),
            })

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates


def _guess_tag(selector: str) -> str:
    if selector.startswith("button") or "button" in selector.lower():
        return "button"
    if selector.startswith(".btn") or "btn" in selector.lower():
        return "button"
    if selector.startswith("a") or selector.startswith("#"):
        return ""
    return "button"


def _extract_classes(selector: str) -> list[str]:
    import re
    return re.findall(r'\.([a-zA-Z0-9_-]+)', selector)


def _extract_text(selector: str) -> str:
    import re
    m = re.search(r"has-text\(['\"]([^'\"]+)['\"]\)", selector)
    if m:
        return m.group(1)
    m = re.search(r":text\(['\"]([^'\"]+)['\"]\)", selector)
    return m.group(1) if m else ""


def _extract_aria(selector: str) -> str:
    import re
    m = re.search(r"aria-label=['\"]([^'\"]+)['\"]", selector)
    return m.group(1) if m else ""


def _build_selector(
    cls: str, text: str, role: str, aria: str, testid: str, tag: str,
    old_classes: list[str] | None = None,
) -> str:
    if testid:
        return f"[data-testid='{testid}']"
    if aria:
        return f"[aria-label='{aria}']"
    if cls:
        classes = cls.split()
        # Prefer a class that has structural similarity to old failing class
        if old_classes:
            best, best_score = classes[0], 0.0
            for c in classes:
                for oc in old_classes:
                    s = difflib.SequenceMatcher(None, oc, c).ratio()
                    if s > best_score and c not in ("btn", "button", "active", "disabled"):
                        best_score, best = s, c
            return f".{best}"
        return f".{classes[0]}"
    if text and tag:
        return f"{tag}:has-text('{text[:30]}')"
    return ""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No payload provided"}))
        sys.exit(1)
    try:
        payload = json.loads(sys.argv[1])
        result = inspect_selectors(
            url=payload["url"],
            failing_selectors=payload.get("selectors", []),
            timeout=payload.get("timeout", 20000),
        )
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
