"use client";

import { useState } from "react";
import { clsx } from "clsx";
import type { QARun } from "@/lib/types";
import { submitApproval } from "@/lib/api";

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.9 ? "#60DFB1" :
    value >= 0.8 ? "#60A4DF" :
    "#DFD660";

  return (
    <div>
      <div className="flex justify-between items-center mb-1">
        <span className="text-[11px] text-cream/40 uppercase tracking-wide">Confidence</span>
        <span className="text-[13px] font-mono font-semibold" style={{ color }}>
          {pct}%
        </span>
      </div>
      <div className="h-1 rounded-pill bg-cream/10 overflow-hidden">
        <div
          className="h-full rounded-pill transition-all"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
    </div>
  );
}

function SelectorDiff({ oldSel, newSel }: { oldSel: string; newSel: string }) {
  return (
    <div className="rounded-md overflow-hidden border border-cream/8 font-mono text-[12px]">
      <div className="flex items-start">
        <div className="w-6 bg-red/10 text-red flex items-center justify-center pt-2 text-[11px]">−</div>
        <div className="flex-1 bg-red/5 px-3 py-2 text-red/80 break-all">{oldSel}</div>
      </div>
      <div className="flex items-start border-t border-cream/5">
        <div className="w-6 bg-green/10 text-green flex items-center justify-center pt-2 text-[11px]">+</div>
        <div className="flex-1 bg-green/5 px-3 py-2 text-green/80 break-all">{newSel}</div>
      </div>
    </div>
  );
}

export function ApprovalQueue({
  run,
  onResolved,
}: {
  run: QARun | null;
  onResolved: () => void;
}) {
  const [reviewerName, setReviewerName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const isPending = run?.status === "awaiting_human";
  const fix = run?.triage_result?.proposed_fix;

  if (!isPending || !run) {
    return (
      <div className="rounded-lg border border-cream/8 bg-surface p-6 flex items-center justify-center">
        <p className="text-cream/30 text-sm">No pending approvals.</p>
      </div>
    );
  }

  async function handleAction(approved: boolean) {
    if (!run || !reviewerName.trim()) return;
    setSubmitting(true);
    try {
      await submitApproval(run.id, approved, reviewerName.trim());
      setSubmitted(true);
      setTimeout(onResolved, 1000);
    } catch (e) {
      console.error(e);
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <div className="rounded-lg border border-green/20 bg-green/5 p-6 text-center animate-fade-in">
        <p className="text-green font-semibold">Decision submitted — pipeline resuming…</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-yellow/20 bg-yellow/5 p-5 animate-fade-in">
      <div className="flex items-center gap-2 mb-4">
        <span className="w-2 h-2 rounded-full bg-yellow animate-pulse" />
        <h3 className="text-sm font-semibold text-yellow uppercase tracking-wide">
          Human Review Required
        </h3>
      </div>

      <div className="space-y-4">
        {/* Confidence */}
        <ConfidenceBar value={run.triage_result?.confidence ?? 0} />

        {/* Evidence */}
        <div>
          <p className="text-[11px] text-cream/40 uppercase tracking-wide mb-1">Evidence</p>
          <p className="text-[13px] text-cream/70">{run.triage_result?.evidence}</p>
        </div>

        {/* Selector diff */}
        {fix && (
          <div>
            <p className="text-[11px] text-cream/40 uppercase tracking-wide mb-1">Proposed Fix</p>
            <SelectorDiff oldSel={fix.old} newSel={fix.new} />
            <p className="text-[11px] text-cream/30 mt-1 font-mono">{fix.file}</p>
          </div>
        )}

        {/* Reviewer name */}
        <div>
          <label className="text-[11px] text-cream/40 uppercase tracking-wide block mb-1">
            Your name
          </label>
          <input
            type="text"
            value={reviewerName}
            onChange={(e) => setReviewerName(e.target.value)}
            placeholder="e.g. Lamaq"
            className={clsx(
              "w-full h-9 px-3 rounded-md border border-cream/12 bg-card",
              "text-[13px] text-cream placeholder:text-cream/25 outline-none",
              "focus:border-blue/50 focus:ring-1 focus:ring-blue/20 transition"
            )}
          />
        </div>

        {/* Buttons */}
        <div className="flex gap-3 pt-1">
          <button
            onClick={() => handleAction(true)}
            disabled={submitting || !reviewerName.trim()}
            className={clsx(
              "flex-1 h-10 rounded-md text-[13px] font-semibold transition",
              "bg-green/15 text-green border border-green/25",
              "hover:bg-green/25 disabled:opacity-40 disabled:cursor-not-allowed"
            )}
          >
            {submitting ? "Submitting…" : "Approve Fix"}
          </button>
          <button
            onClick={() => handleAction(false)}
            disabled={submitting || !reviewerName.trim()}
            className={clsx(
              "flex-1 h-10 rounded-md text-[13px] font-semibold transition",
              "bg-red/10 text-red border border-red/20",
              "hover:bg-red/20 disabled:opacity-40 disabled:cursor-not-allowed"
            )}
          >
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}
