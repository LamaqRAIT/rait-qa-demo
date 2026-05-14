"""
Intent parser — extracts # INTENT: comments from Playwright test suite files.

Each test file carries a file-level intent comment describing the journey, and
individual test functions optionally carry a function-level intent describing the
specific assertion. The triage node uses these to reason semantically about whether
a UI change still satisfies the test's stated purpose.

File-level:    # INTENT: User can log in with valid credentials and reach the dashboard.
Function-level (inside def body):  # INTENT: Post-login destination is /dashboard — not any other page.
"""
import os

SUITE_DIR = "tests/suite"


def _test_name_to_basename(test_name: str) -> str | None:
    """
    Map a failure's test name string (JUnit classname.funcname) to a suite file basename.
    Uses the same keyword heuristic as triage._infer_test_file.
    """
    t = test_name.lower()
    if "login" in t:    return "test_login.py"
    if "cart" in t:     return "test_cart.py"
    if "search" in t:   return "test_search.py"
    if "registr" in t:  return "test_registration.py"
    if "account" in t:  return "test_account.py"
    if "product" in t:  return "test_products.py"
    if "nav" in t:      return "test_navigation.py"
    if "checkout" in t: return "test_checkout.py"
    return None


def _extract_function_name(test_name: str) -> str:
    """
    Return just the function name from a JUnit classname.funcname string.
    e.g. 'tests.suite.test_login.test_login_with_valid_credentials'
         → 'test_login_with_valid_credentials'
    """
    return test_name.split(".")[-1]


def _parse_file_intent(file_path: str) -> str:
    """Return the file-level # INTENT: comment (must appear before any def statement)."""
    try:
        with open(file_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("def ") or stripped.startswith("async def "):
                    break
                if stripped.startswith("# INTENT:"):
                    return stripped[len("# INTENT:"):].strip()
    except Exception:
        pass
    return ""


def _parse_function_intent(file_path: str, function_name: str) -> str:
    """
    Return the first # INTENT: comment found inside the named function body.
    Stops scanning at the next function definition.
    Returns empty string if no inline intent comment exists.
    """
    try:
        with open(file_path) as f:
            lines = f.readlines()

        in_function = False
        for line in lines:
            stripped = line.strip()
            # Detect entry into the target function
            if stripped.startswith(f"def {function_name}(") or stripped.startswith(f"async def {function_name}("):
                in_function = True
                continue
            if in_function:
                # Stop at the next function definition
                if stripped.startswith("def ") or stripped.startswith("async def "):
                    break
                if stripped.startswith("# INTENT:"):
                    return stripped[len("# INTENT:"):].strip()
    except Exception:
        pass
    return ""


def extract_test_intents(failures: list[dict]) -> list[dict]:
    """
    For every failure dict, extract the # INTENT: comment(s) from its test file.

    Returns a list of dicts:
    {
        "test":        <original test name from failure["test"]>,
        "file_intent": <file-level # INTENT: string>,
        "test_intent": <function-level # INTENT: string; falls back to file_intent if absent>
    }

    Falls back gracefully on any parse error — triage continues even if intents
    cannot be read.
    """
    results: list[dict] = []
    file_intent_cache: dict[str, str] = {}

    for failure in failures:
        test_name = failure.get("test", "")
        func_name = _extract_function_name(test_name)
        basename = _test_name_to_basename(test_name)

        if not basename:
            results.append({"test": test_name, "file_intent": "", "test_intent": ""})
            continue

        file_path = os.path.join(SUITE_DIR, basename)

        if file_path not in file_intent_cache:
            file_intent_cache[file_path] = _parse_file_intent(file_path)

        file_intent = file_intent_cache[file_path]
        func_intent = _parse_function_intent(file_path, func_name)

        results.append({
            "test": test_name,
            "file_intent": file_intent,
            "test_intent": func_intent if func_intent else file_intent,
        })

    return results
