"use client";

import { useState } from "react";
import { clsx } from "clsx";
import type { Notification } from "@/lib/types";

const EVENT_ICONS: Record<string, string> = {
  bug_filed:                "🔴",
  env_alert:                "⚠️",
  hitl_requested:           "👀",
  pr_opened:                "✅",
  circuit_breaker_fired:    "🚨",
  quarantine_triggered:     "🔒",
};

const CHANNEL_BADGE: Record<string, string> = {
  slack:  "bg-green/15 text-green",
  inapp:  "bg-blue/10 text-blue/60",
};

export function NotificationBell({ notifications }: { notifications: Notification[] }) {
  const [open, setOpen] = useState(false);
  const unread = notifications.filter((n) => n.status !== "read").length;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={clsx(
          "relative w-8 h-8 rounded-lg flex items-center justify-center transition",
          "border border-cream/8 bg-card hover:bg-surface text-cream/40 hover:text-cream/70",
          open && "bg-surface text-cream/70"
        )}
        aria-label="Notifications"
      >
        <span className="text-[14px]">🔔</span>
        {notifications.length > 0 && (
          <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-blue text-canvas text-[8px] font-bold flex items-center justify-center">
            {notifications.length > 9 ? "9+" : notifications.length}
          </span>
        )}
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />

          {/* Dropdown */}
          <div className="absolute right-0 top-10 z-50 w-96 bg-elevated border border-cream/10 rounded-xl shadow-xl overflow-hidden animate-fade-in">
            <div className="flex items-center justify-between px-4 py-3 border-b border-cream/8">
              <p className="text-[12px] font-semibold text-cream/70">Notifications</p>
              <p className="text-[10px] text-cream/25">{notifications.length} total</p>
            </div>

            <div className="max-h-96 overflow-y-auto">
              {notifications.length === 0 ? (
                <div className="px-4 py-8 text-center text-[12px] text-cream/20">
                  No notifications yet
                </div>
              ) : (
                notifications.map((n) => (
                  <div key={n.id} className="flex items-start gap-3 px-4 py-3 border-b border-cream/5 hover:bg-surface/50 transition">
                    <span className="text-[16px] mt-0.5 shrink-0">{EVENT_ICONS[n.event_type] ?? "📢"}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-[12px] font-medium text-cream/75 truncate">{n.title.replace(/:[a-z_]+:/g, "")}</span>
                        <span className={clsx(
                          "shrink-0 text-[9px] font-semibold px-1.5 py-0.5 rounded-full uppercase",
                          CHANNEL_BADGE[n.channel] ?? "bg-cream/10 text-cream/30"
                        )}>
                          {n.channel}
                        </span>
                      </div>
                      <p className="text-[11px] text-cream/35 leading-snug line-clamp-2">{n.message}</p>
                      {n.created_at && (
                        <p className="text-[10px] text-cream/20 mt-1">
                          {new Date(n.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </p>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
