"use client";

import { clsx } from "clsx";
import type { QARun, Classification } from "@/lib/types";
import { Badge } from "./Badge";

function classificationVariant(c: Classification) {
  if (c === "bug") return "error";
  if (c === "drift") return "active";
  if (c === "env") return "warning";
  return "muted";
}

function statusVariant(s: string) {
  if (s === "complete") return "success";
  if (s === "failed")   return "error";
  if (s === "awaiting_human") return "warning";
  return "muted";
}

function statusLabel(run: QARun): string {
  const cls = run.triage_result?.classification ?? "";
  const st  = run.status;
  if (st === "complete" && run.approved_by === "system") return "RESOLVED";
  if (st === "complete" && run.approved_by) return "APPROVED";
  if (st === "failed" && cls === "bug") return "BUG FILED";
  if (st === "failed") return "FAILED";
  if (st === "awaiting_human") return "PENDING REVIEW";
  return st.toUpperCase().replace("_", " ");
}

export function TicketList({
  runs,
  activeRunId,
  onSelect,
}: {
  runs: QARun[];
  activeRunId: string | null;
  onSelect: (run: QARun) => void;
}) {
  if (runs.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-cream/30 text-sm">
        No runs yet — trigger one to get started.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-cream/8 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-cream/8 bg-elevated">
            {["Run", "Type", "Status", "Page", "Time"].map((h) => (
              <th
                key={h}
                className="px-4 py-2 text-left text-[11px] font-semibold uppercase tracking-widest text-cream/30"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const cls = run.triage_result?.classification ?? "";
            const isActive = run.id === activeRunId;
            return (
              <tr
                key={run.id}
                onClick={() => onSelect(run)}
                className={clsx(
                  "border-b border-cream/5 cursor-pointer transition-colors",
                  isActive ? "bg-blue/8 border-l-2 border-l-blue" : "hover:bg-surface"
                )}
              >
                <td className="px-4 py-3 font-mono text-[12px] text-cream/60">
                  {run.trigger_commit.slice(0, 7) || run.id.slice(0, 7)}
                </td>
                <td className="px-4 py-3">
                  {cls ? (
                    <Badge variant={classificationVariant(cls)}>
                      {cls.toUpperCase()}
                    </Badge>
                  ) : (
                    <Badge variant="muted">—</Badge>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Badge variant={statusVariant(run.status)}>
                    {statusLabel(run)}
                  </Badge>
                </td>
                <td className="px-4 py-3 text-[12px] text-cream/50">
                  {run.suites_run[0]?.replace("test_", "").replace(".py", "") || "—"}
                </td>
                <td className="px-4 py-3 text-[12px] text-cream/30">
                  {new Date(run.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
