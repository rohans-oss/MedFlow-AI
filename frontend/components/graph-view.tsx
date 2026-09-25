"use client";

import type { ChainLink, GraphLabel, SubgraphEdge, SubgraphNode } from "@/lib/types";

// Node types are identified by their text label (not colour); only the focus item (slot 1) and risk level use colour.
const FOCUS = "#2a78d6";
const RISK: Record<string, string> = { HIGH: "#d03b3b", MEDIUM: "#d98a00", LOW: "#1baf7a" };
const LABEL_TEXT: Record<GraphLabel, string> = {
  Hospital: "Hospital", Department: "Department", Category: "Category", Item: "Item", Procedure: "Procedure",
  Supplier: "Supplier", Batch: "Batch", Forecast: "Forecast", StockoutRisk: "Stockout risk", SupplierOrder: "Order",
  ProcurementRecommendation: "Recommendation",
};
const COLUMNS = ["Suppliers", "Orders · procurement", "Item", "Procedures", "Departments"];

const W = 170;
const H = 46;
const GAP_X = 44;
const GAP_Y = 14;

function clip(s: string, n = 26) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/** Layered drawing of an item's neighbourhood: suppliers → orders → item (+ risk, forecast) → procedures → departments. */
export function GraphView({ nodes, edges }: { nodes: SubgraphNode[]; edges: SubgraphEdge[] }) {
  const cols = COLUMNS.map((_, c) => nodes.filter((n) => n.column === c));
  const rows = Math.max(1, ...cols.map((c) => c.length));
  const height = rows * (H + GAP_Y) + 40;
  const width = COLUMNS.length * (W + GAP_X) - GAP_X;
  const pos = new Map<string, { x: number; y: number }>();
  cols.forEach((col, c) => {
    const top = 30 + ((rows - col.length) * (H + GAP_Y)) / 2;
    col.forEach((n, i) => pos.set(n.id, { x: c * (W + GAP_X), y: top + i * (H + GAP_Y) }));
  });
  return (
    <div className="overflow-x-auto" data-testid="graph-view">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Knowledge-graph neighbourhood of the item" className="min-w-full">
        {COLUMNS.map((c, i) => (
          <text key={c} x={i * (W + GAP_X) + W / 2} y={14} textAnchor="middle" fontSize={11} fill="#64748b">{c}</text>
        ))}
        {edges.map((e, i) => {
          const a = pos.get(e.from);
          const b = pos.get(e.to);
          if (!a || !b) return null;
          const [l, r] = a.x <= b.x ? [a, b] : [b, a];
          const same = l.x === r.x;
          const x1 = same ? l.x + W / 2 : l.x + W;
          const y1 = l.y + (same ? H : H / 2);
          const x2 = same ? r.x + W / 2 : r.x;
          const y2 = r.y + (same ? 0 : H / 2);
          const d = same ? `M${x1},${y1} L${x2},${y2}` : `M${x1},${y1} C${x1 + GAP_X / 2},${y1} ${x2 - GAP_X / 2},${y2} ${x2},${y2}`;
          return (
            <path key={i} d={d} fill="none" stroke="#cbd5e1" strokeWidth={1.5}>
              <title>{e.type}</title>
            </path>
          );
        })}
        {nodes.map((n) => {
          const p = pos.get(n.id)!;
          const level = n.label === "StockoutRisk" ? n.name.split(" ")[0] : null;
          const accent = n.label === "Item" ? FOCUS : level ? RISK[level] ?? "#94a3b8" : "#94a3b8";
          return (
            <g key={n.id} transform={`translate(${p.x},${p.y})`}>
              <title>{`${LABEL_TEXT[n.label]}: ${n.name}${n.detail ? ` — ${n.detail}` : ""}`}</title>
              <rect width={W} height={H} rx={8} fill={n.label === "Item" ? "#eff6ff" : "#ffffff"} stroke={accent} strokeWidth={n.label === "Item" ? 2 : 1} />
              <rect width={4} height={H} rx={2} fill={accent} />
              <text x={12} y={17} fontSize={10} fill="#64748b">{LABEL_TEXT[n.label]}{n.detail ? ` · ${clip(n.detail, 14)}` : ""}</text>
              <text x={12} y={34} fontSize={12} fontWeight={600} fill="#0f172a">{clip(n.name, 22)}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** A traversal as a horizontal chain: Supplier → Item → Procedure → Department → Risk. */
export function Chain({ links }: { links: ChainLink[] }) {
  return (
    <ol className="flex flex-wrap items-center gap-1.5 text-xs" data-testid="impact-chain">
      {links.map((l, i) => (
        <li key={i} className="flex items-center gap-1.5">
          {i > 0 && <span aria-hidden className="text-muted-foreground">→</span>}
          <span className="rounded-md border bg-white px-2 py-1">
            <span className="text-muted-foreground">{LABEL_TEXT[l.label]}</span> <span className="font-medium">{l.name}</span>
            {l.detail && <span className="text-muted-foreground"> · {l.detail}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
