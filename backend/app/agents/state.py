from typing import TypedDict, Any


class QAState(TypedDict):
    run_id: str
    trigger_commit: str
    trigger_branch: str
    changed_files: list[str]
    suites_to_run: list[str]
    junit_xml: str
    failures: list[dict[str, Any]]
    dom_report: dict[str, Any]
    classification: str
    confidence: float
    evidence: str
    proposed_fix: dict[str, Any] | None
    approved: bool
    approved_by: str
    auto_fixed: bool
    commit_sha: str
    report_path: str
    error: str | None
