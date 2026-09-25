"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { DailyPoint, NamedCount } from "@/lib/types";
import { formatCompactINR, formatINR, formatNumber } from "@/lib/utils";

// Categorical slot 1 of the validated reference palette (see dataviz skill). Single-series charts only,
// so the chart title names the series and no legend box is needed.
const SERIES_1 = "#2a78d6";
const GRID = "#e2e8f0";
const AXIS = { fontSize: 11, fill: "#64748b" };

const shortDate = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

function TooltipBox({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-lg border bg-white px-3 py-2 text-xs shadow-md">
      <div className="text-muted-foreground">{title}</div>
      <div className="mt-0.5 font-semibold text-foreground">{value}</div>
    </div>
  );
}

/** Daily consumption value (₹) — hospital level, where units are not comparable across items. */
export function ConsumptionValueChart({ data }: { data: DailyPoint[] }) {
  return (
    <div className="h-64" role="img" aria-label="Daily consumption value over the last 30 days">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="fillIssued" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={SERIES_1} stopOpacity={0.18} />
              <stop offset="100%" stopColor={SERIES_1} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis tickFormatter={(v) => formatCompactINR(v)} tick={AXIS} tickLine={false} axisLine={false} width={64} />
          <Tooltip
            cursor={{ stroke: "#94a3b8", strokeDasharray: "3 3" }}
            content={({ active, payload }) =>
              active && payload?.length ? (
                <TooltipBox title={shortDate(payload[0].payload.date)} value={`${formatINR(payload[0].payload.issued_value)} issued`} />
              ) : null
            }
          />
          <Area type="monotone" dataKey="issued_value" stroke={SERIES_1} strokeWidth={2} fill="url(#fillIssued)" activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff" }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Daily units issued for one item. */
export function DailyIssueChart({ data, unit }: { data: DailyPoint[]; unit: string }) {
  return (
    <div className="h-56" role="img" aria-label="Units issued per day over the last 30 days">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} width={48} allowDecimals={false} />
          <Tooltip
            cursor={{ fill: "#f1f5f9" }}
            content={({ active, payload }) =>
              active && payload?.length ? (
                <TooltipBox title={shortDate(payload[0].payload.date)} value={`${formatNumber(payload[0].payload.issued)} ${unit} issued`} />
              ) : null
            }
          />
          <Bar dataKey="issued" fill={SERIES_1} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal ranked bars (single series). */
export function RankedBars({
  data,
  money = false,
  unitLabel = "",
  formatter,
}: {
  data: NamedCount[];
  money?: boolean;
  unitLabel?: string;
  formatter?: (v: number) => string;
}) {
  const max = Math.max(Number.EPSILON, ...data.map((d) => d.value));
  const fmt = formatter ?? ((v: number) => (money ? formatCompactINR(v) : formatNumber(v)));
  return (
    <ul className="space-y-2.5">
      {data.map((d) => (
        <li key={d.name} className="group" title={`${d.name}: ${formatter ? formatter(d.value) : money ? formatINR(d.value) : formatNumber(d.value)} ${unitLabel}`}>
          <div className="mb-1 flex justify-between gap-3 text-xs">
            <span className="truncate text-slate-700">{d.name}</span>
            <span className="shrink-0 font-medium tabular-nums">{fmt(d.value)}</span>
          </div>
          <div className="h-2 rounded bg-slate-100">
            <div className="h-2 rounded group-hover:opacity-80" style={{ width: `${(d.value / max) * 100}%`, background: SERIES_1 }} />
          </div>
        </li>
      ))}
    </ul>
  );
}
