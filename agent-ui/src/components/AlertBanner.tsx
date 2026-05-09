"use client";

import { useState } from "react";
import { clsx } from "clsx";
import type { QARun } from "@/lib/types";

export function AlertBanner({
  run,
  onViewTicket,
}: {
  run: QARun | null;
  onViewTicket: () => void;
}) {
  const [dismissed, setDismissed] = useState(false);

  const isBug =
    run?.status === "failed" &&
    run?.triage_result?.classification === "bug";

  if (!isBug || dismissed) return null;

  const confidence = run.triage_result?.confidence ?? 0;
  const severity = confidence >= 0.9 ? "HIGH" : confidence >= 0.7 ? "MEDIUM" : "LOW";
  const severityColor =
    severity === "HIGH"   ? "text-red" :
    severity === "MEDIUM" ? "text-yellow" :
    "text-cream/60";

  return (
    <div
      className={clsx(
        "flex items-center gap-4 px-5 py-3 border-b border-red/20 bg-red/5",
        "animate-fade-in"
      )}
    >
      <span className="w-2 h-2 rounded-full bg-red shrink-0" />

      <div className="flex-1 text-[13px]">
        <span className={clsx("font-bold mr-2", severityColor)}>
          {severity}
        </span>
        <span className="text-cream/70">
          Bug detected in run{" "}
          <span className="font-mono text-cream/90">
            {run.id.slice(0, 7)}
          </span>{" "}
          — {run.triage_result?.evidence ?? "see report for details"}
        </span>
      </div>

      <div className="flex items-center gap-3 shrink-0">
        <button
          onClick={onViewTicket}
          className="text-[12px] text-blue hover:text-blue/80 font-medium underline-offset-2 hover:underline"
        >
          View Ticket →
        </button>
        <button
          onClick={() => setDismissed(true)}
          className="text-cream/25 hover:text-cream/50 text-lg leading-none"
          aria-label="Dismiss"
        >
          ×
        </button>
      </div>
    </div>
  );
}
