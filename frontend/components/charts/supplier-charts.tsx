"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { SupplierDetailPerf } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

// Validated categorical slots (dataviz validator, light surface): slot 1 blue, slot 2 orange.
const S1 = "#2a78d6";
const S2 = "#eb6834";
const MUTED = "#94a3b8";
const GRID = "#e2e8f0";
const AXIS = { fontSize: 11, fill: "#64748b" };
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const monthLabel = (m: string) => new Date(`${m}-01T00:00:00`).toLocaleDateString("en-IN", { month: "short", year: "2-digit" });

function Legend({ items }: { items: [string, string, boolean?][] }) {
  return (
    <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
      {items.map(([color, label, dashed]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          {dashed ? <span className="w-5 border-t-2 border-dashed" style={{ borderColor: color }} /> : <span className="h-0.5 w-5 rounded" style={{ background: color }} />}
          {label}
        </span>
      ))}
    </div>
  );
}

/** Monthly OTIF (the reliability score's basis) and on-time rate — same unit (%), one axis. */
export function ReliabilityTrendChart({ data }: { data: SupplierDetailPerf["monthly"] }) {
  const points = data.filter((m) => m.decided > 0);
  if (points.length < 2) return <p className="text-sm text-muted-foreground">Not enough monthly history yet.</p>;
  return (
    <div>
      <Legend items={[[S1, "On time and in full (OTIF)"], [S2, "On time (first delivery)"]]} />
      <div className="h-56" role="img" aria-label="Monthly OTIF and on-time rates">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="month" tickFormatter={monthLabel} tick={AXIS} tickLine={false} axisLine={false} minTickGap={16} />
            <YAxis domain={[0, 1]} tickFormatter={pct} tick={AXIS} tickLine={false} axisLine={false} width={40} />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as (typeof points)[number];
                return (
                  <div className="rounded-xl border border-white/80 bg-white/90 px-3 py-2 text-xs shadow-lg shadow-teal-950/10 ring-1 ring-slate-900/5 backdrop-blur">
                    <div className="text-muted-foreground">{monthLabel(p.month)} · {p.orders} orders</div>
                    <div className="mt-0.5 font-semibold">OTIF {pct(p.otif_rate)}</div>
                    <div>On time {pct(p.on_time_rate)}</div>
                    <div className="text-muted-foreground">avg price ₹{p.avg_price.toFixed(2)}</div>
                  </div>
                );
              }}
            />
            <Line dataKey="otif_rate" stroke={S1} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls />
            <Line dataKey="on_time_rate" stroke={S2} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** How many days deliveries actually took, against the quoted lead time. */
export function LeadTimeHistogram({ data, quoted }: { data: SupplierDetailPerf["lead_time_histogram"]; quoted: number | null }) {
  if (!data.some((d) => d.orders > 0)) return <p className="text-sm text-muted-foreground">No deliveries yet.</p>;
  return (
    <div>
      <Legend items={[[S1, "Deliveries by actual lead time (days)"], ...(quoted != null ? [[MUTED, `Quoted lead time (${quoted} d)`, true] as [string, string, boolean]] : [])]} />
      <div className="h-56" role="img" aria-label="Distribution of actual delivery lead times">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="days" tick={AXIS} tickLine={false} axisLine={false} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={36} allowDecimals={false} />
            <Tooltip
              cursor={{ fill: "#f1f5f9" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as { days: number; orders: number };
                return <div className="rounded-xl border border-white/80 bg-white/90 px-3 py-2 text-xs shadow-lg shadow-teal-950/10 ring-1 ring-slate-900/5 backdrop-blur"><b>{p.orders}</b> deliveries took {p.days} day{p.days === 1 ? "" : "s"}</div>;
              }}
            />
            <Bar dataKey="orders" fill={S1} radius={[4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false} />
            {quoted != null && <ReferenceLine x={quoted} stroke={MUTED} strokeDasharray="4 4" />}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Unit price paid per order over time for one item. */
export function PriceHistoryChart({ data, unit }: { data: { date: string; price: number }[]; unit: string }) {
  if (data.length < 2) return <p className="text-sm text-muted-foreground">Fewer than two orders — no price history.</p>;
  const short = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" });
  return (
    <div className="h-48" role="img" aria-label="Unit price paid per order">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="date" tickFormatter={short} tick={AXIS} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis domain={["auto", "auto"]} tick={AXIS} tickLine={false} axisLine={false} width={56} tickFormatter={(v) => `₹${formatNumber(v)}`} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload as { date: string; price: number };
              return <div className="rounded-xl border border-white/80 bg-white/90 px-3 py-2 text-xs shadow-lg shadow-teal-950/10 ring-1 ring-slate-900/5 backdrop-blur">{short(p.date)}: <b>₹{p.price.toFixed(2)}</b> per {unit}</div>;
            }}
          />
          <Line dataKey="price" type="stepAfter" stroke={S1} strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
