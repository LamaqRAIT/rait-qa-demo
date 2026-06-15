"""
RunStatus enum, HITLTrigger enum, and RunRecord dataclass.
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


class HITLTrigger(str, Enum):
    """Named sources that can force a run into AWAITING_HUMAN.
    Multiple triggers are OR-combined — any one is sufficient."""
    TOP2_AMBIGUOUS       = "top2_within_0.10"        # top-2 DOM candidates within 0.10 confidence
    LOW_CONFIDENCE       = "low_confidence"           # triage confidence below auto_fix_threshold
    FPR_BREAKER_ACTIVE   = "fpr_breaker_active"       # FPR circuit breaker raised threshold
    ERROR_RATE_ACTIVE    = "error_rate_breaker_active" # error rate circuit breaker suspended auto-fix
    CALIBRATION_MODE     = "calibration_mode"         # system in cold-start calibration mode
    LLM_SUITE_AMBIGUOUS  = "llm_suite_hitl_flag"      # suite selector LLM flagged ambiguity
    NO_PROPOSED_FIX      = "no_proposed_fix"          # drift classified but no fix could be generated
    HUMAN_RECLASSIFIED   = "human_reclassified"       # human changed classification on a previous run


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
    # Two-step gate signals (populated when vLLM two-step triage runs)
    p_class: float = 0.0           # softmax prob of winning class from Step A logprobs
    logprob_margin: float = 0.0    # top1 − top2 logprob (nats) — measures separation
    fix_grounded: bool | None = None  # proposed_fix.old found in test file
    dom_corroboration: float = 0.0    # best DOM candidate confidence


@dataclass
class RunRecord:
    id: str = ""
    status: RunStatus = RunStatus.PLANNING
    trigger_commit: str = ""
    trigger_branch: str = "main"
    team_id: str = "core-platform"
    suites_run: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    dom_report: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    triage: TriageResult = field(default_factory=TriageResult)
    node_states: dict[str, NodeState] = field(default_factory=dict)
    approved_by: str | None = None
    pr_url: str | None = None
    langfuse_trace_id: str | None = None
    langfuse_trace_url: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    node_timings: dict[str, float] = field(default_factory=dict)
    human_override: bool = False
    override_reason: str | None = None
    consecutive_failures: int = 0
    hitl_triggers: list[str] = field(default_factory=list)
    suite_selection_method: str = "fallback_all"

    @property
    def force_hitl(self) -> bool:
        """True when any HITL trigger has been set."""
        return len(self.hitl_triggers) > 0

    def add_hitl_trigger(self, trigger: "HITLTrigger") -> None:
        if trigger.value not in self.hitl_triggers:
            self.hitl_triggers.append(trigger.value)
    report_text: str | None = None
    # Confidence gate signals (set after gate evaluation)
    p_class: float | None = None
    logprob_margin: float | None = None
    nli_entailment: float | None = None
    fix_grounded: bool | None = None
    dom_corroboration: float | None = None
    gate_route: str | None = None               # "auto_fix" | "human_review"
    gate_held_checks: list = field(default_factory=list)
    # Suite selection scores from embedding similarity
    suite_selection_scores: dict = field(default_factory=dict)
    # GCS DOM snapshot path
    dom_snapshot_gcs_path: str | None = None
    # Latency breakdown (ms)
    triage_ttft_ms: int | None = None
    triage_total_ms: int | None = None
    nli_latency_ms: int | None = None
    embedding_latency_ms: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value if isinstance(self.status, RunStatus) else self.status,
            "trigger_commit": self.trigger_commit,
            "trigger_branch": self.trigger_branch,
            "team_id": self.team_id,
            "suites_run": self.suites_run,
            "failures": self.failures,
            "dom_report": self.dom_report,
            "evidence": self.evidence,
            "triage_result": {
                "classification": self.triage.classification,
                "confidence": self.triage.confidence,
                "evidence": self.triage.evidence,
                "proposed_fix": self.triage.proposed_fix,
                "p_class": self.triage.p_class,
                "logprob_margin": self.triage.logprob_margin,
                "fix_grounded": self.triage.fix_grounded,
                "dom_corroboration": self.triage.dom_corroboration,
            } if self.triage.classification else None,
            "node_states": {
                k: {"node": k, "state": v.state, "annotation": v.annotation}
                for k, v in self.node_states.items()
            },
            "approved_by": self.approved_by,
            "pr_url": self.pr_url,
            "langfuse_trace_id": self.langfuse_trace_id,
            "langfuse_trace_url": self.langfuse_trace_url,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "node_timings": self.node_timings,
            "human_override": self.human_override,
            "override_reason": self.override_reason,
            "consecutive_failures": self.consecutive_failures,
            "hitl_triggers": self.hitl_triggers,
            "force_hitl": self.force_hitl,  # derived property — kept for API compatibility
            "suite_selection_method": self.suite_selection_method,
            "report_text": self.report_text,
            "p_class": self.p_class,
            "logprob_margin": self.logprob_margin,
            "nli_entailment": self.nli_entailment,
            "fix_grounded": self.fix_grounded,
            "dom_corroboration": self.dom_corroboration,
            "gate_route": self.gate_route,
            "gate_held_checks": self.gate_held_checks,
            "suite_selection_scores": self.suite_selection_scores,
            "dom_snapshot_gcs_path": self.dom_snapshot_gcs_path,
            "triage_ttft_ms": self.triage_ttft_ms,
            "triage_total_ms": self.triage_total_ms,
            "nli_latency_ms": self.nli_latency_ms,
            "embedding_latency_ms": self.embedding_latency_ms,
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
            team_id=d.get("team_id", "core-platform"),
            suites_run=d.get("suites_run", []),
            failures=d.get("failures", []),
            dom_report=d.get("dom_report", {}),
            evidence=d.get("evidence", {}),
            triage=TriageResult(
                classification=triage_data.get("classification", ""),
                confidence=triage_data.get("confidence", 0.0),
                evidence=triage_data.get("evidence", ""),
                proposed_fix=triage_data.get("proposed_fix"),
                p_class=triage_data.get("p_class", 0.0),
                logprob_margin=triage_data.get("logprob_margin", 0.0),
                fix_grounded=triage_data.get("fix_grounded"),
                dom_corroboration=triage_data.get("dom_corroboration", 0.0),
            ),
            node_states=node_states,
            approved_by=d.get("approved_by"),
            pr_url=d.get("pr_url"),
            langfuse_trace_id=d.get("langfuse_trace_id"),
            langfuse_trace_url=d.get("langfuse_trace_url"),
            input_tokens=d.get("input_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            cost_usd=d.get("cost_usd", 0.0),
            node_timings=d.get("node_timings", {}),
            human_override=d.get("human_override", False),
            override_reason=d.get("override_reason"),
            consecutive_failures=d.get("consecutive_failures", 0),
            hitl_triggers=d.get("hitl_triggers", []),
            suite_selection_method=d.get("suite_selection_method", "fallback_all"),
            report_text=d.get("report_text"),
            p_class=d.get("p_class"),
            logprob_margin=d.get("logprob_margin"),
            nli_entailment=d.get("nli_entailment"),
            fix_grounded=d.get("fix_grounded"),
            dom_corroboration=d.get("dom_corroboration"),
            gate_route=d.get("gate_route"),
            gate_held_checks=d.get("gate_held_checks", []),
            suite_selection_scores=d.get("suite_selection_scores", {}),
            dom_snapshot_gcs_path=d.get("dom_snapshot_gcs_path"),
            triage_ttft_ms=d.get("triage_ttft_ms"),
            triage_total_ms=d.get("triage_total_ms"),
            nli_latency_ms=d.get("nli_latency_ms"),
            embedding_latency_ms=d.get("embedding_latency_ms"),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.utcnow(),
            updated_at=datetime.fromisoformat(d["updated_at"]) if d.get("updated_at") else datetime.utcnow(),
        )
