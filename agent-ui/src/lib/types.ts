export type NodeState = "idle" | "running" | "success" | "failed" | "skipped" | "waiting";
export type RunStatus = "planning" | "running" | "triaging" | "awaiting_human" | "healing" | "complete" | "failed";
export type Classification = "drift" | "bug" | "env" | "none" | "";

export interface NodeUpdate {
  node: string;
  state: NodeState;
  annotation: string;
}

export interface TriageResult {
  classification: Classification;
  confidence: number;
  evidence: string;
  proposed_fix: { file: string; old: string; new: string } | null;
}

export interface QARun {
  id: string;
  status: RunStatus;
  trigger_commit: string;
  trigger_branch: string;
  suites_run: string[];
  failures: Array<{ test: string; raw: string }>;
  triage_result: TriageResult | null;
  node_states: Record<string, NodeUpdate>;
  approved_by: string | null;
  commit_sha: string | null;
  created_at: string;
  updated_at: string;
}

export interface GitCommit {
  sha: string;
  message: string;
  author: string;
  ago: string;
}

export const PIPELINE_NODES = [
  "git_watcher",
  "change_analyzer",
  "test_runner",
  "browser_inspector",
  "classifier",
  "human_review",
  "auto_fixer",
  "ticket_creator",
  "reporter",
] as const;

export const NODE_LABELS: Record<string, string> = {
  git_watcher:      "Git Watcher",
  change_analyzer:  "Change Analyzer",
  test_runner:      "Test Runner",
  browser_inspector:"Browser Inspector",
  classifier:       "Classifier",
  human_review:     "Human Review",
  auto_fixer:       "Auto-Fixer",
  ticket_creator:   "Ticket Creator",
  reporter:         "Reporter",
};
