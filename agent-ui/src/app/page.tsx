"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { clsx } from "clsx";
import type { QARun } from "@/lib/types";
import { fetchRuns, createRunStream, injectDrift, resetDemo } from "@/lib/api";
import { NodeGraph } from "@/components/NodeGraph";
import { TicketList } from "@/components/TicketList";
import { ApprovalQueue } from "@/components/ApprovalQueue";
import { AlertBanner } from "@/components/AlertBanner";
import { RunReportDrawer } from "@/components/RunReportDrawer";
import { GitLogPanel } from "@/components/GitLogPanel";

type Tab = "runs" | "approvals";

export default function Dashboard() {
  const [runs, setRuns]           = useState<QARun[]>([]);
  const [activeRun, setActiveRun] = useState<QARun | null>(null);
  const [drawerRun, setDrawerRun] = useState<QARun | null>(null);
  const [tab, setTab]             = useState<Tab>("runs");
  const [triggering, setTriggering] = useState(false);
  const streamCleanup = useRef<(() => void) | null>(null);

  // ── Load runs on mount + poll ────────────────────────────────────────────
  const loadRuns = useCallback(async () => {
    const data = await fetchRuns();
    setRuns(data);
    // If no active run, auto-select the most recent one
    if (!activeRun && data.length > 0) {
      setActiveRun(data[0]);
    }
  }, [activeRun]);

  useEffect(() => {
    loadRuns();
    const interval = setInterval(loadRuns, 5000);
    return () => clearInterval(interval);
  }, [loadRuns]);

  // ── Stream selected run ──────────────────────────────────────────────────
  const streamRun = useCallback((run: QARun) => {
    if (streamCleanup.current) streamCleanup.current();
    const terminal = ["complete", "failed"];
    if (terminal.includes(run.status)) {
      setActiveRun(run);
      return;
    }
    const stop = createRunStream(
      run.id,
      (updated) => {
        setActiveRun(updated);
        setRuns((prev) => prev.map((r) => r.id === updated.id ? updated : r));
      },
      () => { loadRuns(); }
    );
    streamCleanup.current = stop;
  }, [loadRuns]);

  const handleSelectRun = (run: QARun) => {
    setActiveRun(run);
    streamRun(run);
    setDrawerRun(null);
  };

  // ── Demo drift injection ───────────────────────────────────────────────
  async function handleInjectDrift(flow: string, hitl = false) {
    setTriggering(true);
    try {
      const { run_id } = await injectDrift(flow, hitl);
      await loadRuns();
      const newRun = (await fetchRuns()).find((r: QARun) => r.id === run_id);
      if (newRun) {
        handleSelectRun(newRun);
        setTab("runs");
      }
    } finally {
      setTriggering(false);
    }
  }

  async function handleReset() {
    await resetDemo();
  }

  const pendingApproval = runs.find((r) => r.status === "awaiting_human") ?? null;

  return (
    <div className="flex flex-col min-h-screen">
      {/* Alert Banner */}
      <AlertBanner
        run={activeRun}
        onViewTicket={() => {
          setDrawerRun(activeRun);
          setTab("runs");
        }}
      />

      {/* Top bar */}
      <header className="flex items-center justify-between px-6 h-14 border-b border-cream/8 bg-surface shrink-0">
        <div className="flex items-center gap-3">
          <span className="w-2 h-2 rounded-full bg-green" />
          <span className="text-[15px] font-semibold tracking-tight">RAIT QA Agent</span>
          <span className="text-cream/25 text-[12px] mx-1">|</span>
          <span className="text-[12px] text-cream/40 font-mono">Demo Dashboard</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[12px] text-cream/30">
            {runs.length} run{runs.length !== 1 ? "s" : ""}
          </span>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-64 border-r border-cream/8 bg-elevated shrink-0 flex flex-col overflow-y-auto">
          <div className="p-4 border-b border-cream/8">
            <p className="text-[11px] text-cream/30 uppercase tracking-widest font-semibold mb-3">
              Trigger Demo Flow
            </p>
            <div className="space-y-2">
              {[
                { label: "Flow 1 — Selector drift", flow: "flow1" },
                { label: "Flow 2 — Text drift", flow: "flow2" },
                { label: "Flow 3 — Login bug", flow: "flow3" },
              ].map(({ label, flow }) => (
                <button
                  key={flow}
                  onClick={() => handleInjectDrift(flow)}
                  disabled={triggering}
                  className={clsx(
                    "w-full text-left px-3 py-2 rounded-md text-[12px] transition",
                    "border border-cream/8 bg-card hover:bg-surface text-cream/60",
                    "hover:text-cream/90 hover:border-cream/15 disabled:opacity-40"
                  )}
                >
                  {triggering ? "Starting…" : label}
                </button>
              ))}
              <div className="border-t border-cream/8 my-2" />
              <p className="text-[10px] text-cream/25 uppercase tracking-widest mb-1">Human-in-the-loop</p>
              <button
                onClick={() => handleInjectDrift("flow1", true)}
                disabled={triggering}
                className={clsx(
                  "w-full text-left px-3 py-2 rounded-md text-[12px] transition",
                  "border border-yellow/25 bg-yellow/5 text-yellow/70",
                  "hover:bg-yellow/10 hover:text-yellow hover:border-yellow/40 disabled:opacity-40"
                )}
              >
                {triggering ? "Starting…" : "Flow 1 — Needs Approval ↗"}
              </button>
              <button
                onClick={handleReset}
                className={clsx(
                  "w-full text-left px-3 py-2 rounded-md text-[12px] transition",
                  "border border-cream/8 bg-card hover:bg-surface text-cream/40",
                  "hover:text-cream/70 hover:border-cream/15"
                )}
              >
                Reset Demo Site
              </button>
            </div>
          </div>

          <div className="p-4 flex-1">
            <p className="text-[11px] text-cream/30 uppercase tracking-widest font-semibold mb-3">
              Active Run
            </p>
            {activeRun ? (
              <div className="space-y-1.5 text-[12px]">
                <div className="flex justify-between">
                  <span className="text-cream/40">ID</span>
                  <span className="font-mono text-blue/80">{activeRun.id.slice(0, 8)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-cream/40">Status</span>
                  <span className="text-cream/70">{activeRun.status.replace("_", " ")}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-cream/40">Branch</span>
                  <span className="font-mono text-cream/60">{activeRun.trigger_branch}</span>
                </div>
                {activeRun.pr_url && (
                  <div className="mt-2 pt-2 border-t border-cream/8">
                    <a
                      href={activeRun.pr_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[12px] text-blue/80 hover:text-blue underline"
                    >
                      View PR on GitHub →
                    </a>
                  </div>
                )}
                {activeRun.langfuse_trace_id && (
                  <div className="mt-1">
                    <a
                      href={`https://us.cloud.langfuse.com/trace/${activeRun.langfuse_trace_id}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[12px] text-cream/40 hover:text-cream/70 underline"
                    >
                      View LLM Trace →
                    </a>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-[12px] text-cream/25">No active run</p>
            )}
          </div>

          {/* Git log */}
          <div className="p-4 border-t border-cream/8">
            <GitLogPanel />
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Node graph */}
          <section>
            <h2 className="text-[13px] font-semibold text-cream/50 uppercase tracking-widest mb-3">
              Pipeline
            </h2>
            <NodeGraph run={activeRun} />
          </section>

          {/* Tabs */}
          <section>
            <div className="flex items-center gap-1 border-b border-cream/8 mb-4">
              {(["runs", "approvals"] as Tab[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={clsx(
                    "px-4 py-2 text-[13px] font-medium border-b-2 -mb-px transition-colors",
                    tab === t
                      ? "border-blue text-blue"
                      : "border-transparent text-cream/40 hover:text-cream/70"
                  )}
                >
                  {t === "runs" ? "Ticket List" : (
                    <span className="flex items-center gap-1.5">
                      Approvals
                      {pendingApproval && (
                        <span className="w-1.5 h-1.5 rounded-full bg-yellow animate-pulse" />
                      )}
                    </span>
                  )}
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
          </section>
        </main>
      </div>

      {/* Run report drawer */}
      <RunReportDrawer run={drawerRun} onClose={() => setDrawerRun(null)} />
    </div>
  );
}
