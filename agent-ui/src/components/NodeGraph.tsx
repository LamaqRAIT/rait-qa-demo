"use client";

import ReactFlow, {
  Node,
  Edge,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  MarkerType,
} from "reactflow";
import "reactflow/dist/style.css";
import dagre from "@dagrejs/dagre";
import { clsx } from "clsx";
import type { NodeState, QARun, NodeUpdate } from "@/lib/types";
import { NODE_LABELS } from "@/lib/types";
import { useEffect, useMemo } from "react";

// ── State → visual ────────────────────────────────────────────────────────────

const STATE_CONFIG: Record<NodeState, {
  border: string; bg: string; label: string; icon: string; anim?: string;
}> = {
  idle:    { border: "border-cream/8",   bg: "bg-card",         label: "text-cream/25",  icon: "○" },
  running: { border: "border-blue",      bg: "bg-blue/5",       label: "text-blue",      icon: "◉", anim: "animate-shimmer-blue" },
  success: { border: "border-green",     bg: "bg-green/8",      label: "text-green",     icon: "✓" },
  failed:  { border: "border-red",       bg: "bg-red/8",        label: "text-red",       icon: "✗" },
  skipped: { border: "border-cream/6",   bg: "bg-bash",         label: "text-cream/20",  icon: "—" },
  waiting: { border: "border-yellow",    bg: "bg-yellow/5",     label: "text-yellow",    icon: "⊙", anim: "animate-shimmer-yellow" },
};

// ── Custom pipeline node ──────────────────────────────────────────────────────

function PipelineNode({ data }: { data: { nodeKey: string; update: NodeUpdate; timing?: number } }) {
  const { nodeKey, update, timing } = data;
  const cfg = STATE_CONFIG[update.state];
  const isSkipped = update.state === "skipped";

  return (
    <div
      className={clsx(
        "rounded-xl border-l-[3px] border p-3 select-none shadow-sm transition-all duration-300",
        "w-[210px]",
        cfg.border, cfg.bg, cfg.anim
      )}
    >
      {/* Header row */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className={clsx("text-[13px] font-mono font-bold w-4 text-center flex-shrink-0", cfg.label)}>
          {cfg.icon}
        </span>
        <span className={clsx(
          "text-[13px] font-semibold tracking-tight flex-1 truncate",
          cfg.label,
          isSkipped && "line-through opacity-40"
        )}>
          {NODE_LABELS[nodeKey] ?? nodeKey}
        </span>
        {timing !== undefined && (
          <span className="text-[10px] text-cream/25 font-mono flex-shrink-0">{timing.toFixed(1)}s</span>
        )}
      </div>

      {/* Annotation */}
      {update.annotation && (
        <p className="text-[10px] text-cream/35 leading-snug font-mono truncate" title={update.annotation}>
          {update.annotation}
        </p>
      )}
    </div>
  );
}

const nodeTypes = { pipeline: PipelineNode };

// ── Dagre layout ─────────────────────────────────────────────────────────────

const NODE_W = 210;
const NODE_H = 70;

const EDGE_DEFS: Array<{ id: string; source: string; target: string; label?: string }> = [
  { id: "e1", source: "git_watcher",       target: "change_analyzer" },
  { id: "e2", source: "change_analyzer",   target: "test_runner" },
  { id: "e3", source: "test_runner",       target: "browser_inspector" },
  { id: "e4", source: "browser_inspector", target: "classifier" },
  { id: "e5", source: "classifier",        target: "auto_fixer",     label: "drift≥thresh" },
  { id: "e6", source: "classifier",        target: "ticket_creator", label: "bug|env" },
  { id: "e7", source: "classifier",        target: "human_review",   label: "drift<thresh" },
  { id: "e8", source: "human_review",      target: "auto_fixer",     label: "approved" },
  { id: "e9", source: "auto_fixer",        target: "reporter" },
  { id: "e10",source: "ticket_creator",    target: "reporter" },
];

const NODE_KEYS = [
  "git_watcher", "change_analyzer", "test_runner", "browser_inspector",
  "classifier", "human_review", "auto_fixer", "ticket_creator", "reporter",
];

function buildDagreLayout(): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", ranksep: 55, nodesep: 40, edgesep: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  NODE_KEYS.forEach((k) => g.setNode(k, { width: NODE_W, height: NODE_H }));
  EDGE_DEFS.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  const positions: Record<string, { x: number; y: number }> = {};
  NODE_KEYS.forEach((k) => {
    const { x, y } = g.node(k);
    positions[k] = { x: x - NODE_W / 2, y: y - NODE_H / 2 };
  });
  return positions;
}

const POSITIONS = buildDagreLayout();

// ── Main component ───────────────────────────────────────────────────────────

export function NodeGraph({ run }: { run: QARun | null }) {
  const nodeStates = run?.node_states ?? {};
  const timings = run?.node_timings ?? {};
  const activeStatus = run?.status ?? "idle";
  const isRunning = !["complete", "failed", "quarantined"].includes(activeStatus);

  const initialNodes: Node[] = useMemo(() => NODE_KEYS.map((key) => ({
    id: key,
    type: "pipeline",
    position: POSITIONS[key],
    data: {
      nodeKey: key,
      update: nodeStates[key] ?? { node: key, state: "idle", annotation: "" },
      timing: timings[key],
    },
    draggable: false,
  })), []); // eslint-disable-line

  const initialEdges: Edge[] = EDGE_DEFS.map((e) => {
    const sourceState = nodeStates[e.source]?.state;
    const animated = isRunning && sourceState === "running";
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label,
      animated,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "rgba(96,164,223,0.5)" },
      style: { stroke: "rgba(96,164,223,0.35)", strokeWidth: 1.5 },
      labelStyle: { fill: "rgba(255,255,235,0.3)", fontSize: 10 },
      labelBgStyle: { fill: "rgba(14,8,4,0.7)", fillOpacity: 0.8 },
    };
  });

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  useEffect(() => {
    if (!run) return;
    setNodes((prev) =>
      prev.map((n) => ({
        ...n,
        data: {
          nodeKey: n.id,
          update: nodeStates[n.id] ?? { node: n.id, state: "idle", annotation: "" },
          timing: timings[n.id],
        },
      }))
    );
    setEdges((prev) =>
      prev.map((e) => {
        const sourceState = nodeStates[e.source]?.state;
        const animated = isRunning && sourceState === "running";
        return { ...e, animated };
      })
    );
  }, [run]); // eslint-disable-line

  return (
    <div>
      <div className="h-[520px] rounded-xl border border-cream/8 bg-surface overflow-hidden">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.18, maxZoom: 1.0 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="rgba(255,255,235,0.035)" />
        </ReactFlow>
      </div>

      {/* Timeline strip */}
      {run && Object.keys(timings).length > 0 && (
        <div className="mt-2 flex gap-1 flex-wrap">
          {Object.entries(timings).map(([node, ms]) => (
            <div key={node} className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-card border border-cream/8 text-[10px] font-mono text-cream/35">
              <span className="text-cream/50">{NODE_LABELS[node] ?? node}</span>
              <span>{Number(ms).toFixed(1)}s</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
