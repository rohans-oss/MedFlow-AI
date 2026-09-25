"use client";

import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { ReplenishmentCalc, Scenario } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

// Validated categorical slots (same palette as V2–V4 charts).
const SERIES = ["#2a78d6", "#eb6834"];
const MUTED = "#94a3b8";
const GRID = "#e2e8f0";
const AXIS = { fontSize: 11, fill: "#64748b" };
const shortDate = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
const money = (v: number) => `₹${formatNumber(Math.round(v))}`;

/** V5.2: projected available stock without any delivery (V3 view) vs with orders in transit weighted by their
 *  historical arrival probability; safety stock as a reference line. Negative = expected shortfall. */
export function AvailabilityChart({ rep, unit }: { rep: ReplenishmentCalc; unit: string }) {
  const points = rep.projection;
  const differs = points.some((p) => Math.abs(p.with_in_transit - p.without_deliveries) > 0.05);
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-5 rounded" style={{ background: SERIES[0] }} />Without deliveries (V3 view)</span>
        {differs && <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-5 rounded" style={{ background: SERIES[1] }} />With in-transit orders × arrival probability</span>}
        <span className="inline-flex items-center gap-1.5"><span className="w-5 border-t-2 border-dashed" style={{ borderColor: MUTED }} />Safety stock ({formatNumber(Math.round(rep.safety_stock))})</span>
      </div>
      <div className="h-60" role="img" aria-label={`Projected available stock for ${points.length} days, with and without orders in transit`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} tickLine={false} axisLine={false} minTickGap={28} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={56} allowDecimals={false} />
            <Tooltip
              cursor={{ stroke: MUTED, strokeDasharray: "3 3" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as (typeof points)[number];
                return (
                  <div className="rounded-xl border border-white/80 bg-white/90 px-3 py-2 text-xs shadow-lg shadow-teal-950/10 ring-1 ring-slate-900/5 backdrop-blur">
                    <div className="text-muted-foreground">{shortDate(p.date)} (end of day)</div>
                    <div className="mt-0.5">Without deliveries: <b>{formatNumber(Math.round(p.without_deliveries))}</b> {unit}</div>
                    {differs && <div>With in-transit (expected): <b>{formatNumber(Math.round(p.with_in_transit))}</b> {unit}</div>}
                  </div>
                );
              }}
            />
            <ReferenceLine y={0} stroke="#cbd5e1" />
            {rep.safety_stock > 0 && <ReferenceLine y={rep.safety_stock} stroke={MUTED} strokeDasharray="4 4" />}
            <Line dataKey="without_deliveries" stroke={SERIES[0]} strokeWidth={2} dot={false} isAnimationActive={false} />
            {differs && <Line dataKey="with_in_transit" stroke={SERIES[1]} strokeWidth={2} dot={false} isAnimationActive={false} />}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Expected total cost per scenario (lower is better); the recommended scenario in the second slot. */
export function ScenarioCostChart({ scenarios }: { scenarios: Scenario[] }) {
  const data = scenarios.map((s) => ({ label: s.label, total: s.costs.total, rec: s.tags.includes("recommended"), s }));
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: SERIES[1] }} />Recommended (lowest expected cost)</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: SERIES[0] }} />Other scenarios</span>
      </div>
      <div style={{ height: Math.max(160, data.length * 30 + 30) }} role="img" aria-label="Expected total cost per scenario">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, left: 8, bottom: 0 }}>
            <CartesianGrid stroke={GRID} horizontal={false} />
            <XAxis type="number" tick={AXIS} tickLine={false} axisLine={false} tickFormatter={money} />
            <YAxis type="category" dataKey="label" tick={AXIS} tickLine={false} axisLine={false} width={200} />
            <Tooltip
              cursor={{ fill: "#f1f5f9" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const s = (payload[0].payload as (typeof data)[number]).s;
                return (
                  <div className="rounded-xl border border-white/80 bg-white/90 px-3 py-2 text-xs shadow-lg shadow-teal-950/10 ring-1 ring-slate-900/5 backdrop-blur">
                    <div className="font-medium">{s.label}</div>
                    <div>Expected total cost <b>{money(s.costs.total)}</b></div>
                    <div className="text-muted-foreground">purchase {money(s.costs.purchase)} · stockout {money(s.costs.stockout)} · holding {money(s.costs.holding)} · expiry {money(s.costs.expiry)} · carried forward −{money(s.costs.carried_forward)}</div>
                    <div className="text-muted-foreground">P(stockout) {(s.metrics.p_stockout * 100).toFixed(0)}% · expected shortage {s.metrics.expected_shortage.toFixed(1)}</div>
                  </div>
                );
              }}
            />
            <Bar dataKey="total" isAnimationActive={false} radius={[0, 3, 3, 0]}>
              {data.map((d) => <Cell key={d.label} fill={d.rec ? SERIES[1] : SERIES[0]} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
