"""
RunStatus enum and RunRecord dataclass.
Replaces LangGraph state management — every transition is a DB write.
"""
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class RunStatus(str, Enum):
    PLANNING        = "planning"
    RUNNING         = "running"
    INSPECTING      = "inspecting"
    TRIAGING        = "triaging"
    AWAITING_HUMAN  = "awaiting_human"
    HEALING         = "healing"
    COMPLETE        = "complete"
    FAILED          = "failed"
    QUARANTINED     = "quarantined"


@dataclass
class NodeState:
    state: str = "idle"
    annotation: str = ""


@dataclass
class TriageResult:
    classification: str = ""
    confidence: float = 0.0
    evidence: str = ""
    proposed_fix: dict[str, Any] | None = None


@dataclass
class RunRecord:
    id: str = ""
    status: RunStatus = RunStatus.PLANNING
    trigger_commit: str = ""
    trigger_branch: str = "main"
    suites_run: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    dom_report: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    triage: TriageResult = field(default_factory=TriageResult)
    node_states: dict[str, NodeState] = field(default_factory=dict)
    approved_by: str | None = None
    pr_url: str | None = None
    langfuse_trace_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    node_timings: dict[str, float] = field(default_factory=dict)
    human_override: bool = False
    override_reason: str | None = None
    consecutive_failures: int = 0
    force_hitl: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value if isinstance(self.status, RunStatus) else self.status,
            "trigger_commit": self.trigger_commit,
            "trigger_branch": self.trigger_branch,
            "suites_run": self.suites_run,
            "failures": self.failures,
            "dom_report": self.dom_report,
            "evidence": self.evidence,
            "triage_result": {
                "classification": self.triage.classification,
                "confidence": self.triage.confidence,
                "evidence": self.triage.evidence,
                "proposed_fix": self.triage.proposed_fix,
            } if self.triage.classification else None,
            "node_states": {
                k: {"node": k, "state": v.state, "annotation": v.annotation}
                for k, v in self.node_states.items()
            },
            "approved_by": self.approved_by,
            "pr_url": self.pr_url,
            "langfuse_trace_id": self.langfuse_trace_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "node_timings": self.node_timings,
            "human_override": self.human_override,
            "override_reason": self.override_reason,
            "consecutive_failures": self.consecutive_failures,
            "force_hitl": self.force_hitl,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RunRecord":
        triage_data = d.get("triage_result") or {}
        node_states = {
            k: NodeState(state=v.get("state", "idle"), annotation=v.get("annotation", ""))
            for k, v in (d.get("node_states") or {}).items()
        }
        return cls(
            id=d.get("id", ""),
            status=RunStatus(d.get("status", "planning")),
            trigger_commit=d.get("trigger_commit", ""),
            trigger_branch=d.get("trigger_branch", "main"),
            suites_run=d.get("suites_run", []),
            failures=d.get("failures", []),
            dom_report=d.get("dom_report", {}),
            evidence=d.get("evidence", {}),
            triage=TriageResult(
                classification=triage_data.get("classification", ""),
                confidence=triage_data.get("confidence", 0.0),
                evidence=triage_data.get("evidence", ""),
                proposed_fix=triage_data.get("proposed_fix"),
            ),
            node_states=node_states,
            approved_by=d.get("approved_by"),
            pr_url=d.get("pr_url"),
            langfuse_trace_id=d.get("langfuse_trace_id"),
            input_tokens=d.get("input_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            cost_usd=d.get("cost_usd", 0.0),
            node_timings=d.get("node_timings", {}),
            human_override=d.get("human_override", False),
            override_reason=d.get("override_reason"),
            consecutive_failures=d.get("consecutive_failures", 0),
            force_hitl=d.get("force_hitl", False),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.utcnow(),
            updated_at=datetime.fromisoformat(d["updated_at"]) if d.get("updated_at") else datetime.utcnow(),
        )
