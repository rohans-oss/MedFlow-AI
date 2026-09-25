"use client";

import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ItemForecast } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

// Categorical slots 1 and 2 of the validated reference palette (dataviz skill).
const SERIES_1 = "#2a78d6";
const SERIES_2 = "#eb6834";
const SERIES_3 = "#1baf7a"; // slot 3 (aqua): scheduled-procedure kit demand (V2B)
const MUTED = "#94a3b8";
const GRID = "#e2e8f0";
const AXIS = { fontSize: 11, fill: "#64748b" };

const shortDate = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

function LegendItem({ kind, label }: { kind: "solid" | "dashed" | "band" | "dot" | "v2a" | "kit"; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      {kind === "solid" && <span className="h-0.5 w-5 rounded" style={{ background: SERIES_1 }} />}
      {kind === "dashed" && <span className="w-5 border-t-2 border-dashed" style={{ borderColor: SERIES_1 }} />}
      {kind === "band" && <span className="h-3 w-5 rounded-sm" style={{ background: SERIES_1, opacity: 0.15 }} />}
      {kind === "dot" && <span className="size-2 rounded-full" style={{ background: MUTED }} />}
      {kind === "v2a" && <span className="w-5 border-t-2 border-dotted" style={{ borderColor: SERIES_2 }} />}
      {kind === "kit" && <span className="h-3 w-2 rounded-sm" style={{ background: SERIES_3, opacity: 0.55 }} />}
      {label}
    </span>
  );
}

type Point = {
  date: string;
  actual?: number | null;
  stockout?: number | null;
  forecast?: number | null;
  band?: [number, number] | null;
  v2a?: number | null; // V2B: consumption-only (V2A) forecast when the procedure-aware model is served
  kit?: number | null; // V2B: units implied by scheduled procedures × mapped kit quantities
};

/** Past consumption (solid) → future forecast (dashed) with an approximate 80% range band. */
export function ForecastChart({ data, highlightDays }: { data: ItemForecast; highlightDays: number }) {
  const hist: Point[] = data.history.map((h) => ({
    date: h.date,
    actual: h.censored ? null : h.actual,
    stockout: h.censored ? 0 : null,
  }));
  // join the lines: the forecast starts from the last actual point
  const lastKnown = [...data.history].reverse().find((h) => !h.censored);
  if (hist.length && lastKnown) hist[hist.length - 1].forecast = lastKnown.actual;
  const v2a = new Map((data.comparison_daily ?? []).map((d) => [d.date, d.predicted]));
  const kit = data.procedure_impact?.daily ?? {};
  const fut: Point[] = data.daily.map((d) => ({
    date: d.date,
    forecast: d.predicted,
    band: [d.lower, d.upper],
    v2a: v2a.get(d.date) ?? null,
    kit: kit[d.date] ? kit[d.date] : null,
  }));
  if (hist.length && lastKnown && v2a.size) hist[hist.length - 1].v2a = lastKnown.actual;
  const points = [...hist, ...fut];
  const hasV2a = v2a.size > 0;
  const hasKit = Object.values(kit).some((v) => v > 0);
  const horizonEnd = data.daily[Math.min(highlightDays, data.daily.length) - 1]?.date;
  const hasStockout = data.history.some((h) => h.censored);

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4">
        <LegendItem kind="solid" label="Actual consumption" />
        <LegendItem kind="dashed" label={`${hasV2a ? "Final forecast" : "Forecast"} (${data.model_version})`} />
        <LegendItem kind="band" label="Approx. 80% range" />
        {hasV2a && <LegendItem kind="v2a" label="V2A consumption-only forecast" />}
        {hasKit && <LegendItem kind="kit" label={`Scheduled procedure kits (next ${highlightDays}d)`} />}
        {hasStockout && <LegendItem kind="dot" label="Stocked out — not true demand" />}
      </div>
      <div className="h-72" role="img" aria-label={`Daily consumption of ${data.item}: last 60 days and ${data.daily.length}-day forecast`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} tickLine={false} axisLine={false} minTickGap={28} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={48} allowDecimals={false} />
            <Tooltip
              cursor={{ stroke: MUTED, strokeDasharray: "3 3" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as Point;
                const isFuture = p.band != null;
                return (
                  <div className="rounded-lg border bg-white px-3 py-2 text-xs shadow-md">
                    <div className="text-muted-foreground">{shortDate(p.date)}</div>
                    {isFuture ? (
                      <>
                        <div className="mt-0.5 font-semibold">{formatNumber(Math.round(p.forecast ?? 0))} {data.unit} forecast</div>
                        <div className="text-muted-foreground">range {formatNumber(Math.round(p.band![0]))}–{formatNumber(Math.round(p.band![1]))}</div>
                        {p.v2a != null && <div className="text-muted-foreground">V2A (consumption only): {formatNumber(Math.round(p.v2a))}</div>}
                        {p.kit != null && <div className="text-muted-foreground">Scheduled procedure kits: {formatNumber(Math.round(p.kit))}</div>}
                      </>
                    ) : p.stockout != null ? (
                      <div className="mt-0.5 font-medium">Stocked out — demand not observed</div>
                    ) : (
                      <div className="mt-0.5 font-semibold">{formatNumber(p.actual ?? 0)} {data.unit} consumed</div>
                    )}
                  </div>
                );
              }}
            />
            <Area dataKey="band" stroke="none" fill={SERIES_1} fillOpacity={0.12} isAnimationActive={false} connectNulls={false} />
            {hasKit && <Bar dataKey="kit" fill={SERIES_3} fillOpacity={0.45} barSize={6} isAnimationActive={false} />}
            {hasV2a && <Line dataKey="v2a" stroke={SERIES_2} strokeWidth={1.5} strokeDasharray="2 3" dot={false} isAnimationActive={false} />}
            <Line dataKey="actual" stroke={SERIES_1} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
            <Line dataKey="forecast" stroke={SERIES_1} strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
            <Scatter dataKey="stockout" fill={MUTED} isAnimationActive={false} />
            <ReferenceLine x={data.data_through} stroke={MUTED} strokeDasharray="2 3" label={{ value: "Data through", position: "insideTopLeft", fontSize: 10, fill: "#64748b" }} />
            {horizonEnd && (
              <ReferenceLine x={horizonEnd} stroke={MUTED} label={{ value: `+${highlightDays}d`, position: "insideTopRight", fontSize: 10, fill: "#64748b" }} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Holdout: what the model predicted vs what actually happened. */
export function BacktestChart({ data, unit }: { data: { date: string; actual: number | null; predicted: number }[]; unit: string }) {
  return (
    <div>
      <div className="mb-2 flex gap-4">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="h-0.5 w-5 rounded" style={{ background: SERIES_1 }} /> Actual
        </span>
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="w-5 border-t-2 border-dashed" style={{ borderColor: SERIES_2 }} /> Predicted (trained without these days)
        </span>
      </div>
      <div className="h-44" role="img" aria-label="Backtest: actual vs predicted daily consumption on the holdout period">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} tickLine={false} axisLine={false} minTickGap={24} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} allowDecimals={false} />
            <Tooltip
              content={({ active, payload }) =>
                active && payload?.length ? (
                  <div className="rounded-lg border bg-white px-3 py-2 text-xs shadow-md">
                    <div className="text-muted-foreground">{shortDate(payload[0].payload.date)}</div>
                    <div>Actual: <b>{payload[0].payload.actual == null ? "stocked out" : `${formatNumber(payload[0].payload.actual)} ${unit}`}</b></div>
                    <div>Predicted: <b>{formatNumber(Math.round(payload[0].payload.predicted))} {unit}</b></div>
                  </div>
                ) : null
              }
            />
            <Line dataKey="actual" stroke={SERIES_1} strokeWidth={2} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />
            <Line dataKey="predicted" stroke={SERIES_2} strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Diverging contributions: what moved the forecast above/below the model's baseline level. */
export function DriverBars({ drivers, unit }: { drivers: { label: string; units: number }[]; unit: string }) {
  const max = Math.max(1, ...drivers.map((d) => Math.abs(d.units)));
  return (
    <ul className="space-y-2">
      {drivers.map((d) => (
        <li key={d.label} className="grid grid-cols-[minmax(0,11rem)_1fr_4.5rem] items-center gap-3 text-xs">
          <span className="truncate text-slate-700" title={d.label}>{d.label}</span>
          <div className="relative h-2.5 rounded bg-slate-100">
            <div className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
            <div
              className="absolute inset-y-0 rounded"
              style={{
                background: d.units >= 0 ? SERIES_1 : SERIES_2,
                left: d.units >= 0 ? "50%" : `${50 - (Math.abs(d.units) / max) * 50}%`,
                width: `${(Math.abs(d.units) / max) * 50}%`,
              }}
            />
          </div>
          <span className="text-right font-medium tabular-nums">
            {d.units > 0 ? "+" : ""}
            {formatNumber(Math.round(d.units) || 0)} <span className="font-normal text-muted-foreground">{unit}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
