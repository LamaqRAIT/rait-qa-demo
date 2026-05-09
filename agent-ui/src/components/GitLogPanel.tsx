"use client";

import { useEffect, useState, useRef } from "react";
import { clsx } from "clsx";
import type { GitCommit } from "@/lib/types";
import { fetchGitLog } from "@/lib/api";

export function GitLogPanel({ latestCommitSha }: { latestCommitSha?: string | null }) {
  const [commits, setCommits] = useState<GitCommit[]>([]);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const prevSha = useRef<string | null>(null);

  useEffect(() => {
    async function load() {
      const data = await fetchGitLog();
      setCommits(data);

      // Highlight newly appeared commit
      if (latestCommitSha && latestCommitSha !== prevSha.current) {
        prevSha.current = latestCommitSha;
        setHighlighted(latestCommitSha);
        setTimeout(() => setHighlighted(null), 3000);
      }
    }
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [latestCommitSha]);

  return (
    <div className="rounded-lg border border-cream/8 bg-surface overflow-hidden">
      <div className="px-4 py-2 border-b border-cream/8 flex items-center gap-2">
        <span className="text-[11px] text-cream/35 uppercase tracking-widest font-semibold">
          Git Log
        </span>
        <span className="text-cream/20 text-[11px]">last 5 commits</span>
      </div>
      <div className="divide-y divide-cream/5">
        {commits.length === 0 ? (
          <div className="px-4 py-4 text-[12px] text-cream/25">
            No commits found — is the repo configured?
          </div>
        ) : (
          commits.map((c) => (
            <div
              key={c.sha}
              className={clsx(
                "flex items-start gap-3 px-4 py-2.5 transition-colors duration-700",
                c.sha === highlighted ? "bg-blue/12" : "hover:bg-surface"
              )}
            >
              <span className="font-mono text-[11px] text-blue/70 shrink-0 pt-0.5">
                {c.sha}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-[12px] text-cream/75 truncate">{c.message}</p>
                <p className="text-[11px] text-cream/30 mt-0.5">
                  {c.author} · {c.ago}
                </p>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
