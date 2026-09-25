"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ClipboardList, Loader2, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { RankedBars } from "@/components/charts/charts";
import { BacktestChart, DriverBars, ForecastChart } from "@/components/charts/forecast-charts";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  Stat,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { ApiError, get, post } from "@/lib/api";
import type { ForecastOverview, ItemForecast, ModelComparison, ModelDetail, ModelVersion, TrainResult } from "@/lib/types";
import { formatDate, formatINR, formatNumber, timeAgo } from "@/lib/utils";

const pct = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
const MODEL_LABEL: Record<string, string> = {
  xgboost: "XGBoost",
  moving_average_7: "7-day moving average",
  historical_average: "Historical average",
  xgboost_procedure: "Procedure-aware XGBoost (V2B)",
};
const SOURCE_LABEL: Record<string, string> = {
  procedure_aware: "Procedure-aware (V2B)",
  consumption: "Consumption history (V2A)",
  baseline: "Baseline",
};
const FEATURE_LABEL: Record<string, string> = {
  lag_1: "Yesterday's consumption",
  lag_7: "Same weekday last week",
  lag_14: "Same weekday 2 weeks ago",
  roll_mean_7: "7-day average",
  roll_mean_14: "14-day average",
  roll_std_7: "7-day volatility",
  trend_7_14: "Recent trend",
  day_of_week: "Day of week",
  department: "Department",
  item: "Item",
  procedure_count: "Procedures scheduled that day",
  procedure_expected_quantity: "Kit-expected units that day",
  procedure_type_count: "Procedure types that day",
  procedure_department_count: "Departments with procedures",
  procedure_demand_share: "Procedure share of recent demand",
  procedure_expected_delta_7: "Kit units vs last week",
};

/* ------------------------------------------------------------------ model banner */

function ModelBanner({ model, stale, comparison }: { model: ModelVersion; stale: boolean; comparison: ModelComparison | null }) {
  return (
    <div className="mb-6 space-y-2 rounded-xl border bg-card px-4 py-3 text-sm">
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <span className="inline-flex items-center gap-2 font-medium">
        <CheckCircle2 className="size-4 text-emerald-600" /> Active model <Badge tone="blue" className="font-mono">{model.name}</Badge>
      </span>
      <span className="text-muted-foreground">{MODEL_LABEL[model.model_type]}</span>
      <span className="text-muted-foreground">Holdout WAPE <b className="text-foreground">{pct(model.metrics.wape)}</b></span>
      <span className="text-muted-foreground">Trained {timeAgo(model.trained_at)}</span>
      <span className="text-muted-foreground">Data through {formatDate(model.data_end)}</span>
      {stale && (
        <Badge tone="amber" className="gap-1"><AlertTriangle className="size-3" /> Newer stock data exists — retrain to refresh</Badge>
      )}
    </div>
    {comparison && (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-2 text-muted-foreground" data-testid="model-comparison">
        <span>Source <Badge tone={comparison.active_source === "procedure_aware" ? "violet" : "neutral"}>{SOURCE_LABEL[comparison.active_source]}</Badge></span>
        <span>V2A WAPE <b className="text-foreground">{pct(comparison.v2a_wape)}</b></span>
        <span>V2B WAPE <b className="text-foreground">{comparison.v2b_trained ? pct(comparison.v2b_wape) : "not trained"}</b></span>
        <span className="text-foreground">{comparison.summary}</span>
        {comparison.schedule_changed_since_training && (
          <Badge tone="amber" className="gap-1"><AlertTriangle className="size-3" /> Procedure data changed since training — retrain to use it</Badge>
        )}
      </div>
    )}
    </div>
  );
}

/* ------------------------------------------------------------------ V2B: procedure impact + stock */

function ProcedureImpactCard({ data, days }: { data: ItemForecast; days: number }) {
  const pi = data.procedure_impact;
  const cmp = data.model_comparison;
  return (
    <Card data-testid="procedure-impact">
      <CardHeader
        title="Procedure impact"
        description={`Scheduled procedures that use this item, next ${days} days`}
        action={<Link href="/procedures" className="inline-flex items-center gap-1 text-xs font-medium text-teal-700 hover:underline"><ClipboardList className="size-3" /> Procedures</Link>}
      />
      <CardBody className="space-y-4">
        {!pi || pi.scheduled_procedures === 0 ? (
          <p className="text-sm text-muted-foreground">
            No scheduled procedures use this item in the next {days} days{pi?.note ? ` — ${pi.note}` : ""}. The forecast relies on consumption history.
          </p>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              {[
                ["Scheduled procedures", formatNumber(pi.scheduled_procedures), "impact-count"],
                ["Kit-expected units", `${formatNumber(Math.round(pi.expected_quantity))} ${data.unit}`, "impact-qty"],
                ["V2A forecast", pi.v2a_forecast == null ? "—" : formatNumber(Math.round(pi.v2a_forecast)), "impact-v2a"],
                ["Procedure-aware forecast", pi.v2b_forecast == null ? "—" : formatNumber(Math.round(pi.v2b_forecast)), "impact-v2b"],
              ].map(([k, v, id]) => (
                <div key={k} className="rounded-lg border p-3">
                  <dt className="text-xs text-muted-foreground">{k}</dt>
                  <dd className="mt-0.5 font-semibold tabular-nums" data-testid={id}>{v}</dd>
                </div>
              ))}
            </dl>
            <Table>
              <THead>
                <tr>
                  <TH>Procedure</TH>
                  <TH>Department</TH>
                  <TH className="text-right">Count</TH>
                  <TH className="text-right">Per procedure</TH>
                  <TH className="text-right">Expected</TH>
                </tr>
              </THead>
              <tbody>
                {pi.types.map((t) => (
                  <TR key={t.procedure_type_id}>
                    <TD>{t.name}<div className="text-xs text-muted-foreground">{t.code}</div></TD>
                    <TD className="text-xs">{t.department}</TD>
                    <TD className="text-right tabular-nums">{t.count}</TD>
                    <TD className="text-right tabular-nums">{formatNumber(t.quantity_per_procedure)}</TD>
                    <TD className="text-right tabular-nums font-medium">{formatNumber(Math.round(t.expected_quantity))}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
            <p className="text-xs text-muted-foreground">{pi.note}</p>
          </>
        )}
        {cmp && (
          <p className="text-xs text-muted-foreground">
            Served by <b className="text-foreground">{SOURCE_LABEL[data.forecast_source]}</b> ({data.model_version}). {cmp.summary}
            {cmp.falls_back_to_v2a_from && ` Schedule known through ${formatDate(cmp.schedule_through!)}; later days use the V2A forecast.`}
          </p>
        )}
      </CardBody>
    </Card>
  );
}

function StockCoverCard({ data, days }: { data: ItemForecast; days: number }) {
  const short = data.expected_shortage > 0;
  return (
    <Card data-testid="stock-cover">
      <CardHeader title="Stock vs forecast" description={`Usable stock against the served forecast for the next ${days} days`} />
      <CardBody>
        <dl className="grid grid-cols-2 gap-3 text-sm">
          {[
            ["Current usable stock", `${formatNumber(data.usable_stock)} ${data.unit}`],
            ["Expected demand", `${formatNumber(data.predicted_demand)} ${data.unit}`],
            ["Expected shortage", short ? `${formatNumber(data.expected_shortage)} ${data.unit}` : "None"],
            ["Days of stock remaining", data.days_of_stock_remaining == null ? "> 30 days" : `${data.days_of_stock_remaining} days`],
          ].map(([k, v]) => (
            <div key={k} className="rounded-lg border p-3">
              <dt className="text-xs text-muted-foreground">{k}</dt>
              <dd className={`mt-0.5 font-semibold tabular-nums ${k === "Expected shortage" && short ? "text-red-600" : ""}`}>{v}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">
          {data.stock_covers_horizon
            ? `Usable stock covers the forecast for the next ${days} days.`
            : `The forecast exceeds usable stock within ${days} days. Planning signal only — not a stockout prediction.`}
        </p>
      </CardBody>
    </Card>
  );
}

/* ------------------------------------------------------------------ item forecast */

function ItemTab({ overview, itemId, onItem }: { overview: ForecastOverview; itemId: number | null; onItem: (id: number) => void }) {
  const [days, setDays] = useState(14);
  const { data, isLoading, error } = useQuery({
    queryKey: ["forecast", itemId, days],
    queryFn: () => get<ItemForecast>(`/forecasts/${itemId}`, { days }),
    enabled: !!itemId,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-72 flex-1">
          <label htmlFor="fc-item" className="mb-1.5 block text-sm font-medium text-slate-700">Item</label>
          <Select id="fc-item" value={itemId ?? ""} onChange={(e) => onItem(Number(e.target.value))}>
            {overview.items.map((i) => (
              <option key={i.consumable_id} value={i.consumable_id}>{i.name} ({i.sku})</option>
            ))}
          </Select>
        </div>
        <div>
          <span className="mb-1.5 block text-sm font-medium text-slate-700">Horizon</span>
          <div className="inline-flex rounded-lg border bg-card p-0.5" role="group" aria-label="Horizon">
            {[7, 14, 30].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                aria-pressed={days === d}
                className={`rounded-md px-3 py-1.5 text-sm font-medium ${days === d ? "bg-teal-700 text-white" : "text-muted-foreground hover:bg-muted"}`}
              >
                {d} days
              </button>
            ))}
          </div>
        </div>
      </div>

      {error ? (
        <Card><EmptyState title="No forecast for this item" description={error instanceof ApiError ? error.message : undefined} /></Card>
      ) : isLoading || !data ? (
        <Skeleton className="h-96" />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {data.horizons.filter((h) => [7, 14, 30].includes(h.days)).map((h) => (
              <Stat
                key={h.days}
                label={`Next ${h.days} days`}
                value={<span data-testid={`forecast-${h.days}`}>{formatNumber(Math.round(h.predicted))} <span className="text-base font-normal text-muted-foreground">{data.unit}</span></span>}
                sub={`range ${formatNumber(Math.round(h.lower))}–${formatNumber(Math.round(h.upper))}`}
                tone={h.days === days ? "green" : "default"}
              />
            ))}
            <Stat
              label="Usable stock cover"
              value={data.days_of_cover == null ? "—" : `${formatNumber(Math.round(data.days_of_cover))} days`}
              sub={`${formatNumber(data.usable_stock)} ${data.unit} on hand vs forecast rate`}
              tone={data.days_of_cover != null && data.days_of_cover < 7 ? "red" : data.days_of_cover != null && data.days_of_cover < 14 ? "amber" : "default"}
            />
          </div>

          <Card>
            <CardHeader
              title={`${data.item} — daily consumption (${data.unit})`}
              description="Last 60 days of actual consumption and the forecast for the next 30 days"
              action={<Link href={`/inventory/${data.item_id}`} className="text-xs font-medium text-teal-700 hover:underline">Open item</Link>}
            />
            <CardBody>
              <ForecastChart data={data} highlightDays={days} />
              <p className="mt-2 text-xs text-muted-foreground">Range: {data.interval}.</p>
            </CardBody>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader title="Why this forecast" description={`Plain-language explanation for the next ${days} days`} />
              <CardBody>
                <ul className="list-disc space-y-1.5 pl-5 text-sm" data-testid="forecast-explanation">
                  {data.explanation.map((e) => <li key={e}>{e}</li>)}
                </ul>
              </CardBody>
            </Card>
            <Card>
              <CardHeader
                title="What drives it"
                description={
                  data.drivers.length
                    ? `Feature contributions over ${data.drivers_horizon} days, relative to the model's baseline of ${formatNumber(Math.round(data.base_level ?? 0))} ${data.unit}`
                    : "Baseline model — no feature contributions"
                }
              />
              <CardBody>
                {data.drivers.length ? (
                  <DriverBars drivers={data.drivers} unit={data.unit} />
                ) : (
                  <p className="text-sm text-muted-foreground">The active model is a {MODEL_LABEL[data.model_type]?.toLowerCase()} and uses only recent consumption.</p>
                )}
              </CardBody>
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2"><ProcedureImpactCard data={data} days={days} /></div>
            <StockCoverCard data={data} days={days} />
          </div>

          {data.backtest && (
            <Card>
              <CardHeader
                title="How accurate was it? (backtest)"
                description={`Model trained without ${formatDate(data.backtest.test_start)} – ${formatDate(data.backtest.test_end)}, then scored on those days`}
              />
              <CardBody className="grid gap-6 lg:grid-cols-3">
                <div className="lg:col-span-2"><BacktestChart data={data.backtest.daily} unit={data.unit} /></div>
                <dl className="grid grid-cols-2 gap-3 text-sm">
                  {[
                    ["Actual", `${formatNumber(Math.round(data.backtest.actual_total))} ${data.unit}`],
                    ["Predicted", `${formatNumber(Math.round(data.backtest.predicted_total))} ${data.unit}`],
                    ["MAE / day", formatNumber(Number(data.backtest.mae.toFixed(1)))],
                    ["RMSE / day", formatNumber(Number(data.backtest.rmse.toFixed(1)))],
                    ["WAPE", pct(data.backtest.wape)],
                    ["Bias", pct(data.backtest.bias)],
                  ].map(([k, v]) => (
                    <div key={k} className="rounded-lg border p-3">
                      <dt className="text-xs text-muted-foreground">{k}</dt>
                      <dd className="mt-0.5 font-semibold tabular-nums">{v}</dd>
                    </div>
                  ))}
                </dl>
              </CardBody>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ all items */

function AllItemsTab({ onItem }: { onItem: (id: number) => void }) {
  const [days, setDays] = useState(14);
  const [search, setSearch] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["forecasts", "overview", days], queryFn: () => get<ForecastOverview>("/forecasts", { days }) });
  const rows = useMemo(() => {
    const s = search.toLowerCase();
    return (data?.items ?? []).filter((i) => !s || i.name.toLowerCase().includes(s) || i.sku.toLowerCase().includes(s));
  }, [data, search]);

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3 border-b p-4">
        <div className="relative min-w-56 flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input placeholder="Search item" className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <Select className="w-40" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Horizon">
          {[7, 14, 30].map((d) => <option key={d} value={d}>Next {d} days</option>)}
        </Select>
        {data && <span className="text-sm text-muted-foreground">Forecast value: <b className="text-foreground">{formatINR(data.total_value)}</b></span>}
      </div>
      {isLoading ? (
        <div className="space-y-2 p-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-9" />)}</div>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Item</TH>
              <TH className="text-right">Forecast ({days}d)</TH>
              <TH className="text-right">Range</TH>
              <TH className="text-right">Recent avg / day</TH>
              <TH className="text-right">Change</TH>
              <TH className="text-right">Usable stock</TH>
              <TH className="text-right">Cover</TH>
              <TH className="text-right">Procedures</TH>
              <TH className="text-right">Shortage</TH>
              <TH className="text-right">Backtest WAPE</TH>
            </tr>
          </THead>
          <tbody>
            {rows.map((r) => (
              <TR key={r.consumable_id} className="cursor-pointer" onClick={() => onItem(r.consumable_id)}>
                <TD>
                  <span className="font-medium text-teal-800 hover:underline">{r.name}</span>
                  <div className="text-xs text-muted-foreground">{r.sku}</div>
                </TD>
                <TD className="text-right tabular-nums font-medium">{formatNumber(Math.round(r.predicted_demand))} <span className="text-xs font-normal text-muted-foreground">{r.unit}</span></TD>
                <TD className="text-right tabular-nums text-xs text-muted-foreground">{formatNumber(Math.round(r.lower))}–{formatNumber(Math.round(r.upper))}</TD>
                <TD className="text-right tabular-nums">{formatNumber(Number(r.avg_daily_last_30.toFixed(1)))}</TD>
                <TD className="text-right tabular-nums">
                  {r.change_vs_recent == null ? "—" : `${r.change_vs_recent > 0 ? "▲" : r.change_vs_recent < 0 ? "▼" : ""} ${pct(Math.abs(r.change_vs_recent), 0)}`}
                </TD>
                <TD className="text-right tabular-nums">{formatNumber(r.usable_stock)}</TD>
                <TD className={`text-right tabular-nums ${r.days_of_cover != null && r.days_of_cover < 7 ? "font-semibold text-red-600" : r.days_of_cover != null && r.days_of_cover < 14 ? "text-amber-700" : ""}`}>
                  {r.days_of_cover == null ? "—" : `${formatNumber(Math.round(r.days_of_cover))} d`}
                </TD>
                <TD className="text-right tabular-nums" title={r.scheduled_procedures ? `${formatNumber(Math.round(r.procedure_driven_demand))} kit-expected units` : undefined}>
                  {r.scheduled_procedures || "—"}
                </TD>
                <TD className={`text-right tabular-nums ${r.expected_shortage > 0 ? "font-semibold text-red-600" : "text-muted-foreground"}`}>
                  {r.expected_shortage > 0 ? formatNumber(r.expected_shortage) : "—"}
                </TD>
                <TD className="text-right tabular-nums text-muted-foreground">{pct(r.backtest_wape, 0)}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ model evaluation */

function EvaluationTab({ active }: { active: ModelVersion }) {
  const [selected, setSelected] = useState(active.id);
  const models = useQuery({ queryKey: ["forecast-models"], queryFn: () => get<ModelVersion[]>("/forecasts/models") });
  const detail = useQuery({ queryKey: ["forecast-models", selected], queryFn: () => get<ModelDetail>(`/forecasts/models/${selected}`) });
  const [sortWorst, setSortWorst] = useState(true);
  const v2b = detail.data?.run_candidates.find((m) => m.model_type === "xgboost_procedure");
  const xgb =
    (v2b?.is_active ? v2b : undefined) ?? detail.data?.run_candidates.find((m) => m.model_type === "xgboost") ?? v2b;
  const folds = (v2b?.params?.validation_folds ?? []) as { test_start: string; test_end: string; v2a_wape: number; v2b_wape: number }[];
  const importance = Object.entries(xgb?.feature_importance ?? {})
    .map(([k, v]) => ({ name: FEATURE_LABEL[k] ?? k, value: v }))
    .sort((a, b) => b.value - a.value);
  const items = [...(detail.data?.items ?? [])].sort((a, b) =>
    sortWorst ? (b.wape ?? -1) - (a.wape ?? -1) : a.name.localeCompare(b.name),
  );

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Candidate comparison (same holdout)"
            description={detail.data ? `Trained on ${formatDate(detail.data.model.data_start)} – ${formatDate(detail.data.model.test_start)} (exclusive), scored on ${formatDate(detail.data.model.test_start)} – ${formatDate(detail.data.model.test_end)}. Lowest WAPE is served.` : undefined}
          />
          <Table data-testid="candidate-table">
            <THead>
              <tr>
                <TH>Model</TH>
                <TH className="text-right">MAE</TH>
                <TH className="text-right">RMSE</TH>
                <TH className="text-right">WAPE</TH>
                <TH className="text-right">Bias</TH>
                <TH>Status</TH>
              </tr>
            </THead>
            <tbody>
              {detail.data?.run_candidates.map((m) => (
                <TR key={m.id}>
                  <TD>
                    <span className="font-mono text-xs">{m.name}</span>
                    <div className="text-xs text-muted-foreground">{MODEL_LABEL[m.model_type]}</div>
                  </TD>
                  <TD className="text-right tabular-nums">{m.metrics.mae?.toFixed(2) ?? "—"}</TD>
                  <TD className="text-right tabular-nums">{m.metrics.rmse?.toFixed(2) ?? "—"}</TD>
                  <TD className="text-right tabular-nums font-semibold">{pct(m.metrics.wape)}</TD>
                  <TD className="text-right tabular-nums">{pct(m.metrics.bias)}</TD>
                  <TD>{m.is_active ? <Badge tone="green">Serving</Badge> : m.notes ? <Badge>{m.notes}</Badge> : <span className="text-xs text-muted-foreground">candidate</span>}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
          <p className="border-t px-5 py-3 text-xs text-muted-foreground">
            MAE/RMSE are per item-day in units; WAPE = Σ|error| ÷ Σ actual; bias &gt; 0 means over-forecasting. Stock-out days are excluded (demand not observed).
          </p>
        </Card>
        <Card>
          <CardHeader title="Feature importance" description={xgb ? `${xgb.name} · share of total gain` : "No XGBoost model in this run"} />
          <CardBody>
            {importance.length ? <RankedBars data={importance} formatter={(v) => pct(v, 0)} /> : <EmptyState title="—" />}
          </CardBody>
        </Card>
      </div>

      {v2b && folds.length > 0 && (
        <Card data-testid="v2b-folds">
          <CardHeader
            title="V2A vs V2B — rolling validation"
            description="V2B is served only if it beats V2A on the holdout and on every earlier 14-day window. Ties keep V2A."
          />
          <Table>
            <THead>
              <tr>
                <TH>Window</TH>
                <TH className="text-right">V2A WAPE</TH>
                <TH className="text-right">V2B WAPE</TH>
                <TH>Result</TH>
              </tr>
            </THead>
            <tbody>
              {folds.map((f, i) => (
                <TR key={f.test_start}>
                  <TD className="whitespace-nowrap text-xs">{formatDate(f.test_start)} – {formatDate(f.test_end)} {i === 0 && <Badge className="ml-1">holdout</Badge>}</TD>
                  <TD className="text-right tabular-nums">{pct(f.v2a_wape)}</TD>
                  <TD className="text-right tabular-nums">{pct(f.v2b_wape)}</TD>
                  <TD>{f.v2b_wape < f.v2a_wape ? <Badge tone="green">V2B better</Badge> : <Badge>V2A better or equal</Badge>}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Per-item holdout error"
          description={detail.data ? `${detail.data.model.name} · ${detail.data.items.length} items` : undefined}
          action={
            <Button size="sm" variant="ghost" onClick={() => setSortWorst(!sortWorst)}>
              Sort: {sortWorst ? "worst first" : "name"}
            </Button>
          }
        />
        <div className="max-h-[28rem] overflow-y-auto">
          <Table>
            <THead>
              <tr>
                <TH>Item</TH>
                <TH className="text-right">Actual</TH>
                <TH className="text-right">Predicted</TH>
                <TH className="text-right">Error</TH>
                <TH className="text-right">MAE</TH>
                <TH className="text-right">WAPE</TH>
                <TH className="text-right">Bias</TH>
              </tr>
            </THead>
            <tbody>
              {items.map((i) => (
                <TR key={i.consumable_id}>
                  <TD>{i.name}<div className="text-xs text-muted-foreground">{i.sku}</div></TD>
                  <TD className="text-right tabular-nums">{formatNumber(Math.round(i.actual_total))}</TD>
                  <TD className="text-right tabular-nums">{formatNumber(Math.round(i.predicted_total))}</TD>
                  <TD className="text-right tabular-nums">{formatNumber(Math.round(i.predicted_total - i.actual_total))}</TD>
                  <TD className="text-right tabular-nums">{i.mae.toFixed(1)}</TD>
                  <TD className={`text-right tabular-nums ${i.wape != null && i.wape > 0.4 ? "font-semibold text-red-600" : ""}`}>{pct(i.wape, 0)}</TD>
                  <TD className="text-right tabular-nums">{pct(i.bias, 0)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </div>
      </Card>

      <Card>
        <CardHeader title="Model registry" description="Every training run is stored with its data window, dataset hash, parameters and metrics" />
        <Table>
          <THead>
            <tr>
              <TH>Version</TH>
              <TH>Type</TH>
              <TH>Trained</TH>
              <TH>Data window</TH>
              <TH className="text-right">Series</TH>
              <TH className="text-right">WAPE</TH>
              <TH>Dataset</TH>
              <TH />
            </tr>
          </THead>
          <tbody>
            {models.data?.map((m) => (
              <TR key={m.id} className={m.id === selected ? "bg-teal-50/50" : ""}>
                <TD className="font-mono text-xs">{m.name} {m.is_active && <Badge tone="green" className="ml-1">active</Badge>}</TD>
                <TD className="text-xs">{MODEL_LABEL[m.model_type]}</TD>
                <TD className="whitespace-nowrap text-xs text-muted-foreground">{new Date(m.trained_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</TD>
                <TD className="whitespace-nowrap text-xs">{formatDate(m.data_start)} – {formatDate(m.data_end)}</TD>
                <TD className="text-right tabular-nums">{m.n_series}</TD>
                <TD className="text-right tabular-nums">{pct(m.metrics.wape)}</TD>
                <TD className="font-mono text-xs text-muted-foreground" title={m.dataset_hash}>{m.dataset_hash.slice(0, 10)}</TD>
                <TD className="text-right"><Button size="sm" variant="ghost" onClick={() => setSelected(m.id)}>Inspect</Button></TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ page */

function ForecastsInner() {
  const { can } = useMe();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = params.get("tab") ?? "item";
  const overview = useQuery({ queryKey: ["forecasts", "overview", 14], queryFn: () => get<ForecastOverview>("/forecasts", { days: 14 }) });

  const itemParam = Number(params.get("item")) || null;
  const itemId = itemParam ?? overview.data?.items[0]?.consumable_id ?? null;

  const setParams = (next: Record<string, string>) => {
    const p = new URLSearchParams(params.toString());
    Object.entries(next).forEach(([k, v]) => p.set(k, v));
    router.replace(`${pathname}?${p.toString()}`, { scroll: false });
  };

  const train = useAction(() => post<TrainResult>("/forecasts/train"), {
    invalidate: [["forecasts"], ["forecast"], ["forecast-models"], ["dashboard"]],
    success: (r) =>
      `Trained ${Object.keys(r.candidates).join(", ")} in ${r.seconds}s — serving ${r.active_model}` +
      (r.procedure_model?.trained ? (r.procedure_model.improved ? " (V2B improved on V2A)" : " (V2B did not improve on V2A — V2A kept)") : ""),
  });

  const model = overview.data?.model;
  return (
    <>
      <PageHeader
        title="Demand forecast"
        description="Demand forecasting per item — baselines, consumption XGBoost (V2A) and procedure-aware XGBoost (V2B), evaluated on held-out days. Operations planning only."
        actions={
          can(PERM.TRAIN_FORECASTS) && (
            <Button onClick={() => train.mutate()} disabled={train.isPending} variant={model ? "outline" : "default"}>
              {train.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} {model ? "Retrain models" : "Train models"}
            </Button>
          )
        }
      />
      {overview.isLoading ? (
        <Skeleton className="h-96" />
      ) : !model ? (
        <Card>
          <EmptyState
            title="No forecasting model yet"
            description={
              can(PERM.TRAIN_FORECASTS)
                ? "Train the models to generate forecasts. It needs at least 28 days of consumption history and takes a few seconds."
                : "Ask a procurement or inventory manager to train the forecasting models."
            }
          />
        </Card>
      ) : (
        <>
          <ModelBanner model={model} stale={overview.data!.is_stale} comparison={overview.data!.comparison} />
          <Tabs.Root value={tab} onValueChange={(v) => setParams({ tab: v })}>
            <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b">
              {[["item", "Item forecast"], ["all", "All items"], ["evaluation", "Model evaluation"]].map(([k, label]) => (
                <Tabs.Trigger
                  key={k}
                  value={k}
                  className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800"
                >
                  {label}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            <Tabs.Content value="item">
              <ItemTab overview={overview.data!} itemId={itemId} onItem={(id) => setParams({ item: String(id) })} />
            </Tabs.Content>
            <Tabs.Content value="all">
              <AllItemsTab onItem={(id) => setParams({ item: String(id), tab: "item" })} />
            </Tabs.Content>
            <Tabs.Content value="evaluation">
              <EvaluationTab key={model.id} active={model} />
            </Tabs.Content>
          </Tabs.Root>
        </>
      )}
    </>
  );
}

export default function ForecastsPage() {
  return (
    <Suspense>
      <ForecastsInner />
    </Suspense>
  );
}
