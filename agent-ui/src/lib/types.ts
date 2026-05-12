export type NodeState = "idle" | "running" | "success" | "failed" | "skipped" | "waiting";
export type RunStatus = "planning" | "running" | "inspecting" | "triaging" | "awaiting_human" | "healing" | "complete" | "failed" | "quarantined";
export type Classification = "drift" | "bug" | "env" | "none" | "";
export type UserRole = "super_admin" | "qa_manager" | "qa_engineer" | "developer" | "system_agent";

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
  team_id: string;
  suites_run: string[];
  failures: Array<{ test: string; raw: string; selector?: string }>;
  triage_result: TriageResult | null;
  node_states: Record<string, NodeUpdate>;
  approved_by: string | null;
  pr_url: string | null;
  commit_sha: string | null;
  force_hitl: boolean;
  langfuse_trace_id: string | null;
  langfuse_trace_url: string | null;
  suite_selection_method: string;
  report_text: string | null;
  evidence: {
    recent_commits?: Array<{ sha: string; message: string; changed_files: string[]; hours_ago: number }>;
    dom_report?: { changed_selectors?: Array<{ old: string; found: string; confidence: number; match_reason: string }> };
    test_history?: { consecutive_failures: number; last_5_results: string[] };
  } | null;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  node_timings: Record<string, number>;
  created_at: string;
  updated_at: string;
}

export interface GitCommit {
  sha: string;
  message: string;
  author: string;
  ago: string;
}

export interface Ticket {
  id: string;
  key: string;
  ticket_type: string;
  classification: string | null;
  severity: string;
  status: string;
  title: string;
  body: string;
  run_id: string | null;
  team_id: string | null;
  jira_remote_id: string | null;
  jira_url: string | null;
  created_at: string | null;
}

export interface Notification {
  id: string;
  channel: string;
  event_type: string;
  title: string;
  message: string;
  status: string;
  run_id: string | null;
  created_at: string | null;
}

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  team_id: string;
}

export interface MetricsSummary {
  total_runs: number;
  success_runs: number;
  success_rate: number;
  avg_cost_usd: number;
  total_cost_usd: number;
  avg_confidence: number;
}

export interface CircuitBreakerStatus {
  effective_threshold: number;
  base_threshold: number;
  threshold_overridden: boolean;
  recent_events: Array<{
    id: string;
    event_type: string;
    severity: string;
    message: string;
    created_at: string;
  }>;
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

export const ROLE_LABELS: Record<UserRole, string> = {
  super_admin:  "Super Admin",
  qa_manager:   "QA Manager",
  qa_engineer:  "QA Engineer",
  developer:    "Developer",
  system_agent: "System Agent",
};
