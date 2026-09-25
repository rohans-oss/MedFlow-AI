"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { RiskDetail, RiskModel } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

// Validated categorical slots 1–3 (dataviz validator: all checks pass; slot 3 needs labels/table → legend + metrics table).
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a"];
const MUTED = "#94a3b8";
const GRID = "#e2e8f0";
const AXIS = { fontSize: 11, fill: "#64748b" };
const shortDate = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
const addDays = (d: string, n: number) => {
  const x = new Date(`${d}T00:00:00Z`);
  x.setUTCDate(x.getUTCDate() + n);
  return x.toISOString().slice(0, 10);
};

/** Projected usable stock (no deliveries assumed) against the served forecast, with the decision dates marked. */
export function ProjectionChart({ data }: { data: RiskDetail }) {
  const points = data.projection.map((p) => ({ ...p, stock: p.stock_end }));
  const start = data.projection[0]?.date;
  const arrival = start && data.lead_time_days != null ? addDays(start, data.lead_time_days) : null;
  const inRange = (d: string | null) => !!d && points.some((p) => p.date === d);
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-5 rounded" style={{ background: SERIES[0] }} />Projected usable stock ({data.unit}), no deliveries</span>
        <span className="inline-flex items-center gap-1.5"><span className="w-5 border-t-2 border-dashed" style={{ borderColor: MUTED }} />Reorder level</span>
      </div>
      <div className="h-64" role="img" aria-label={`Projected usable stock of ${data.name} for the next ${points.length} days`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 16, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} tickLine={false} axisLine={false} minTickGap={28} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={52} allowDecimals={false} />
            <Tooltip
              cursor={{ stroke: MUTED, strokeDasharray: "3 3" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as (typeof points)[number];
                return (
                  <div className="rounded-lg border bg-white px-3 py-2 text-xs shadow-md">
                    <div className="text-muted-foreground">{shortDate(p.date)}</div>
                    <div className="mt-0.5 font-semibold">{formatNumber(Math.round(p.stock_end))} {data.unit} left</div>
                    <div className="text-muted-foreground">forecast demand {formatNumber(Math.round(p.demand))}</div>
                    {p.unmet > 0 && <div className="font-medium text-red-700">{formatNumber(Math.round(p.unmet))} {data.unit} short</div>}
                    {p.expired > 0 && <div className="text-muted-foreground">{formatNumber(Math.round(p.expired))} expired</div>}
                  </div>
                );
              }}
            />
            <Area dataKey="stock" stroke={SERIES[0]} strokeWidth={2} fill={SERIES[0]} fillOpacity={0.1} isAnimationActive={false} />
            {data.reorder_level > 0 && <ReferenceLine y={data.reorder_level} stroke={MUTED} strokeDasharray="4 4" />}
            {inRange(data.expected_stockout_date) && (
              <ReferenceLine x={data.expected_stockout_date!} stroke="#d03b3b" strokeDasharray="2 3"
                label={{ value: "Projected stockout", position: "insideTopRight", fontSize: 10, fill: "#475569" }} />
            )}
            {arrival && inRange(arrival) && (
              <ReferenceLine x={arrival} stroke={MUTED}
                label={{ value: "Delivery if ordered today", position: "insideBottomLeft", fontSize: 10, fill: "#475569" }} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

const MODEL_LABEL: Record<string, string> = {
  xgboost_classifier: "XGBoost risk model",
  cover_rule: "Cover rule (V3.1)",
  reorder_rule: "Reorder-level rule (V1)",
};

/** Precision–recall on the ledger backtest, one line per scorer (same rows, same labels). */
export function PrCurveChart({ models }: { models: RiskModel[] }) {
  const order = ["xgboost_classifier", "cover_rule", "reorder_rule"];
  const series = order
    .map((t) => models.find((m) => m.model_type === t))
    .filter((m): m is RiskModel => !!m && !!m.evaluation?.pr_curve.length)
    .map((m, i) => ({
      m,
      color: SERIES[i],
      points: [...m.evaluation!.pr_curve].filter((p) => p.recall != null && p.precision != null)
        .sort((a, b) => (a.recall ?? 0) - (b.recall ?? 0))
        .map((p) => ({ recall: p.recall!, precision: p.precision!, threshold: p.threshold })),
    }));
  if (!series.length) return <p className="text-sm text-muted-foreground">No stockouts in the backtest window — no curve to draw.</p>;
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        {series.map((s) => (
          <span key={s.m.id} className="inline-flex items-center gap-1.5">
            <span className="h-0.5 w-5 rounded" style={{ background: s.color }} />
            {MODEL_LABEL[s.m.model_type]} · PR-AUC {s.m.metrics.backtest.pr_auc?.toFixed(2) ?? "—"}
          </span>
        ))}
      </div>
      <div className="h-64" role="img" aria-label="Precision versus recall on the hospital's own history for each risk scorer">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart margin={{ top: 8, right: 16, left: 0, bottom: 12 }}>
            <CartesianGrid stroke={GRID} />
            <XAxis type="number" dataKey="recall" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} tick={AXIS}
              tickLine={false} axisLine={false} label={{ value: "Recall (stockouts caught)", position: "insideBottom", offset: -8, fontSize: 11, fill: "#64748b" }} />
            <YAxis type="number" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} tick={AXIS} tickLine={false} axisLine={false} width={44} />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                return (
                  <div className="rounded-lg border bg-white px-3 py-2 text-xs shadow-md">
                    {payload.map((p) => {
                      const d = p.payload as { recall: number; precision: number; threshold: number };
                      return (
                        <div key={String(p.name)} className="flex items-center gap-2">
                          <span className="size-2 rounded-full" style={{ background: p.color }} />
                          <span className="text-muted-foreground">{p.name}:</span>
                          <span className="font-medium">precision {Math.round(d.precision * 100)}% · recall {Math.round(d.recall * 100)}%</span>
                        </div>
                      );
                    })}
                  </div>
                );
              }}
            />
            {series.map((s) => (
              <Line key={s.m.id} data={s.points} dataKey="precision" name={MODEL_LABEL[s.m.model_type]} stroke={s.color}
                strokeWidth={2} dot={{ r: 2 }} activeDot={{ r: 4 }} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export { MODEL_LABEL as RISK_MODEL_LABEL };
