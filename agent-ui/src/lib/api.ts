import type { QARun, GitCommit, Ticket, Notification, AuthUser, MetricsSummary, CircuitBreakerStatus } from "./types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Auth helpers ──────────────────────────────────────────────────────────────

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("rait_access_token");
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function login(email: string, password: string): Promise<{ access_token: string; user: AuthUser }> {
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error("Invalid credentials");
  const data = await res.json();
  if (typeof window !== "undefined") {
    localStorage.setItem("rait_access_token", data.access_token);
    localStorage.setItem("rait_user", JSON.stringify(data.user));
  }
  return data;
}

export async function logout(): Promise<void> {
  await fetch(`${API}/auth/logout`, { method: "POST", headers: authHeaders() });
  if (typeof window !== "undefined") {
    localStorage.removeItem("rait_access_token");
    localStorage.removeItem("rait_user");
  }
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem("rait_user");
  return raw ? JSON.parse(raw) : null;
}

export async function fetchDemoUsers(): Promise<Array<{ email: string; password: string; role: string; full_name: string }>> {
  try {
    const res = await fetch(`${API}/auth/demo-users`);
    if (!res.ok) return [];
    return res.json();
  } catch { return []; }
}

// ── Runs ──────────────────────────────────────────────────────────────────────

export async function fetchRuns(): Promise<QARun[]> {
  try {
    const res = await fetch(`${API}/runs`, { headers: authHeaders() });
    if (!res.ok) return [];
    return res.json();
  } catch { return []; }
}

export async function fetchRun(runId: string): Promise<QARun> {
  const res = await fetch(`${API}/runs/${runId}`, { headers: authHeaders() });
  if (!res.ok) throw new Error("Run not found");
  return res.json();
}

export async function submitApproval(
  runId: string,
  approved: boolean,
  reviewerName: string
): Promise<void> {
  const res = await fetch(`${API}/approve/${runId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ approved, reviewer_name: reviewerName }),
  });
  if (!res.ok) throw new Error("Approval failed");
}

export async function triggerManualRun(
  scenario: string = "flow1"
): Promise<{ run_id: string }> {
  const params = new URLSearchParams({ scenario, commit_sha: "manual" });
  const res = await fetch(`${API}/webhook/manual?${params}`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Trigger failed");
  return res.json();
}

export async function injectDrift(flow: string, hitl = false): Promise<{ run_id: string; description: string }> {
  const params = new URLSearchParams({ flow, ...(hitl ? { hitl: "true" } : {}) });
  const res = await fetch(`${API}/demo/inject-drift?${params}`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Drift injection failed");
  return res.json();
}

export async function resetDemo(): Promise<void> {
  await fetch(`${API}/demo/reset`, { method: "POST", headers: authHeaders() });
}

export function createRunStream(
  runId: string,
  onData: (run: QARun) => void,
  onDone: () => void
): () => void {
  const token = getToken();
  const url = `${API}/runs/${runId}/stream${token ? `?token=${token}` : ""}`;
  const source = new EventSource(url);
  source.onmessage = (e) => {
    const run: QARun = JSON.parse(e.data);
    onData(run);
    if (["complete", "failed", "quarantined"].includes(run.status)) {
      source.close();
      onDone();
    }
  };
  source.onerror = () => { source.close(); onDone(); };
  return () => source.close();
}

// ── Git ───────────────────────────────────────────────────────────────────────

export async function fetchGitLog(): Promise<GitCommit[]> {
  try {
    const res = await fetch(`${API}/git/log`, { headers: authHeaders() });
    if (!res.ok) return [];
    return res.json();
  } catch { return []; }
}

// ── Tickets ───────────────────────────────────────────────────────────────────

export async function fetchTickets(): Promise<Ticket[]> {
  try {
    const res = await fetch(`${API}/tickets`, { headers: authHeaders() });
    if (!res.ok) return [];
    return res.json();
  } catch { return []; }
}

// ── Notifications ─────────────────────────────────────────────────────────────

export async function fetchNotifications(): Promise<Notification[]> {
  try {
    const res = await fetch(`${API}/notifications`, { headers: authHeaders() });
    if (!res.ok) return [];
    return res.json();
  } catch { return []; }
}

// ── Metrics ───────────────────────────────────────────────────────────────────

export async function fetchMetricsSummary(): Promise<MetricsSummary> {
  try {
    const res = await fetch(`${API}/metrics/summary`, { headers: authHeaders() });
    if (!res.ok) throw new Error("Metrics unavailable");
    return res.json();
  } catch {
    return { total_runs: 0, success_runs: 0, success_rate: 0, avg_cost_usd: 0, total_cost_usd: 0, avg_confidence: 0 };
  }
}

export async function fetchCircuitBreakers(): Promise<CircuitBreakerStatus> {
  try {
    const res = await fetch(`${API}/metrics/circuit_breakers`, { headers: authHeaders() });
    if (!res.ok) throw new Error();
    return res.json();
  } catch {
    return { effective_threshold: 0.80, base_threshold: 0.80, threshold_overridden: false, recent_events: [] };
  }
}

export async function fetchClassifications(): Promise<Array<{ classification: string; count: number }>> {
  try {
    const res = await fetch(`${API}/metrics/classifications`, { headers: authHeaders() });
    if (!res.ok) return [];
    return res.json();
  } catch { return []; }
}

// ── Scheduler ─────────────────────────────────────────────────────────────────

export async function triggerNightlyRun(): Promise<void> {
  await fetch(`${API}/scheduler/trigger-nightly`, { method: "POST", headers: authHeaders() });
}

export async function rebuildIndex(): Promise<{ entries: number }> {
  const res = await fetch(`${API}/scheduler/rebuild-index`, { method: "POST", headers: authHeaders() });
  return res.json();
}

export async function resetCircuitBreakers(): Promise<{ status: string; effective_threshold: number }> {
  const res = await fetch(`${API}/admin/reset-circuit-breakers`, { method: "POST", headers: authHeaders() });
  return res.json();
}
