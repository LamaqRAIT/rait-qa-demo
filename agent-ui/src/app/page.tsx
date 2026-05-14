"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { clsx } from "clsx";
import type { QARun, Notification, AuthUser } from "@/lib/types";
import {
  fetchRuns, createRunStream, injectDrift, resetDemo,
  fetchNotifications, fetchMetricsSummary, fetchCircuitBreakers,
  fetchTickets, logout, getStoredUser, triggerNightlyRun, resetCircuitBreakers,
} from "@/lib/api";
import { NodeGraph } from "@/components/NodeGraph";
import { TicketList } from "@/components/TicketList";
import { ApprovalQueue } from "@/components/ApprovalQueue";
import { AlertBanner } from "@/components/AlertBanner";
import { RunReportDrawer } from "@/components/RunReportDrawer";
import { GitLogPanel } from "@/components/GitLogPanel";
import { MetricsPanel } from "@/components/MetricsPanel";
import { TicketsPanel } from "@/components/TicketsPanel";
import { NotificationBell } from "@/components/NotificationBell";

type Tab = "runs" | "approvals" | "tickets" | "metrics";

const FLOWS = [
  { label: "Flow 1 — Selector drift",    flow: "flow1", desc: "checkout btn class rename" },
  { label: "Flow 2 — Text drift",        flow: "flow2", desc: "checkout btn text change" },
  { label: "Flow 3 — Login bug",         flow: "flow3", desc: "redirect destination bug" },
  { label: "Flow 4 — Cart drift",        flow: "flow4", desc: "cart btn class rename" },
  { label: "Flow 5 — Search drift",      flow: "flow5", desc: "search input ID rename (HITL)" },
  { label: "Flow 6 — Register drift",    flow: "flow6", desc: "reg email field ID rename" },
];

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [runs, setRuns]           = useState<QARun[]>([]);
  const [activeRun, setActiveRun] = useState<QARun | null>(null);
  const [drawerRun, setDrawerRun] = useState<QARun | null>(null);
  const [tab, setTab]             = useState<Tab>("runs");
  const [triggering, setTriggering] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const streamCleanup = useRef<(() => void) | null>(null);

  // Auth check
  useEffect(() => {
    const stored = getStoredUser();
    if (!stored) { router.push("/login"); return; }
    setUser(stored);
  }, [router]);

  // Load runs + notifications
  const loadRuns = useCallback(async () => {
    const data = await fetchRuns();
    setRuns(data);
    if (!activeRun && data.length > 0) setActiveRun(data[0]);
  }, [activeRun]);

  const loadNotifications = useCallback(async () => {
    const n = await fetchNotifications();
    setNotifications(n);
  }, []);

  useEffect(() => {
    loadRuns();
    loadNotifications();
    const ri = setInterval(loadRuns, 5000);
    const ni = setInterval(loadNotifications, 10000);
    return () => { clearInterval(ri); clearInterval(ni); };
  }, [loadRuns, loadNotifications]);

  // Stream active run
  const streamRun = useCallback((run: QARun) => {
    if (streamCleanup.current) streamCleanup.current();
    if (["complete", "failed", "quarantined"].includes(run.status)) { setActiveRun(run); return; }
    const stop = createRunStream(
      run.id,
      (updated) => {
        setActiveRun(updated);
        setRuns((prev) => prev.map((r) => r.id === updated.id ? updated : r));
      },
      () => { loadRuns(); loadNotifications(); }
    );
    streamCleanup.current = stop;
  }, [loadRuns, loadNotifications]);

  const handleSelectRun = (run: QARun) => {
    setActiveRun(run);
    streamRun(run);
    setDrawerRun(null);
  };

  // Drift injection
  async function handleInjectDrift(flow: string, hitl = false) {
    setTriggering(true);
    try {
      const { run_id } = await injectDrift(flow, hitl);
      await loadRuns();
      const data = await fetchRuns();
      const newRun = data.find((r: QARun) => r.id === run_id);
      if (newRun) { handleSelectRun(newRun); setTab("runs"); }
    } finally {
      setTriggering(false);
    }
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  const pendingApproval = runs.find((r) => r.status === "awaiting_human") ?? null;
  const canApprove = user && ["super_admin", "qa_manager", "qa_engineer"].includes(user.role);

  if (!user) return null;

  return (
    <div className="flex flex-col min-h-screen">
      <AlertBanner run={activeRun} onViewTicket={() => { setDrawerRun(activeRun); }} />

      {/* Top bar */}
      <header className="flex items-center justify-between px-5 h-13 border-b border-cream/8 bg-surface shrink-0">
        <div className="flex items-center gap-3">
          <span className="w-2 h-2 rounded-full bg-green" />
          <span className="text-[14px] font-semibold tracking-tight">RAIT QA Agent</span>
          <span className="text-cream/20 mx-1">|</span>
          <span className="text-[11px] text-cream/35 font-mono">Demo Dashboard</span>
        </div>
        <div className="flex items-center gap-3">
          <NotificationBell notifications={notifications} />
          <div className="flex items-center gap-2 pl-3 border-l border-cream/8">
            <div className="text-right">
              <p className="text-[12px] font-medium text-cream/80">{user.full_name}</p>
              <p className="text-[10px] text-cream/35 uppercase tracking-wide">{user.role.replace("_", " ")}</p>
            </div>
            <button
              onClick={handleLogout}
              className="text-[11px] text-cream/30 hover:text-cream/60 transition ml-1"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-72 border-r border-cream/8 bg-elevated shrink-0 flex flex-col overflow-y-auto">
          {/* Demo flows */}
          <div className="p-4 border-b border-cream/8">
            <p className="text-[10px] text-cream/25 uppercase tracking-widest font-semibold mb-2.5">
              Trigger Demo Flow
            </p>
            <div className="space-y-1.5">
              {FLOWS.map(({ label, flow, desc }) => (
                <button
                  key={flow}
                  onClick={() => handleInjectDrift(flow)}
                  disabled={triggering}
                  title={desc}
                  className={clsx(
                    "w-full text-left px-3 py-2 rounded-lg text-[12px] transition",
                    "border border-cream/8 bg-card hover:bg-surface text-cream/55",
                    "hover:text-cream/85 hover:border-cream/15 disabled:opacity-40"
                  )}
                >
                  {triggering ? "Starting…" : label}
                </button>
              ))}
              <div className="border-t border-cream/8 pt-2 mt-1 space-y-1.5">
                <button
                  onClick={() => handleInjectDrift("flow1", true)}
                  disabled={triggering}
                  className={clsx(
                    "w-full text-left px-3 py-2 rounded-lg text-[12px] transition",
                    "border border-yellow/20 bg-yellow/5 text-yellow/65",
                    "hover:bg-yellow/10 hover:text-yellow hover:border-yellow/35 disabled:opacity-40"
                  )}
                >
                  {triggering ? "Starting…" : "⊙ Force HITL Approval"}
                </button>
                <button
                  onClick={async () => { await triggerNightlyRun(); await loadRuns(); }}
                  className={clsx(
                    "w-full text-left px-3 py-2 rounded-lg text-[12px] transition",
                    "border border-blue/15 bg-blue/5 text-blue/60",
                    "hover:bg-blue/10 hover:text-blue hover:border-blue/30"
                  )}
                >
                  ⏱ Trigger Nightly Run
                </button>
                <button
                  onClick={async () => { await resetCircuitBreakers(); await loadRuns(); }}
                  className={clsx(
                    "w-full text-left px-3 py-2 rounded-lg text-[12px] transition",
                    "border border-red/12 bg-red/5 text-red/50",
                    "hover:bg-red/10 hover:text-red/70 hover:border-red/25"
                  )}
                >
                  ⚡ Reset Circuit Breakers
                </button>
                <button
                  onClick={() => resetDemo()}
                  className={clsx(
                    "w-full text-left px-3 py-2 rounded-lg text-[12px] transition",
                    "border border-cream/8 bg-card hover:bg-surface text-cream/35",
                    "hover:text-cream/60 hover:border-cream/15"
                  )}
                >
                  Reset Demo Site
                </button>
              </div>
            </div>
          </div>

          {/* Active run info */}
          {activeRun && (
            <div className="p-4 border-b border-cream/8">
              <p className="text-[10px] text-cream/25 uppercase tracking-widest font-semibold mb-2">
                Active Run
              </p>
              <div className="space-y-1 text-[11px]">
                {[
                  ["ID", <span key="id" className="font-mono text-blue/70">{activeRun.id.slice(0, 8)}</span>],
                  ["Status", activeRun.status.replace(/_/g, " ")],
                  ["Branch", <span key="br" className="font-mono text-cream/50">{activeRun.trigger_branch}</span>],
                  ["Suites", activeRun.suites_run.length > 0 ? activeRun.suites_run.length + " selected" : "all"],
                  ["Method", <span key="m" className="text-cream/40">{activeRun.suite_selection_method}</span>],
                  activeRun.cost_usd > 0 ? ["Cost", `$${activeRun.cost_usd.toFixed(4)}`] : null,
                ].filter(Boolean).map((row, i) => (
                  <div key={i} className="flex justify-between gap-2">
                    <span className="text-cream/30">{(row as [string, React.ReactNode])[0]}</span>
                    <span className="text-cream/65 text-right">{(row as [string, React.ReactNode])[1]}</span>
                  </div>
                ))}
                {activeRun.pr_url && (
                  <a href={activeRun.pr_url} target="_blank" rel="noreferrer"
                    className="block mt-2 pt-2 border-t border-cream/8 text-blue/70 hover:text-blue text-[11px]">
                    View PR on GitHub →
                  </a>
                )}
                {activeRun.langfuse_trace_url && (
                  <a href={activeRun.langfuse_trace_url} target="_blank" rel="noreferrer"
                    className="block text-cream/35 hover:text-cream/60 text-[11px]">
                    View LLM Trace →
                  </a>
                )}
              </div>
            </div>
          )}

          {/* Git log */}
          <div className="p-4 flex-1">
            <GitLogPanel latestCommitSha={activeRun?.trigger_commit} />
          </div>
        </aside>

        {/* Main */}
        <main className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* Pipeline graph */}
          <section>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-[12px] font-semibold text-cream/40 uppercase tracking-widest">Pipeline</h2>
              {activeRun?.triage_result && (
                <div className="flex items-center gap-3 text-[11px]">
                  <span className={clsx(
                    "px-2 py-0.5 rounded-full font-semibold uppercase text-[10px]",
                    activeRun.triage_result.classification === "drift" && "bg-blue/15 text-blue",
                    activeRun.triage_result.classification === "bug"   && "bg-red/15 text-red",
                    activeRun.triage_result.classification === "env"   && "bg-yellow/15 text-yellow",
                  )}>
                    {activeRun.triage_result.classification}
                  </span>
                  <span className="text-cream/30 font-mono">
                    {(activeRun.triage_result.confidence * 100).toFixed(0)}% conf
                  </span>
                </div>
              )}
            </div>
            <NodeGraph run={activeRun} />
          </section>

          {/* Tabs */}
          <section>
            <div className="flex items-center gap-0.5 border-b border-cream/8 mb-4">
              {(["runs", "approvals", "tickets", "metrics"] as Tab[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={clsx(
                    "px-4 py-2 text-[12px] font-medium border-b-2 -mb-px transition-colors capitalize",
                    tab === t
                      ? "border-blue text-blue"
                      : "border-transparent text-cream/35 hover:text-cream/65"
                  )}
                >
                  {t === "approvals" ? (
                    <span className="flex items-center gap-1.5">
                      Approvals
                      {pendingApproval && canApprove && (
                        <span className="w-1.5 h-1.5 rounded-full bg-yellow animate-pulse" />
                      )}
                    </span>
                  ) : t === "runs" ? "Run Log" : t.charAt(0).toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>

            {tab === "runs" && (
              <TicketList
                runs={runs}
                activeRunId={activeRun?.id ?? null}
                onSelect={(run) => { handleSelectRun(run); setDrawerRun(run); }}
              />
            )}
            {tab === "approvals" && (
              <ApprovalQueue
                run={pendingApproval}
                onResolved={() => { loadRuns(); setTab("runs"); }}
              />
            )}
            {tab === "tickets" && <TicketsPanel />}
            {tab === "metrics" && <MetricsPanel />}
          </section>
        </main>
      </div>

      <RunReportDrawer run={drawerRun} onClose={() => setDrawerRun(null)} />
    </div>
  );
}
