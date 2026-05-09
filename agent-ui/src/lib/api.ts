import type { QARun, GitCommit } from "./types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchRuns(): Promise<QARun[]> {
  const res = await fetch(`${API}/runs`);
  if (!res.ok) throw new Error("Failed to fetch runs");
  return res.json();
}

export async function fetchRun(runId: string): Promise<QARun> {
  const res = await fetch(`${API}/runs/${runId}`);
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, reviewer_name: reviewerName }),
  });
  if (!res.ok) throw new Error("Approval failed");
}

export async function triggerManualRun(
  changedFiles: string[] = ["checkout.html"]
): Promise<{ run_id: string }> {
  const params = new URLSearchParams();
  changedFiles.forEach((f) => params.append("changed_files", f));
  params.set("commit_sha", "manual");
  const res = await fetch(`${API}/webhook/manual?${params}`, { method: "POST" });
  if (!res.ok) throw new Error("Trigger failed");
  return res.json();
}

export async function fetchGitLog(): Promise<GitCommit[]> {
  try {
    const res = await fetch(`${API}/git/log`);
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export function createRunStream(
  runId: string,
  onData: (run: QARun) => void,
  onDone: () => void
): () => void {
  const source = new EventSource(`${API}/runs/${runId}/stream`);
  source.onmessage = (e) => {
    const run: QARun = JSON.parse(e.data);
    onData(run);
    if (run.status === "complete" || run.status === "failed") {
      source.close();
      onDone();
    }
  };
  source.onerror = () => { source.close(); onDone(); };
  return () => source.close();
}
