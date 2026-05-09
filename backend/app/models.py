from pydantic import BaseModel, Field
from typing import Literal, Any
from datetime import datetime
import uuid


RunStatus = Literal[
    "planning", "running", "triaging", "awaiting_human",
    "healing", "complete", "failed"
]

Classification = Literal["drift", "bug", "env"]

NodeState = Literal["idle", "running", "success", "failed", "skipped", "waiting"]


class NodeUpdate(BaseModel):
    node: str
    state: NodeState
    annotation: str = ""


class TriageResult(BaseModel):
    classification: Classification
    confidence: float
    evidence: str
    proposed_fix: dict[str, Any] | None = None


class QARun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: RunStatus = "planning"
    trigger_commit: str = ""
    trigger_branch: str = "main"
    suites_run: list[str] = []
    failures: list[dict] = []
    triage_result: TriageResult | None = None
    node_states: dict[str, NodeUpdate] = {}
    approved_by: str | None = None
    commit_sha: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "trigger_commit": self.trigger_commit,
            "trigger_branch": self.trigger_branch,
            "suites_run": self.suites_run,
            "failures": self.failures,
            "triage_result": self.triage_result.model_dump() if self.triage_result else None,
            "node_states": {k: v.model_dump() for k, v in self.node_states.items()},
            "approved_by": self.approved_by,
            "commit_sha": self.commit_sha,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class WebhookPayload(BaseModel):
    ref: str = "refs/heads/main"
    after: str = ""
    commits: list[dict] = []
    repository: dict = {}


class ApprovalRequest(BaseModel):
    approved: bool
    reviewer_name: str
