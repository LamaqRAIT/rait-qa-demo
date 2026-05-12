"use client";

import { useEffect, useState } from "react";
import { clsx } from "clsx";
import { fetchMetricsSummary, fetchCircuitBreakers, fetchClassifications } from "@/lib/api";
import type { MetricsSummary, CircuitBreakerStatus } from "@/lib/types";

function StatCard({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
  return (
    <div className="bg-card border border-cream/8 rounded-xl p-4">
      <p className="text-[10px] text-cream/30 uppercase tracking-widest font-semibold mb-2">{label}</p>
      <p className={clsx("text-[24px] font-bold tracking-tight", accent ?? "text-cream/85")}>{value}</p>
      {sub && <p className="text-[11px] text-cream/30 mt-1">{sub}</p>}
    </div>
  );
}

function SeverityDot({ severity }: { severity: string }) {
  return (
    <span className={clsx(
      "w-1.5 h-1.5 rounded-full inline-block mr-1.5",
      severity === "critical" && "bg-red",
      severity === "warning"  && "bg-yellow",
      severity === "info"     && "bg-blue/60",
    )} />
  );
}

export function MetricsPanel() {
  const [summary, setSummary] = useState<MetricsSummary | null>(null);
  const [cb, setCb] = useState<CircuitBreakerStatus | null>(null);
  const [dist, setDist] = useState<Array<{ classification: string; count: number }>>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    const [s, c, d] = await Promise.all([
      fetchMetricsSummary(),
      fetchCircuitBreakers(),
      fetchClassifications(),
    ]);
    setSummary(s);
    setCb(c);
    setDist(d);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  if (loading) {
    return (
      <div className="h-48 flex items-center justify-center text-cream/25 text-sm animate-pulse">
        Loading metrics…
      </div>
    );
  }

  const totalDist = dist.reduce((a, b) => a + b.count, 0) || 1;

  return (
    <div className="space-y-5">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-3">
        <StatCard
          label="Total Runs"
          value={String(summary?.total_runs ?? 0)}
          sub="last 7 days"
        />
        <StatCard
          label="Success Rate"
          value={`${((summary?.success_rate ?? 0) * 100).toFixed(0)}%`}
          sub={`${summary?.success_runs ?? 0} passed`}
          accent={summary && summary.success_rate >= 0.7 ? "text-green" : "text-red"}
        />
        <StatCard
          label="Avg Cost / Run"
          value={`$${(summary?.avg_cost_usd ?? 0).toFixed(4)}`}
          sub={`total $${(summary?.total_cost_usd ?? 0).toFixed(4)}`}
        />
        <StatCard
          label="Avg Confidence"
          value={`${((summary?.avg_confidence ?? 0) * 100).toFixed(0)}%`}
          sub="triage accuracy proxy"
          accent={summary && summary.avg_confidence >= 0.80 ? "text-green" : "text-yellow"}
        />
      </div>

      {/* Classification distribution */}
      {dist.length > 0 && (
        <div className="bg-card border border-cream/8 rounded-xl p-4">
          <p className="text-[10px] text-cream/30 uppercase tracking-widest font-semibold mb-3">
            Classification Distribution (last 30d)
          </p>
          <div className="space-y-2">
            {dist.map((d) => {
              const pct = Math.round((d.count / totalDist) * 100);
              const color = d.classification === "drift" ? "#60A4DF"
                          : d.classification === "bug"   ? "#DF6460"
                          : "#DFD660";
              return (
                <div key={d.classification}>
                  <div className="flex justify-between text-[12px] mb-1">
                    <span className="font-medium text-cream/65 uppercase">{d.classification}</span>
                    <span className="font-mono text-cream/35">{d.count} ({pct}%)</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-cream/5 overflow-hidden">
                    <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Circuit breaker status */}
      {cb && (
        <div className={clsx(
          "border rounded-xl p-4",
          cb.threshold_overridden ? "border-yellow/25 bg-yellow/5" : "border-cream/8 bg-card"
        )}>
          <div className="flex items-center justify-between mb-3">
            <p className="text-[10px] text-cream/30 uppercase tracking-widest font-semibold">
              Circuit Breakers
            </p>
            <div className="flex items-center gap-2">
              <span className={clsx(
                "text-[10px] font-semibold px-2 py-0.5 rounded-full",
                cb.threshold_overridden ? "bg-yellow/15 text-yellow" : "bg-green/15 text-green"
              )}>
                {cb.threshold_overridden ? "OVERRIDDEN" : "NOMINAL"}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-4 text-[12px] mb-3">
            <div>
              <span className="text-cream/30">Base threshold: </span>
              <span className="font-mono text-cream/60">{(cb.base_threshold * 100).toFixed(0)}%</span>
            </div>
            <div>
              <span className="text-cream/30">Effective: </span>
              <span className={clsx("font-mono font-semibold", cb.threshold_overridden ? "text-yellow" : "text-cream/60")}>
                {cb.effective_threshold >= 1 ? "∞ (HITL forced)" : `${(cb.effective_threshold * 100).toFixed(0)}%`}
              </span>
            </div>
          </div>

          {cb.recent_events.length > 0 && (
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {cb.recent_events.map((ev) => (
                <div key={ev.id} className="flex items-start gap-2 text-[11px]">
                  <SeverityDot severity={ev.severity} />
                  <span className="text-cream/35 font-mono shrink-0">{new Date(ev.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                  <span className="text-cream/55 font-semibold shrink-0">{ev.event_type.replace(/_/g, " ")}</span>
                  <span className="text-cream/30 truncate">{ev.message}</span>
                </div>
              ))}
            </div>
          )}
          {cb.recent_events.length === 0 && (
            <p className="text-[12px] text-cream/20">No circuit breaker events yet.</p>
          )}
        </div>
      )}

      <button
        onClick={load}
        className="text-[11px] text-cream/25 hover:text-cream/50 transition"
      >
        ↻ Refresh metrics
      </button>
    </div>
  );
}
