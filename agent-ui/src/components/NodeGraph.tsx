"use client";

import ReactFlow, {
  Node,
  Edge,
  Background,
  BackgroundVariant,
  MiniMap,
  useNodesState,
  useEdgesState,
} from "reactflow";
import "reactflow/dist/style.css";
import { clsx } from "clsx";
import type { NodeState, QARun, NodeUpdate } from "@/lib/types";
import { NODE_LABELS } from "@/lib/types";
import { useEffect } from "react";

// ── Node state → visual config ──────────────────────────────────────────────

const STATE_CONFIG: Record<NodeState, { border: string; bg: string; label: string; anim?: string }> = {
  idle:    { border: "border-cream/10",  bg: "bg-card",     label: "text-cream/30",  },
  running: { border: "border-blue",      bg: "bg-card",     label: "text-blue",      anim: "animate-shimmer-blue"   },
  success: { border: "border-green",     bg: "bg-green/5",  label: "text-green",     },
  failed:  { border: "border-red",       bg: "bg-red/5",    label: "text-red",       },
  skipped: { border: "border-cream/10",  bg: "bg-bash",     label: "text-cream/25",  },
  waiting: { border: "border-yellow",    bg: "bg-card",     label: "text-yellow",    anim: "animate-shimmer-yellow" },
};

const STATE_ICON: Record<NodeState, string> = {
  idle:    "○",
  running: "◉",
  success: "✓",
  failed:  "✗",
  skipped: "—",
  waiting: "⊙",
};

// ── Custom node component ────────────────────────────────────────────────────

function PipelineNode({ data }: { data: { nodeKey: string; update: NodeUpdate } }) {
  const { nodeKey, update } = data;
  const cfg = STATE_CONFIG[update.state];
  const isSkipped = update.state === "skipped";

  return (
    <div
      className={clsx(
        "w-44 rounded-lg border-l-[3px] border border-cream/8 p-3 select-none",
        cfg.border, cfg.bg, cfg.anim
      )}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className={clsx("text-sm font-mono", cfg.label)}>{STATE_ICON[update.state]}</span>
        <span
          className={clsx(
            "text-[13px] font-semibold tracking-tight",
            cfg.label,
            isSkipped && "line-through opacity-50"
          )}
        >
          {NODE_LABELS[nodeKey] ?? nodeKey}
        </span>
      </div>
      {update.annotation && (
        <p className="text-[11px] text-cream/40 leading-tight mt-1 italic font-mono">
          {update.annotation}
        </p>
      )}
    </div>
  );
}

const nodeTypes = { pipeline: PipelineNode };

// ── Layout positions ─────────────────────────────────────────────────────────

const POSITIONS: Record<string, { x: number; y: number }> = {
  git_watcher:       { x: 0,   y: 0   },
  change_analyzer:   { x: 0,   y: 90  },
  test_runner:       { x: 0,   y: 180 },
  browser_inspector: { x: 0,   y: 270 },
  classifier:        { x: 0,   y: 360 },
  human_review:      { x: 220, y: 360 },
  auto_fixer:        { x: -110,y: 450 },
  ticket_creator:    { x: 110, y: 450 },
  reporter:          { x: 0,   y: 540 },
};

const EDGES: Edge[] = [
  { id: "e1", source: "git_watcher",       target: "change_analyzer",   animated: true },
  { id: "e2", source: "change_analyzer",   target: "test_runner",       animated: true },
  { id: "e3", source: "test_runner",       target: "browser_inspector", animated: true },
  { id: "e4", source: "browser_inspector", target: "classifier",        animated: true },
  { id: "e5", source: "classifier",        target: "auto_fixer",        animated: true, label: "drift ≥ 0.80" },
  { id: "e6", source: "classifier",        target: "ticket_creator",    animated: true, label: "bug | env" },
  { id: "e7", source: "classifier",        target: "human_review",      animated: true, label: "drift < 0.80" },
  { id: "e8", source: "human_review",      target: "auto_fixer",        animated: true, label: "approved" },
  { id: "e9", source: "auto_fixer",        target: "reporter",          animated: true },
  { id:"e10", source: "ticket_creator",    target: "reporter",          animated: true },
];

// ── Main component ───────────────────────────────────────────────────────────

export function NodeGraph({ run }: { run: QARun | null }) {
  const nodeStates = run?.node_states ?? {};

  const initialNodes: Node[] = Object.keys(POSITIONS).map((key) => ({
    id: key,
    type: "pipeline",
    position: POSITIONS[key],
    data: {
      nodeKey: key,
      update: nodeStates[key] ?? { node: key, state: "idle", annotation: "" },
    },
    draggable: false,
  }));

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(EDGES);

  useEffect(() => {
    if (!run) return;
    setNodes((prev) =>
      prev.map((n) => ({
        ...n,
        data: {
          ...n.data,
          update: nodeStates[n.id] ?? { node: n.id, state: "idle", annotation: "" },
        },
      }))
    );
  }, [run, setNodes]);

  return (
    <div className="h-[640px] rounded-lg border border-cream/8 bg-surface overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(255,255,235,0.04)" />
      </ReactFlow>
    </div>
  );
}
