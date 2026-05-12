"use client";

import { useEffect, useState } from "react";
import { clsx } from "clsx";
import { fetchTickets } from "@/lib/api";
import type { Ticket } from "@/lib/types";

const SEVERITY_STYLES: Record<string, string> = {
  high:   "bg-red/15 text-red",
  medium: "bg-yellow/15 text-yellow",
  low:    "bg-cream/10 text-cream/40",
};

const STATUS_STYLES: Record<string, string> = {
  open:        "bg-blue/15 text-blue",
  in_progress: "bg-yellow/15 text-yellow",
  resolved:    "bg-green/15 text-green",
};

const TYPE_ICON: Record<string, string> = {
  bug: "🔴",
  env: "⚠️",
};

export function TicketsPanel() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchTickets().then((t) => { setTickets(t); setLoading(false); });
  }, []);

  if (loading) {
    return (
      <div className="h-48 flex items-center justify-center text-cream/25 text-sm animate-pulse">
        Loading tickets…
      </div>
    );
  }

  if (tickets.length === 0) {
    return (
      <div className="h-48 flex items-center justify-center text-cream/25 text-sm">
        No tickets yet — trigger a bug or env flow to create one.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {tickets.map((t) => (
        <div key={t.id} className="bg-card border border-cream/8 rounded-xl p-4 hover:border-cream/15 transition">
          <div className="flex items-start justify-between gap-3 mb-2">
            <div className="flex items-center gap-2">
              <span className="text-[13px]">{TYPE_ICON[t.ticket_type] ?? "📋"}</span>
              <span className="font-mono text-[12px] font-semibold text-cream/55">{t.key}</span>
              <span className={clsx(
                "text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full",
                SEVERITY_STYLES[t.severity] ?? SEVERITY_STYLES.low
              )}>
                {t.severity}
              </span>
              <span className={clsx(
                "text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full",
                STATUS_STYLES[t.status] ?? STATUS_STYLES.open
              )}>
                {t.status.replace("_", " ")}
              </span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {t.jira_url ? (
                <a href={t.jira_url} target="_blank" rel="noreferrer"
                  className="text-[11px] text-blue/70 hover:text-blue underline">
                  {t.jira_remote_id ?? "Jira →"}
                </a>
              ) : (
                <span className="text-[10px] text-cream/20 italic">local only</span>
              )}
            </div>
          </div>

          <p className="text-[13px] text-cream/75 mb-1 font-medium leading-tight line-clamp-1">
            {t.title.replace("[QA Agent] ", "")}
          </p>

          <div className="flex items-center gap-3 text-[10px] text-cream/25 mt-2">
            {t.run_id && (
              <span className="font-mono">Run: {t.run_id.slice(0, 8)}</span>
            )}
            {t.created_at && (
              <span>{new Date(t.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
            )}
            {t.team_id && (
              <span className="text-cream/20">{t.team_id}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
