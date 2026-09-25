"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, AlertTriangle, CheckCircle2, FlaskConical, Loader2, RefreshCw, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { RiskBadge } from "@/components/badges";
import { RankedBars } from "@/components/charts/charts";
import { PrCurveChart, ProjectionChart, RISK_MODEL_LABEL } from "@/components/charts/risk-charts";
import { ItemSupplierOptionsView } from "@/components/supplier-options";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Input,
  PageHeader,
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
import type { RiskDetail, RiskItem, RiskLevel, RiskMetrics, RiskModelDetail, RiskOverview, RiskTrainResult } from "@/lib/types";
import { formatDate, formatNumber, timeAgo } from "@/lib/utils";

const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const RISK_KEYS = [["stockout-risks"], ["alerts"], ["dashboard"]];
const FEATURE_LABEL: Record<string, string> = {
  usable_stock: "Current usable stock", forecast_7: "7-day demand", forecast_14: "14-day demand", forecast_30: "30-day demand",
  stock_to_forecast_7: "Stock vs 7-day demand", stock_to_forecast_14: "Stock vs 14-day demand", stock_to_forecast_30: "Stock vs 30-day demand",
  recent_mean_7: "7-day avg consumption", recent_mean_28: "28-day avg consumption", trend_7_28: "Consumption trend",
  volatility_28: "Demand volatility", procedure_demand_14: "Procedure demand (14 d)", procedure_share_14: "Procedure share",
  lead_time_days: "Supplier lead time", stock_to_reorder: "Stock vs reorder level", projected_cover_days: "Projected cover",
  cover_minus_lead_time: "Cover minus lead time", expiring_14: "Expiring within 14 d", expiring_share_14: "Share expiring soon",
  stockout_days_60: "Stockout days (60 d)", days_since_stockout: "Days since stockout", days_since_receipt: "Days since delivery",
  department: "Main department",
};

/* ------------------------------------------------------------------ item detail */

function ItemPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["stockout-risks", "item", id], queryFn: () => get<RiskDetail>(`/stockout-risks/${id}`) });
  if (error) return <Card className="mb-6"><EmptyState title="No risk for this item" description={error instanceof ApiError ? error.message : undefined} /></Card>;
  if (isLoading || !data) return <Skeleton className="mb-6 h-96" />;
  const u = data.unit;
  const facts: [string, React.ReactNode][] = [
    ["Probability (14 days)", <span key="p" data-testid="detail-probability">{pct(data.probability)}</span>],
    ["Usable stock", `${formatNumber(data.usable_stock)} ${u}`],
    ["Expected 14-day demand", `${formatNumber(Math.round(data.forecast_14))} ${u}`],
    ["Days of stock remaining", data.days_of_stock_remaining == null ? "> 30" : `${data.days_of_stock_remaining}`],
    ["Expected stockout", data.expected_stockout_date ? formatDate(data.expected_stockout_date) : "Not within 30 days"],
    ["Shortage (14 days)", data.shortage_quantity ? `${formatNumber(data.shortage_quantity)} ${u}` : "None"],
    ["Supplier lead time", data.lead_time_days == null ? "Unknown" : `${data.lead_time_days} days`],
    ["Order by", data.order_by_date ? <span key="o" className={data.order_overdue ? "text-red-600" : ""}>{formatDate(data.order_by_date)}{data.order_overdue ? " (passed)" : ""}</span> : "—"],
  ];
  return (
    <Card className="mb-6" data-testid="risk-detail">
      <CardHeader
        title={<span className="flex items-center gap-2">{data.name} <RiskBadge level={data.risk_level} out={data.out_of_stock} /></span>}
        description={`${data.sku} · risk model ${data.risk_model ?? "—"} · forecast ${data.forecast_model ?? "—"} · updated ${timeAgo(data.updated_at)}`}
        action={
          <div className="flex items-center gap-2">
            <Link href={`/forecasts?item=${data.consumable_id}`} className="text-xs font-medium text-teal-700 hover:underline">Forecast</Link>
            <Link href={`/inventory/${data.consumable_id}`} className="text-xs font-medium text-teal-700 hover:underline">Item</Link>
            <Link href={`/knowledge-graph?item=${data.consumable_id}`} className="text-xs font-medium text-teal-700 hover:underline">Explain in graph</Link>
            <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close detail"><X /></Button>
          </div>
        }
      />
      <CardBody className="space-y-6">
        <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
          {facts.map(([k, v]) => (
            <div key={k} className="rounded-lg border p-3">
              <dt className="text-xs text-muted-foreground">{k}</dt>
              <dd className="mt-0.5 font-semibold tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
        <div className="grid gap-6 lg:grid-cols-5">
          <div className="lg:col-span-3"><ProjectionChart data={data} /></div>
          <div className="lg:col-span-2">
            <h4 className="mb-2 text-sm font-semibold">Why</h4>
            <ul className="list-disc space-y-1.5 pl-5 text-sm" data-testid="risk-reasons">
              {data.reasons.map((r) => <li key={r}>{r}</li>)}
            </ul>
          </div>
        </div>
        {data.drivers.length > 0 && (
          <div>
            <h4 className="mb-2 text-sm font-semibold">Model factors <span className="font-normal text-muted-foreground">(SHAP, log-odds; ↑ raises risk)</span></h4>
            <Table>
              <THead><tr><TH>Factor</TH><TH className="text-right">Value</TH><TH className="text-right">Effect</TH></tr></THead>
              <tbody>
                {data.drivers.map((d) => (
                  <TR key={d.feature}>
                    <TD>{d.label}</TD>
                    <TD className="text-right tabular-nums">{d.value == null ? "—" : formatNumber(Number(d.value.toFixed(2)))}</TD>
                    <TD className={`text-right tabular-nums ${d.contribution > 0 ? "text-red-700" : "text-emerald-700"}`}>
                      {d.contribution > 0 ? "↑" : "↓"} {Math.abs(d.contribution).toFixed(2)}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          </div>
        )}
        <div data-testid="risk-supplier-options">
          <h4 className="mb-2 text-sm font-semibold">Supplier options <span className="font-normal text-muted-foreground">(V4 · delivery history)</span></h4>
          <ItemSupplierOptionsView itemId={data.consumable_id} compact />
        </div>
        <p className="text-xs text-muted-foreground">
          Projection assumes no new deliveries (orders in transit are counted on the Procurement page, weighted by their arrival probability) and uses the served demand forecast.
          Probability thresholds: medium ≥ {pct(data.warn_threshold)}, high ≥ {pct(data.high_threshold)}.
        </p>
      </CardBody>
    </Card>
  );
}

/* ------------------------------------------------------------------ risk list */

function RisksTab({ data, selected, onSelect }: { data: RiskOverview; selected: number | null; onSelect: (id: number | null) => void }) {
  const [level, setLevel] = useState<RiskLevel | "ALL">("ALL");
  const [search, setSearch] = useState("");
  const rows = useMemo(() => {
    const s = search.toLowerCase();
    return data.items.filter((i) => (level === "ALL" || i.risk_level === level) && (!s || i.name.toLowerCase().includes(s) || i.sku.toLowerCase().includes(s)));
  }, [data, level, search]);
  const c = data.counts;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="High risk" icon={<AlertTriangle />} tone={c.high ? "red" : "default"} value={<span data-testid="count-high">{c.high}</span>} sub={`${c.out_of_stock} already out of stock`} />
        <Stat label="Medium risk" icon={<AlertCircle />} tone={c.medium ? "amber" : "default"} value={<span data-testid="count-medium">{c.medium}</span>} sub="worth reviewing" />
        <Stat label="Low risk" icon={<CheckCircle2 />} tone="green" value={<span data-testid="count-low">{c.low}</span>} sub={`of ${c.total} active items`} />
        <Stat label="Risk horizon" value={`${data.horizon_days} days`} sub={data.as_of ? `as of ${formatDate(data.as_of)}` : undefined} />
      </div>
      {selected != null && <ItemPanel key={selected} id={selected} onClose={() => onSelect(null)} />}
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <div className="relative min-w-56 flex-1">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input placeholder="Search item" className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="inline-flex rounded-lg border bg-card p-0.5" role="group" aria-label="Risk level">
            {(["ALL", "HIGH", "MEDIUM", "LOW"] as const).map((l) => (
              <button key={l} onClick={() => setLevel(l)} aria-pressed={level === l}
                className={`rounded-md px-3 py-1.5 text-sm font-medium ${level === l ? "bg-teal-700 text-white" : "text-muted-foreground hover:bg-muted"}`}>
                {l === "ALL" ? "All" : l.charAt(0) + l.slice(1).toLowerCase()}
              </button>
            ))}
          </div>
        </div>
        {!rows.length ? (
          <EmptyState title="No items at this risk level" />
        ) : (
          <Table data-testid="risk-table">
            <THead>
              <tr>
                <TH>Item</TH>
                <TH>Risk</TH>
                <TH className="text-right">Probability</TH>
                <TH>Expected stockout</TH>
                <TH className="text-right">Shortage (14 d)</TH>
                <TH className="text-right">Stock / 14-d demand</TH>
                <TH className="text-right">Lead time</TH>
                <TH>Order by</TH>
                <TH>Reason</TH>
              </tr>
            </THead>
            <tbody>
              {rows.map((r: RiskItem) => (
                <TR key={r.consumable_id} className={`cursor-pointer ${selected === r.consumable_id ? "bg-teal-50/60" : ""}`} onClick={() => onSelect(r.consumable_id)}>
                  <TD className="min-w-56">
                    <span className="font-medium text-teal-800 hover:underline">{r.name}</span>
                    <div className="text-xs text-muted-foreground">{r.sku}</div>
                  </TD>
                  <TD><RiskBadge level={r.risk_level} out={r.out_of_stock} /></TD>
                  <TD className="text-right font-semibold tabular-nums">{pct(r.probability)}</TD>
                  <TD className="whitespace-nowrap">
                    {r.out_of_stock ? <span className="text-red-600">Now</span> : r.expected_stockout_date ? (
                      <>{formatDate(r.expected_stockout_date)}<div className="text-xs text-muted-foreground">{r.days_of_stock_remaining} days left</div></>
                    ) : <span className="text-muted-foreground">Beyond 30 days</span>}
                  </TD>
                  <TD className={`text-right tabular-nums ${r.shortage_quantity ? "font-medium text-red-700" : "text-muted-foreground"}`}>
                    {r.shortage_quantity ? `${formatNumber(r.shortage_quantity)} ${r.unit}` : "—"}
                  </TD>
                  <TD className="text-right tabular-nums text-xs">{formatNumber(r.usable_stock)} / {formatNumber(Math.round(r.forecast_14))}</TD>
                  <TD className="text-right tabular-nums">{r.lead_time_days == null ? "—" : `${r.lead_time_days} d`}</TD>
                  <TD className={`whitespace-nowrap text-xs ${r.order_overdue && !r.out_of_stock ? "font-medium text-red-600" : ""}`}>
                    {r.out_of_stock ? "Now" : r.order_by_date ? `${formatDate(r.order_by_date)}${r.order_overdue ? " (passed)" : ""}` : "—"}
                  </TD>
                  <TD className="max-w-72 truncate text-xs text-muted-foreground" title={r.main_reason}>{r.main_reason}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ evaluation */

const METRIC_ROWS: [keyof RiskMetrics, string, (v: number | null) => string][] = [
  ["precision", "Precision", (v) => pct(v)],
  ["recall", "Recall (stockout-days caught)", (v) => pct(v)],
  ["f1", "F1", (v) => (v == null ? "—" : v.toFixed(2))],
  ["pr_auc", "PR-AUC", (v) => (v == null ? "—" : v.toFixed(2))],
  ["fp", "False positives (needless warnings)", (v) => (v == null ? "—" : formatNumber(v))],
  ["fn", "False negatives (missed)", (v) => (v == null ? "—" : formatNumber(v))],
  ["brier", "Brier score (lower = better calibrated)", (v) => (v == null ? "—" : v.toFixed(3))],
  ["event_recall", "Stockout events warned about", (v) => pct(v)],
  ["lead_time_recall", "…warned ≥ lead time ahead", (v) => pct(v)],
  ["median_warning_days", "Median warning (days before)", (v) => (v == null ? "—" : `${v}`)],
];

function EvaluationTab({ modelId }: { modelId: number }) {
  const { data } = useQuery({ queryKey: ["stockout-risks", "model", modelId], queryFn: () => get<RiskModelDetail>(`/stockout-risks/models/${modelId}`) });
  if (!data) return <Skeleton className="h-96" />;
  const order = ["xgboost_classifier", "cover_rule", "reorder_rule"];
  const cands = order.map((t) => data.run_candidates.find((m) => m.model_type === t)).filter((m) => !!m);
  const served = data.run_candidates.find((m) => m.is_active) ?? data.model;
  const xgb = data.run_candidates.find((m) => m.model_type === "xgboost_classifier");
  const bt = served.metrics.backtest;
  const floor = served.metrics.backtest_with_floor;
  const importance = Object.entries(xgb?.feature_importance ?? {}).map(([k, v]) => ({ name: FEATURE_LABEL[k] ?? k, value: v }))
    .sort((a, b) => b.value - a.value).slice(0, 10);
  const sim = (served.params?.simulation ?? {}) as Record<string, unknown>;
  return (
    <div className="space-y-6">
      <div className="flex items-start gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm text-violet-900">
        <FlaskConical className="mt-0.5 size-4 shrink-0" />
        <span>
          The risk model is <b>trained on simulated histories</b> (synthetic replays of each item&apos;s demand, reorder policy,
          supplier delays and expiry, {formatNumber(served.n_train_rows)} rows). It is <b>evaluated on this hospital&apos;s own ledger</b>{" "}
          ({served.eval_start ? formatDate(served.eval_start) : "—"} – {served.eval_end ? formatDate(served.eval_end) : "—"}): {bt.n_events} stockout
          events, {formatNumber(bt.n_rows)} item-days. Demo data is synthetic; these numbers say nothing about real hospitals.
        </span>
      </div>

      <Card data-testid="risk-comparison">
        <CardHeader title="Could the system have predicted the stockouts that happened?"
          description={`Backtest on the ledger: for every item and day, the risk as it would have been computed that day vs whether the item ran out in the next ${served.horizon_days} days. ${data.model.evaluation?.selection ?? ""}`} />
        <Table>
          <THead>
            <tr>
              <TH>Metric</TH>
              {floor && <TH className="text-right">{RISK_MODEL_LABEL[served.model_type]} + lead-time rule <Badge tone="green" className="ml-2">What you see</Badge></TH>}
              {cands.map((m) => (
                <TH key={m!.id} className="text-right">
                  {RISK_MODEL_LABEL[m!.model_type]}
                  {m!.is_active && <Badge tone="blue" className="ml-2">Serving</Badge>}
                </TH>
              ))}
            </tr>
          </THead>
          <tbody>
            {METRIC_ROWS.map(([k, label, fmt]) => (
              <TR key={k}>
                <TD className="text-sm">{label}</TD>
                {floor && <TD className="text-right font-medium tabular-nums">{fmt((floor[k] as number | null) ?? null)}</TD>}
                {cands.map((m) => <TD key={m!.id} className="text-right tabular-nums">{fmt((m!.metrics.backtest[k] as number | null) ?? null)}</TD>)}
              </TR>
            ))}
          </tbody>
        </Table>
        <p className="border-t px-5 py-3 text-xs text-muted-foreground">
          Rows are item-days where the item was in stock. Warning thresholds were set on synthetic validation data (catch 80% of stockouts), never on
          this backtest. Missing a stockout (false negative) is treated as worse than an extra warning. The lead-time rule raises an item to at least
          MEDIUM when its projected cover exceeds the supplier lead time by 2 days or less (HIGH when an order placed now would arrive too late).
        </p>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Precision vs recall" description="Each point is a different warning threshold on the same backtest rows" />
          <CardBody><PrCurveChart models={data.run_candidates} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Feature importance" description={xgb ? `${xgb.name} · share of total gain` : "No XGBoost model"} />
          <CardBody>{importance.length ? <RankedBars data={importance} formatter={(v) => pct(v)} /> : <EmptyState title="—" />}</CardBody>
        </Card>
      </div>

      <Card data-testid="risk-events">
        <CardHeader title="Actual stockout events" description={`First day of each stockout on the ledger, and when a MEDIUM or HIGH warning (${served.name} + lead-time rule) first appeared`} />
        <Table>
          <THead>
            <tr>
              <TH>Item</TH><TH>Stocked out</TH><TH className="text-right">Lead time</TH><TH>First warning</TH>
              <TH className="text-right">Warning (days)</TH><TH>Result</TH>
            </tr>
          </THead>
          <tbody>
            {(served.evaluation?.events_with_floor?.length ? served.evaluation.events_with_floor : served.evaluation?.events ?? []).map((e) => (
              <TR key={`${e.consumable_id}-${e.date}`}>
                <TD>{data.item_names[String(e.consumable_id)] ?? `#${e.consumable_id}`}</TD>
                <TD className="whitespace-nowrap">{formatDate(e.date)}</TD>
                <TD className="text-right tabular-nums">{e.lead_time ?? "—"} d</TD>
                <TD className="whitespace-nowrap">{e.first_warning ? formatDate(e.first_warning) : "—"}</TD>
                <TD className="text-right tabular-nums">{e.warning_days ?? "—"}</TD>
                <TD>
                  {e.caught_in_time ? <Badge tone="green">Warned in time</Badge> : e.caught ? <Badge tone="amber">Warned, too late to reorder</Badge> : <Badge tone="red">Missed</Badge>}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>

      {(xgb?.evaluation?.calibration.length ?? 0) > 0 && (
        <Card>
          <CardHeader title="Calibration (XGBoost, backtest)" description="Do predicted probabilities match how often stockouts actually happened?" />
          <Table>
            <THead><tr><TH>Predicted</TH><TH className="text-right">Item-days</TH><TH className="text-right">Mean predicted</TH><TH className="text-right">Observed rate</TH></tr></THead>
            <tbody>
              {xgb!.evaluation!.calibration.map((c) => (
                <TR key={c.bin}>
                  <TD>{c.bin}</TD>
                  <TD className="text-right tabular-nums">{formatNumber(c.n)}</TD>
                  <TD className="text-right tabular-nums">{pct(c.mean_predicted)}</TD>
                  <TD className="text-right tabular-nums">{pct(c.observed_rate)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      <p className="text-xs text-muted-foreground">
        Simulation settings: supplier on-time {String((sim.p_on_time as number[] | undefined)?.map((x) => pct(x)).join("–") ?? "—")},
        missed-reorder chance {pct(sim.p_missed_reorder as number | undefined)}, demand spikes, short-dated deliveries.
        Dataset {served.dataset_hash.slice(0, 10)} · trained {timeAgo(served.trained_at)}.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ page */

function RisksInner() {
  const { can } = useMe();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = params.get("tab") ?? "risks";
  const selected = Number(params.get("item")) || null;
  const setParams = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params.toString());
    Object.entries(next).forEach(([k, v]) => (v == null ? p.delete(k) : p.set(k, v)));
    router.replace(`${pathname}?${p.toString()}`, { scroll: false });
  };
  const { data, isLoading } = useQuery({ queryKey: ["stockout-risks", "overview"], queryFn: () => get<RiskOverview>("/stockout-risks") });
  const train = useAction(() => post<RiskTrainResult>("/stockout-risks/train"), {
    invalidate: RISK_KEYS,
    success: (r) => `Risk model trained in ${r.seconds}s — serving ${r.active_model} (${r.n_events} past stockouts in the backtest)`,
  });
  const refresh = useAction(() => post<RiskOverview>("/stockout-risks/refresh"), { invalidate: RISK_KEYS, success: "Risk recalculated for all items" });
  const model = data?.model;
  const bt = model?.metrics.backtest;

  return (
    <>
      <PageHeader
        title="Stockout risk"
        description="Which items are likely to run out, when, and why — current stock and expiry (V1) + demand forecast (V2) + supplier lead time. Operations planning only."
        actions={
          can(PERM.TRAIN_RISK) && (
            <>
              {model && <Button variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}>{refresh.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Recalculate</Button>}
              <Button onClick={() => train.mutate()} disabled={train.isPending || !data?.forecast_model} variant={model ? "outline" : "default"}>
                {train.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} {model ? "Retrain risk model" : "Train risk model"}
              </Button>
            </>
          )
        }
      />
      {isLoading || !data ? (
        <Skeleton className="h-96" />
      ) : !model ? (
        <Card><EmptyState title="No stockout risk yet" description={data.message ?? undefined} /></Card>
      ) : (
        <>
          <div className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border bg-card px-4 py-3 text-sm" data-testid="risk-model-banner">
            <span className="inline-flex items-center gap-2 font-medium">
              <CheckCircle2 className="size-4 text-emerald-600" /> Risk model <Badge tone="blue" className="font-mono">{model.name}</Badge>
            </span>
            <span className="text-muted-foreground">{RISK_MODEL_LABEL[model.model_type]}</span>
            <span className="text-muted-foreground">Backtest precision <b className="text-foreground">{pct(bt?.precision)}</b> · recall <b className="text-foreground">{pct(bt?.recall)}</b> · PR-AUC <b className="text-foreground">{bt?.pr_auc?.toFixed(2) ?? "—"}</b></span>
            <span className="text-muted-foreground">Demand from <span className="font-mono">{data.forecast_model}</span></span>
            <span className="text-muted-foreground">Trained {timeAgo(model.trained_at)}</span>
          </div>
          <Tabs.Root value={tab} onValueChange={(v) => setParams({ tab: v })}>
            <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b">
              {[["risks", "Items at risk"], ["evaluation", "Evaluation"]].map(([k, label]) => (
                <Tabs.Trigger key={k} value={k}
                  className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800">
                  {label}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            <Tabs.Content value="risks">
              <RisksTab data={data} selected={selected} onSelect={(id) => setParams({ item: id == null ? null : String(id) })} />
            </Tabs.Content>
            <Tabs.Content value="evaluation">
              <EvaluationTab key={model.id} modelId={model.id} />
            </Tabs.Content>
          </Tabs.Root>
        </>
      )}
    </>
  );
}

export default function StockoutRisksPage() {
  return (
    <Suspense>
      <RisksInner />
    </Suspense>
  );
}
