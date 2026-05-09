"use client";

import { clsx } from "clsx";
import type { QARun } from "@/lib/types";
import { Badge } from "./Badge";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <p className="text-[11px] text-cream/35 uppercase tracking-widest mb-1">{label}</p>
      <div className="text-[13px] text-cream/80">{children}</div>
    </div>
  );
}

export function RunReportDrawer({
  run,
  onClose,
}: {
  run: QARun | null;
  onClose: () => void;
}) {
  if (!run) return null;

  const cls = run.triage_result?.classification ?? "";
  const fix = run.triage_result?.proposed_fix;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-charcoal/60 z-40 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Drawer */}
      <div
        className={clsx(
          "fixed right-0 top-0 h-full w-[440px] z-50",
          "bg-elevated border-l border-cream/8 overflow-y-auto",
          "animate-fade-in"
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-cream/8">
          <h2 className="text-[15px] font-semibold">Run Report</h2>
          <button
            onClick={onClose}
            className="text-cream/30 hover:text-cream/70 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="px-6 py-5 space-y-1">
          {/* Identity */}
          <Field label="Run ID">
            <span className="font-mono text-blue">{run.id}</span>
          </Field>

          <Field label="Commit">
            <span className="font-mono">{run.trigger_commit.slice(0, 12) || "manual"}</span>
            <span className="text-cream/30 ml-2">on {run.trigger_branch}</span>
          </Field>

          <Field label="Status">
            <Badge
              variant={
                run.status === "complete" ? "success" :
                run.status === "failed"   ? "error"   : "warning"
              }
            >
              {run.status.toUpperCase().replace("_", " ")}
            </Badge>
          </Field>

          <Field label="Timestamp">
            {new Date(run.created_at).toLocaleString()}
          </Field>

          {/* Suites */}
          {run.suites_run.length > 0 && (
            <Field label="Suites run">
              <ul className="space-y-1">
                {run.suites_run.map((s) => (
                  <li key={s} className="font-mono text-cream/60">• {s}</li>
                ))}
              </ul>
            </Field>
          )}

          {/* Failures */}
          {run.failures.length > 0 && (
            <Field label={`Failures (${run.failures.length})`}>
              <div className="rounded-md bg-bash border border-cream/8 p-3 space-y-1">
                {run.failures.map((f, i) => (
                  <p key={i} className="font-mono text-[12px] text-red/80 break-all">
                    {f.test || f.raw}
                  </p>
                ))}
              </div>
            </Field>
          )}

          {/* Triage */}
          {run.triage_result && (
            <>
              <Field label="Classification">
                <Badge
                  variant={
                    cls === "bug" ? "error" :
                    cls === "drift" ? "active" :
                    cls === "env" ? "warning" : "muted"
                  }
                >
                  {cls.toUpperCase()}
                </Badge>
                <span className="text-cream/40 text-[12px] ml-2 font-mono">
                  {(run.triage_result.confidence * 100).toFixed(0)}% confidence
                </span>
              </Field>

              <Field label="Evidence">
                <p className="text-cream/70 leading-relaxed">{run.triage_result.evidence}</p>
              </Field>

              {fix && (
                <Field label="Proposed fix">
                  <div className="rounded-md bg-bash border border-cream/8 font-mono text-[12px] overflow-hidden">
                    <div className="flex">
                      <span className="w-5 text-center text-red/70 bg-red/5 py-1.5">−</span>
                      <span className="px-3 py-1.5 text-red/70 break-all">{fix.old}</span>
                    </div>
                    <div className="flex border-t border-cream/5">
                      <span className="w-5 text-center text-green/70 bg-green/5 py-1.5">+</span>
                      <span className="px-3 py-1.5 text-green/70 break-all">{fix.new}</span>
                    </div>
                  </div>
                  <p className="text-cream/30 text-[11px] mt-1 font-mono">{fix.file}</p>
                </Field>
              )}
            </>
          )}

          {/* Resolution */}
          {run.approved_by && (
            <Field label="Approved by">
              <span className="text-green">{run.approved_by}</span>
            </Field>
          )}

          {run.commit_sha && (
            <Field label="Heal commit">
              <span className="font-mono text-blue">{run.commit_sha}</span>
            </Field>
          )}
        </div>
      </div>
    </>
  );
}
