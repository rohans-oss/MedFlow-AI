"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { Check, FlaskConical, Loader2, Pencil, RotateCcw, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { RiskBadge } from "@/components/badges";
import { AvailabilityChart, ScenarioCostChart } from "@/components/charts/procurement-charts";
import { ApproveDialog, ModifyDialog, PROCUREMENT_KEYS, RejectDialog } from "@/components/forms/procurement-dialogs";
import { RecommendationPilotHooks } from "@/components/pilots";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Field,
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
import { get, post, put } from "@/lib/api";
import type {
  ArrivalEvaluation,
  Attention,
  CostSettings,
  CostSettingsValues,
  GenerateResult,
  ProcurementPlan,
  RecommendationDetail,
  RecommendationRow,
  Scenario,
  ScenarioTag,
  WhatIfResult,
} from "@/lib/types";
import { formatDate, formatNumber } from "@/lib/utils";

const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const money = (v: number | null | undefined) => (v == null ? "—" : `₹${formatNumber(Math.round(v))}`);

const TAG: Record<ScenarioTag, { label: string; tone: "green" | "blue" | "violet" | "amber" | "neutral" | "orange" }> = {
  recommended: { label: "Recommended", tone: "green" },
  cheapest: { label: "Cheapest price", tone: "blue" },
  most_reliable: { label: "Most reliable", tone: "violet" },
  fastest: { label: "Fastest", tone: "amber" },
  no_order: { label: "No order", tone: "neutral" },
  split: { label: "Split", tone: "orange" },
};

function Tags({ tags }: { tags: ScenarioTag[] }) {
  return <span className="flex flex-wrap gap-1">{tags.map((t) => <Badge key={t} tone={TAG[t].tone}>{TAG[t].label}</Badge>)}</span>;
}

function GenerateButton({ itemIds, label = "Generate recommendations" }: { itemIds?: number[]; label?: string }) {
  const action = useAction(() => post<GenerateResult>("/procurement/recommendations/generate", itemIds ? { item_ids: itemIds } : {}), {
    invalidate: PROCUREMENT_KEYS,
    success: (r) => `${r.created} recommendation${r.created === 1 ? "" : "s"} for review (${r.orders_recommended} with an order, ${money(r.purchase_value)}) — nothing ordered`,
  });
  return (
    <Button onClick={() => action.mutate()} disabled={action.isPending}>
      {action.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} {label}
    </Button>
  );
}

/* ------------------------------------------------------------------ 1. needs attention */

function AttentionTab({ onOpen }: { onOpen: (id: number, tab: string) => void }) {
  const { can } = useMe();
  const { data, isLoading } = useQuery({ queryKey: ["procurement", "attention"], queryFn: () => get<Attention>("/procurement/needs-attention") });
  if (isLoading || !data) return <Skeleton className="h-96" />;
  const overdue = data.items.filter((r) => r.order_by_date && r.order_by_date < data.as_of).length;
  const transit = data.items.filter((r) => r.in_transit_orders > 0).length;
  const pending = data.items.filter((r) => r.pending_recommendation_id != null).length;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-4">
        <Stat label="Items needing a decision" value={<span data-testid="attention-count">{data.items.length}</span>} sub={`review period ${data.settings.review_period_days} days`} />
        <Stat label="Order-by date passed" value={overdue} tone={overdue ? "red" : "green"} sub="with in-transit orders counted" />
        <Stat label="With orders in transit" value={transit} sub="counted at their arrival probability" />
        <Stat label="Pending recommendations" value={pending} tone={pending ? "amber" : "default"} sub="awaiting approval" />
      </div>
      <Card>
        <CardHeader
          title="Needs attention"
          description={`Items whose projected stock (physical + in-transit × historical arrival probability − forecast demand) falls below safety stock before the next review, V3 medium/high risk, or overdue deliveries. As of ${formatDate(data.as_of)}.`}
          action={can(PERM.PROCUREMENT_RECOMMEND) && data.items.length > 0 && <GenerateButton />}
        />
        {!data.items.length ? (
          <EmptyState title="Nothing needs a procurement decision in this review period" />
        ) : (
          <Table data-testid="attention-table">
            <THead>
              <tr>
                <TH>Item</TH><TH>V3 risk</TH><TH className="text-right">Usable</TH><TH className="text-right">In transit (expected)</TH>
                <TH>Stock runs out</TH><TH>Order by</TH><TH className="text-right">P(stockout) no order</TH><TH className="text-right">Qty needed (MOQ)</TH><TH>Why</TH><TH />
              </tr>
            </THead>
            <tbody>
              {data.items.map((r) => (
                <TR key={r.item.id}>
                  <TD className="min-w-48">
                    <button className="text-left font-medium text-teal-800 hover:underline" onClick={() => onOpen(r.item.id, "scenarios")}>{r.item.name}</button>
                    <div className="text-xs text-muted-foreground">{r.item.sku} · {r.avg_daily_demand.toFixed(1)}/day</div>
                  </TD>
                  <TD>{r.risk_level ? <RiskBadge level={r.risk_level} out={r.usable_stock <= 0} /> : "—"}</TD>
                  <TD className="text-right tabular-nums">{formatNumber(r.usable_stock)}</TD>
                  <TD className="text-right tabular-nums">
                    {r.in_transit_orders ? <>{formatNumber(r.in_transit_quantity)} <span className="text-xs text-muted-foreground">({formatNumber(Math.round(r.expected_in_transit))})</span></> : "—"}
                    {r.overdue_orders > 0 && <div><Badge tone="red">{r.overdue_orders} overdue</Badge></div>}
                  </TD>
                  <TD className="whitespace-nowrap">{r.need_by_date ? formatDate(r.need_by_date) : "not within horizon"}</TD>
                  <TD className="whitespace-nowrap">{r.order_by_date ? <span className={r.order_by_date <= data.as_of ? "font-semibold text-red-600" : ""}>{formatDate(r.order_by_date)}</span> : "—"}</TD>
                  <TD className="text-right tabular-nums">{pct(r.p_stockout_no_order)} <div className="text-xs text-muted-foreground">{r.horizon_days} d · short {formatNumber(Math.round(r.expected_shortage_no_order))}</div></TD>
                  <TD className="text-right tabular-nums">{formatNumber(r.required_quantity)}{r.moq_adjusted_quantity > r.required_quantity && <div className="text-xs text-muted-foreground">MOQ → {formatNumber(r.moq_adjusted_quantity)}</div>}</TD>
                  <TD className="min-w-72 max-w-md text-xs text-muted-foreground"><ul className="list-disc space-y-0.5 pl-4">{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul></TD>
                  <TD className="whitespace-nowrap text-right">
                    {r.pending_recommendation_id != null
                      ? <Button size="sm" variant="outline" onClick={() => onOpen(r.item.id, "recommendations")}>Pending: {r.pending_summary}</Button>
                      : <Button size="sm" variant="ghost" onClick={() => onOpen(r.item.id, "scenarios")}>Scenarios</Button>}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {data.skipped.length > 0 && <p className="text-xs text-muted-foreground">{data.skipped.length} item(s) could not be planned (no forecast or no supplier).</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ 2. recommendations */

function RecommendationsTab({ focus, onOpen }: { focus: number | null; onOpen: (id: number, tab: string) => void }) {
  const { can } = useMe();
  const { data, isLoading } = useQuery({ queryKey: ["procurement", "recommendations", "PENDING"], queryFn: () => get<RecommendationRow[]>("/procurement/recommendations", { status: "PENDING" }) });
  if (isLoading || !data) return <Skeleton className="h-96" />;
  if (!data.length) {
    return (
      <Card>
        <EmptyState title="No pending recommendations" description="Generate recommendations for the items that need attention. Each one shows the quantity, supplier and expected cost, and waits for a person to approve it."
          action={can(PERM.PROCUREMENT_RECOMMEND) ? <GenerateButton /> : undefined} />
      </Card>
    );
  }
  const orders = data.filter((r) => r.lines.length);
  const total = orders.reduce((a, r) => a + r.purchase_value, 0);
  const stale = data.some((r) => !r.is_current);
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Recommendations awaiting review" value={data.length} sub={`${orders.length} with an order · ${data.length - orders.length} no new order`} />
        <Stat label="Purchase value if all approved" value={money(total)} />
        <Stat label="Generated" value={formatDate(data[0].as_of)} tone={stale ? "amber" : "default"} sub={stale ? "out of date — regenerate before approving" : `by ${data[0].generated_by ?? "training run"}`} />
      </div>
      {can(PERM.PROCUREMENT_RECOMMEND) && <div className="flex justify-end"><GenerateButton label="Regenerate" /></div>}
      <div className="grid gap-4 lg:grid-cols-2" data-testid="recommendation-cards">
        {data.map((r) => (
          <Card key={r.id} className={focus === r.item.id ? "ring-2 ring-teal-600" : ""} data-testid={`rec-${r.item.sku}`}>
            <CardHeader
              title={<span className="flex flex-wrap items-center gap-2">{r.item.name} {r.risk_level && <RiskBadge level={r.risk_level} />}</span>}
              description={`${r.item.sku}${r.order_by_date ? ` · order by ${formatDate(r.order_by_date)}` : ""}${r.need_by_date ? ` · runs out ${formatDate(r.need_by_date)}` : ""}`}
              action={<Button size="sm" variant="ghost" onClick={() => onOpen(r.item.id, "scenarios")}>Compare scenarios</Button>}
            />
            <CardBody className="space-y-3 text-sm">
              <div className="grid grid-cols-3 gap-3">
                <div><div className="text-xs text-muted-foreground">Order</div><div className="font-semibold">{r.lines.length ? r.lines.map((l) => `${formatNumber(l.quantity)} × ${l.code}`).join(" + ") : "No new order"}</div></div>
                <div><div className="text-xs text-muted-foreground">Purchase · expected cost</div><div className="font-semibold tabular-nums">{money(r.purchase_value)} · {money(r.expected_cost)}</div></div>
                <div><div className="text-xs text-muted-foreground">P(stockout) with · without</div><div className="font-semibold tabular-nums">{pct(r.p_stockout)} · {pct(r.no_order_p_stockout)}</div></div>
              </div>
              {r.lines.map((l) => (
                <div key={l.supplier_id} className="text-xs text-muted-foreground">
                  {l.supplier}: ₹{l.unit_price.toFixed(2)}/{r.item.unit} · typically arrives {formatDate(l.typical_arrival_date)} · {l.window_n ? `${l.window_k} of ${l.window_n} past orders in the window` : "no comparable history"}
                </div>
              ))}
              <p className="text-muted-foreground">{r.headline}</p>
              <ExplanationToggle id={r.id} />
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  );
}

function ExplanationToggle({ id }: { id: number }) {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["procurement", "recommendation", id],
    queryFn: () => get<RecommendationDetail>(`/procurement/recommendations/${id}`),
    enabled: open,
  });
  return (
    <div>
      <button className="text-xs font-medium text-teal-800 hover:underline" onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Show"} explanation & cost breakdown</button>
      {open && data && (
        <div className="mt-2 space-y-2" data-testid="rec-explanation">
          <ul className="list-disc space-y-1 pl-5 text-xs">{data.explanation.map((x) => <li key={x}>{x}</li>)}</ul>
          <CostBreakdown s={{ costs: data.cost_breakdown }} />
          <RecommendationPilotHooks recommendationId={id} />
        </div>
      )}
    </div>
  );
}

function CostBreakdown({ s }: { s: Pick<Scenario, "costs"> }) {
  const c = s.costs;
  const rows: [string, number, string][] = [
    ["Purchase", c.purchase, `goods ${money(c.purchase_goods)} + order cost ${money(c.order_fixed)}`],
    ["+ Expected stockout", c.stockout, "units short × stockout cost per unit"],
    ["+ Expected holding", c.holding, "extra unit-days in stock × holding rate"],
    ["+ Expected expiry / waste", c.expiry, "units expiring before use × price × (1 + disposal)"],
    ["− Carried forward", -c.carried_forward, "units not used in the horizon serve later demand"],
  ];
  return (
    <table className="w-full text-xs" data-testid="cost-breakdown">
      <tbody>
        {rows.map(([k, v, hint]) => (
          <tr key={k} className="border-b last:border-0"><td className="py-1">{k}</td><td className="py-1 text-right tabular-nums">{money(v)}</td><td className="py-1 pl-3 text-muted-foreground">{hint}</td></tr>
        ))}
        <tr className="font-semibold"><td className="py-1">= Expected total cost</td><td className="py-1 text-right tabular-nums">{money(c.total)}</td><td /></tr>
      </tbody>
    </table>
  );
}

/* ------------------------------------------------------------------ 3. scenarios */

function ItemPicker({ value, onChange }: { value: number | null; onChange: (id: number) => void }) {
  const { data } = useQuery({ queryKey: ["procurement", "attention"], queryFn: () => get<Attention>("/procurement/needs-attention") });
  return (
    <Select className="w-80" aria-label="Item" value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))}>
      <option value="">Select an item that needs attention…</option>
      {data?.items.map((r) => <option key={r.item.id} value={r.item.id}>{r.item.name} ({r.item.sku})</option>)}
    </Select>
  );
}

function ScenariosTab({ itemId, onItem }: { itemId: number | null; onItem: (id: number) => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["procurement", "plan", itemId],
    queryFn: () => get<ProcurementPlan>(`/procurement/items/${itemId}/plan`),
    enabled: itemId != null,
  });
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3"><ItemPicker value={itemId} onChange={onItem} />{data && <span className="text-xs text-muted-foreground">Live calculation — nothing is stored or ordered.</span>}</div>
      {itemId == null ? <Card><EmptyState title="Pick an item to compare suppliers and quantities" /></Card>
        : isLoading ? <Skeleton className="h-96" />
        : error ? <Card><EmptyState title="Cannot plan this item" description={(error as Error).message} /></Card>
        : data ? <PlanView plan={data} /> : null}
    </div>
  );
}

function PlanView({ plan }: { plan: ProcurementPlan }) {
  const r = plan.replenishment;
  const u = plan.item.unit;
  const steps: [string, string][] = [
    ["Expected lead time (reference supplier " + plan.reference_supplier + ")", `${r.lead_time_days} d (± ${r.lead_time_sd} d)`],
    ["Cover period = lead time + review period", `${r.cover_days} d`],
    ["Forecast demand over the cover period", `${formatNumber(Math.round(r.demand_over_cover))} ${u} (${r.avg_daily_demand}/day)`],
    [`Safety stock = z × √(cover × σ² + demand² × σ_lead²)`, `${formatNumber(Math.round(r.safety_stock))} ${u} (σ ${r.sigma_daily}/day, ${plan.demand.sigma_basis})`],
    ["− Usable stock (minus expiring before use)", `${formatNumber(r.usable_stock)}${r.expiring_before_use ? ` − ${formatNumber(Math.round(r.expiring_before_use))}` : ""}`],
    ["− Expected in-transit arrivals (× arrival probability)", formatNumber(Math.round(r.expected_incoming))],
    ["= Required quantity", `${formatNumber(r.required_quantity)} ${u}`],
    ["MOQ-adjusted quantity", `${formatNumber(r.moq_adjusted_quantity)} ${u} (MOQ ${formatNumber(r.moq)})`],
    ["Expiry cap · storage cap", `${r.expiry_cap == null ? "no expiry" : formatNumber(r.expiry_cap)} · ${r.storage_cap == null ? "no max level" : formatNumber(r.storage_cap)}`],
    ["Reorder point · order by", `${formatNumber(Math.round(r.reorder_point))} · ${r.order_by_date ? formatDate(r.order_by_date) : "not within 30 days"}`],
  ];
  return (
    <div className="space-y-6" data-testid="plan-view">
      <Card>
        <CardHeader title={`Why ${plan.scenarios.find((s) => s.key === plan.recommended_key)?.label ?? ""}`} description={`${plan.item.name} · horizon ${plan.horizon_days} days · delivery needed by ${plan.need_by_date ? formatDate(plan.need_by_date) : "—"} · ${plan.solver.engine} (${plan.solver.status})`} />
        <CardBody><ul className="list-disc space-y-1 pl-5 text-sm" data-testid="plan-explanation">{plan.explanation.map((x) => <li key={x}>{x}</li>)}</ul></CardBody>
      </Card>

      <Card>
        <CardHeader title="Scenarios" description="Every option evaluated on the same simulated demand and delivery paths. Sorted by expected total cost." />
        <Table data-testid="scenario-table">
          <THead>
            <tr>
              <TH>Scenario</TH><TH className="text-right">Purchase</TH><TH className="text-right">Stockout</TH><TH className="text-right">Holding</TH><TH className="text-right">Expiry</TH><TH className="text-right">Carried fwd</TH>
              <TH className="text-right">Expected total</TH><TH className="text-right">P(stockout)</TH><TH className="text-right">Exp. shortage</TH><TH>Arrival</TH><TH>Delivery evidence</TH>
            </tr>
          </THead>
          <tbody>
            {plan.scenarios.map((s) => (
              <TR key={s.key} className={s.tags.includes("recommended") ? "bg-emerald-50/60" : ""}>
                <TD className="min-w-56"><div className="font-medium">{s.label}</div><Tags tags={s.tags} /><div className="mt-0.5 text-xs text-muted-foreground">{s.note}</div></TD>
                <TD className="text-right tabular-nums">{money(s.costs.purchase)}</TD>
                <TD className="text-right tabular-nums">{money(s.costs.stockout)}</TD>
                <TD className="text-right tabular-nums">{money(s.costs.holding)}</TD>
                <TD className="text-right tabular-nums">{money(s.costs.expiry)}</TD>
                <TD className="text-right tabular-nums">−{money(s.costs.carried_forward)}</TD>
                <TD className="text-right font-semibold tabular-nums">{money(s.costs.total)}</TD>
                <TD className="text-right tabular-nums">{pct(s.metrics.p_stockout)}</TD>
                <TD className="text-right tabular-nums">{s.metrics.expected_shortage.toFixed(1)}</TD>
                <TD className="whitespace-nowrap">{s.metrics.arrival_date ? formatDate(s.metrics.arrival_date) : "—"}{s.metrics.last_arrival_date && <div className="text-xs text-muted-foreground">rest {formatDate(s.metrics.last_arrival_date)}</div>}</TD>
                <TD className="min-w-48 text-xs">
                  {s.lines.map((l) => (
                    <div key={l.supplier_id}>{l.code}: {l.window_n ? `${l.window_k}/${l.window_n} within ${plan.need_by_days} d` : "no history"} · P {pct(l.p_arrive_by_need)}{l.arrival_basis && l.arrival_basis !== "item" && <span className="text-muted-foreground"> ({l.arrival_basis})</span>}</div>
                  ))}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Expected total cost by scenario" description="Lower is better; hover for the breakdown" />
          <CardBody><ScenarioCostChart scenarios={plan.scenarios} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Projected available stock" description="V5.2: physical stock + in-transit × arrival probability − forecast demand" />
          <CardBody><AvailabilityChart rep={r} unit={u} /></CardBody>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="V5.1 replenishment calculation" description="Reference supplier's numbers, step by step" />
          <CardBody>
            <dl className="divide-y text-sm" data-testid="replenishment-steps">
              {steps.map(([k, v]) => <div key={k} className="flex justify-between gap-4 py-1.5"><dt className="text-muted-foreground">{k}</dt><dd className="text-right font-medium tabular-nums">{v}</dd></div>)}
            </dl>
            {r.warnings.length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-amber-700">{r.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Orders in transit" description="Counted at their historical probability of arriving in time — never as certain" />
          {!plan.in_transit.length ? <EmptyState title="No open orders for this item" /> : (
            <Table data-testid="transit-table">
              <THead><tr><TH>Order</TH><TH className="text-right">Outstanding</TH><TH>Expected</TH><TH className="text-right">P(in time)</TH><TH className="text-right">Counted as</TH></tr></THead>
              <tbody>
                {plan.in_transit.map((t) => (
                  <TR key={t.order_id}>
                    <TD><span className="font-mono text-xs">{t.reference}</span><div className="text-xs text-muted-foreground">{t.supplier}</div></TD>
                    <TD className="text-right tabular-nums">{formatNumber(t.outstanding)}</TD>
                    <TD className="whitespace-nowrap">{formatDate(t.expected_date)}{t.overdue_days > 0 && <Badge tone="red" className="ml-1">{t.overdue_days} d overdue</Badge>}</TD>
                    <TD className="text-right tabular-nums">{t.arrival_basis === "no_evidence" ? <span title="No past order from this supplier took this long">no evidence</span> : <>{pct(t.p_arrive_by_need)} <div className="text-xs text-muted-foreground">{t.window_k}/{t.window_n}</div></>}</TD>
                    <TD className="text-right tabular-nums">{formatNumber(Math.round(t.expected_quantity))}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 4. what-if */

function WhatIfTab({ itemId, onItem }: { itemId: number | null; onItem: (id: number) => void }) {
  const plan = useQuery({ queryKey: ["procurement", "plan", itemId], queryFn: () => get<ProcurementPlan>(`/procurement/items/${itemId}/plan`), enabled: itemId != null });
  const suppliers = useMemo(() => {
    const m = new Map<number, string>();
    for (const s of plan.data?.scenarios ?? []) for (const l of s.lines) m.set(l.supplier_id, `${l.supplier} (${l.code})`);
    for (const t of plan.data?.in_transit ?? []) m.set(t.supplier_id, t.supplier);
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [plan.data]);
  const [supplierId, setSupplierId] = useState<number | "">("");
  const [days, setDays] = useState(2);
  const [demand, setDemand] = useState(0);
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const pickItem = (id: number) => { setResult(null); setSupplierId(""); onItem(id); };
  const run = useAction(
    () => post<WhatIfResult>(`/procurement/items/${itemId}/what-if`, { delays: supplierId === "" ? [] : [{ supplier_id: supplierId, days }], demand_change_pct: demand }),
    { onSuccess: setResult },
  );
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="What-if simulator" description="Re-runs the same scenarios on the same simulated demand with supplier deliveries later (new and in-transit orders) or demand changed. Nothing is stored." />
        <CardBody>
          <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
            <Field label="Item" htmlFor="wi_item"><ItemPicker value={itemId} onChange={pickItem} /></Field>
            <Field label="Supplier delayed" htmlFor="wi_sup">
              <Select id="wi_sup" className="w-64" value={supplierId} onChange={(e) => setSupplierId(e.target.value === "" ? "" : Number(e.target.value))} disabled={!plan.data}>
                <option value="">No supplier delay</option>
                {suppliers.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </Select>
            </Field>
            <Field label="Extra days" htmlFor="wi_days"><Input id="wi_days" type="number" min={0} max={60} className="w-24" value={days} onChange={(e) => setDays(Number(e.target.value))} /></Field>
            <Field label="Demand change %" htmlFor="wi_dem"><Input id="wi_dem" type="number" min={-90} max={300} className="w-28" value={demand} onChange={(e) => setDemand(Number(e.target.value))} /></Field>
            <Button type="submit" disabled={itemId == null || run.isPending}>{run.isPending ? <Loader2 className="animate-spin" /> : <FlaskConical />} Run what-if</Button>
            {result && <Button type="button" variant="ghost" onClick={() => setResult(null)}><RotateCcw /> Reset</Button>}
          </form>
        </CardBody>
      </Card>
      {result && (
        <Card data-testid="whatif-result">
          <CardHeader title={`What-if · ${result.item.name}`} description={`Same ${result.horizon_days}-day horizon and delivery window (${result.need_by_date ? formatDate(result.need_by_date) : "—"})`} />
          <CardBody><ul className="list-disc space-y-1 pl-5 text-sm" data-testid="whatif-summary">{result.summary.map((x) => <li key={x}>{x}</li>)}</ul></CardBody>
          <Table>
            <THead>
              <tr><TH>Scenario</TH><TH className="text-right">P(stockout)</TH><TH className="text-right">Expected shortage</TH><TH className="text-right">Expected cost</TH><TH>First arrival</TH><TH className="text-right">P(arrives in time)</TH></tr>
            </THead>
            <tbody>
              {result.scenarios.map((s) => {
                const ch = (a: number, b: number) => (Math.abs(b - a) > 1e-9 ? (b > a ? "text-red-700" : "text-emerald-700") : "");
                return (
                  <TR key={s.key} className={s.key === result.baseline_recommended ? "bg-emerald-50/60" : ""}>
                    <TD className="min-w-52"><div className="font-medium">{s.label}</div><Tags tags={s.tags} />{s.key === result.whatif_best && s.key !== result.baseline_recommended && <Badge tone="green" className="ml-1">Best under what-if</Badge>}</TD>
                    <TD className="text-right tabular-nums">{pct(s.before.p_stockout)} → <span className={ch(s.before.p_stockout, s.after.p_stockout)}>{pct(s.after.p_stockout)}</span></TD>
                    <TD className="text-right tabular-nums">{s.before.expected_shortage.toFixed(1)} → <span className={ch(s.before.expected_shortage, s.after.expected_shortage)}>{s.after.expected_shortage.toFixed(1)}</span></TD>
                    <TD className="text-right tabular-nums">{money(s.before.total_cost)} → <span className={ch(s.before.total_cost, s.after.total_cost)}>{money(s.after.total_cost)}</span></TD>
                    <TD className="whitespace-nowrap">{s.before.arrival_date ? `${formatDate(s.before.arrival_date)} → ${s.after.arrival_date ? formatDate(s.after.arrival_date) : "not in horizon"}` : "—"}</TD>
                    <TD className="text-right tabular-nums">{s.before.p_arrive_by_need == null ? "—" : `${pct(s.before.p_arrive_by_need)} → ${pct(s.after.p_arrive_by_need)}`}</TD>
                  </TR>
                );
              })}
            </tbody>
          </Table>
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 5. approval queue */

function ApprovalTab() {
  const { can } = useMe();
  const [status, setStatus] = useState("PENDING");
  const { data, isLoading } = useQuery({ queryKey: ["procurement", "recommendations", status], queryFn: () => get<RecommendationRow[]>("/procurement/recommendations", { status }) });
  const approver = can(PERM.PROCUREMENT_APPROVE);
  return (
    <Card>
      <CardHeader
        title="Approval queue"
        description="Risk detected → scenarios → optimiser → your review. Approving records the supplier order in MedFlow (tracking only — nothing is sent to the supplier)."
        action={
          <Select className="w-44" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="PENDING">Pending</option><option value="decided">Decided</option><option value="APPROVED">Approved</option>
            <option value="REJECTED">Rejected</option><option value="SUPERSEDED">Superseded</option><option value="all">All</option>
          </Select>
        }
      />
      {isLoading || !data ? <Skeleton className="m-5 h-64" /> : !data.length ? <EmptyState title={status === "PENDING" ? "Nothing waiting for approval" : "No recommendations"} /> : (
        <Table data-testid="approval-table">
          <THead>
            <tr><TH>Item</TH><TH>Recommended</TH><TH className="text-right">Purchase</TH><TH className="text-right">Expected cost</TH><TH className="text-right">P(stockout) with · without</TH><TH>Order by</TH><TH>Status</TH><TH /></tr>
          </THead>
          <tbody>
            {data.map((r) => (
              <TR key={r.id} data-testid={`queue-${r.item.sku}`}>
                <TD className="min-w-48"><Link href={`/inventory/${r.item.id}`} className="font-medium hover:underline">{r.item.name}</Link><div className="text-xs text-muted-foreground">{r.item.sku} · #{r.id} · {formatDate(r.as_of)}</div></TD>
                <TD className="min-w-48 text-sm">
                  {r.lines.length ? r.lines.map((l) => <div key={l.supplier_id}>{formatNumber(l.quantity)} {r.item.unit} · {l.supplier} <span className="text-xs text-muted-foreground">₹{l.unit_price.toFixed(2)}</span></div>) : <span className="text-muted-foreground">No new order</span>}
                  {r.modified && r.final_lines && <div className="mt-1 text-xs text-amber-800">Approved as: {r.final_lines.map((l) => `${formatNumber(l.quantity)} · ${l.supplier}`).join(" + ") || "no order"}</div>}
                </TD>
                <TD className="text-right tabular-nums">{money(r.purchase_value)}</TD>
                <TD className="text-right tabular-nums">{money(r.expected_cost)}</TD>
                <TD className="text-right tabular-nums">{pct(r.p_stockout)} · {pct(r.no_order_p_stockout)}</TD>
                <TD className="whitespace-nowrap">{r.order_by_date ? formatDate(r.order_by_date) : "—"}</TD>
                <TD className="min-w-40 text-xs">
                  <Badge tone={r.status === "APPROVED" ? "green" : r.status === "REJECTED" ? "red" : r.status === "PENDING" ? "amber" : "neutral"}>{r.status.toLowerCase()}{r.modified ? " (modified)" : ""}</Badge>
                  {r.decided_by && <div className="mt-1 text-muted-foreground">{r.decided_by}{r.decision_reason ? `: “${r.decision_reason}”` : ""}</div>}
                  {r.orders.length > 0 && <div className="mt-1">Recorded: {r.orders.map((o) => o.reference).join(", ")}</div>}
                  {r.status === "PENDING" && !r.is_current && <div className="mt-1 text-amber-700">Out of date — regenerate</div>}
                </TD>
                <TD className="whitespace-nowrap text-right">
                  {r.status === "PENDING" && approver && r.is_current && (
                    <div className="flex justify-end gap-1">
                      <ApproveDialog rec={r} trigger={<Button size="sm" aria-label={`Approve ${r.item.sku}`}><Check /> Approve</Button>} />
                      <ModifyDialog rec={r} trigger={<Button size="sm" variant="outline" aria-label={`Modify ${r.item.sku}`}><Pencil /> Modify</Button>} />
                      <RejectDialog rec={r} trigger={<Button size="sm" variant="ghost" aria-label={`Reject ${r.item.sku}`}><X /> Reject</Button>} />
                    </div>
                  )}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}
      {!approver && <p className="border-t px-5 py-3 text-xs text-muted-foreground">Approving needs the procurement role (procurement:approve).</p>}
    </Card>
  );
}

/* ------------------------------------------------------------------ cost model */

const FIELDS: { key: keyof CostSettingsValues; label: string; step?: string; kind?: "bool" | "optional" }[] = [
  { key: "service_level", label: "Service level", step: "0.01" },
  { key: "review_period_days", label: "Review period (days)" },
  { key: "horizon_days", label: "Max horizon (days)" },
  { key: "stockout_cost_multiplier", label: "Stockout cost × reference price", step: "0.5" },
  { key: "holding_cost_rate", label: "Holding cost (share of value / year)", step: "0.01" },
  { key: "disposal_cost_pct", label: "Disposal cost (share of price)", step: "0.01" },
  { key: "order_cost", label: "Fixed cost per order line (₹)", step: "1" },
  { key: "budget_limit", label: "Budget per run (₹, optional)", step: "1", kind: "optional" },
  { key: "simulations", label: "Monte Carlo paths" },
  { key: "allow_split", label: "Allow split orders", kind: "bool" },
];

function CostModelTab() {
  const { can } = useMe();
  const { data, isLoading } = useQuery({ queryKey: ["procurement", "settings"], queryFn: () => get<CostSettings>("/procurement/settings") });
  const [edits, setValues] = useState<CostSettingsValues | null>(null);
  const values = edits ?? data?.values ?? null;
  const save = useAction(() => put<CostSettings>("/procurement/settings", values), {
    invalidate: [["procurement"]], success: "Cost model saved — regenerate recommendations to apply it", onSuccess: () => setValues(null),
  });
  if (isLoading || !data || !values) return <Skeleton className="h-96" />;
  const editable = can(PERM.PROCUREMENT_CONFIGURE);
  return (
    <div className="grid gap-6 lg:grid-cols-[2fr_3fr]">
      <div className="space-y-6">
        <Card data-testid="cost-formula">
          <CardHeader title="Expected-cost model" description="The optimiser minimises this — every parameter is shown and editable; no hidden weights" />
          <CardBody><ul className="list-disc space-y-1.5 pl-5 text-sm">{data.formula.map((f) => <li key={f}>{f}</li>)}</ul></CardBody>
        </Card>
        <ArrivalCalibration />
      </div>
      <Card>
        <CardHeader title="Parameters" description={data.is_default ? "Defaults (not yet customised)" : `Last changed ${formatDate(data.updated_at)} by ${data.updated_by}`} />
        <CardBody>
          <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
            {FIELDS.map((f) => (
              <Field key={f.key} label={f.label} htmlFor={`cm_${f.key}`} hint={data.explain[f.key]}>
                {f.kind === "bool" ? (
                  <Select id={`cm_${f.key}`} value={String(values[f.key])} disabled={!editable} onChange={(e) => setValues({ ...values, [f.key]: e.target.value === "true" })}>
                    <option value="true">Yes</option><option value="false">No</option>
                  </Select>
                ) : (
                  <Input id={`cm_${f.key}`} type="number" step={f.step ?? "1"} readOnly={!editable} value={values[f.key] == null ? "" : String(values[f.key])}
                    onChange={(e) => setValues({ ...values, [f.key]: e.target.value === "" && f.kind === "optional" ? null : Number(e.target.value) })} />
                )}
              </Field>
            ))}
            {editable && (
              <div className="flex gap-2 sm:col-span-2">
                <Button type="submit" disabled={save.isPending}>{save.isPending && <Loader2 className="animate-spin" />} Save cost model</Button>
                <Button type="button" variant="outline" onClick={() => setValues(data.defaults)}>Reset to defaults</Button>
              </div>
            )}
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

function ArrivalCalibration() {
  const { data } = useQuery({ queryKey: ["procurement", "evaluation"], queryFn: () => get<ArrivalEvaluation>("/procurement/evaluation") });
  if (!data) return <Skeleton className="h-48" />;
  if (!data.n) return <Card><EmptyState title="No completed orders yet to check delivery probabilities" /></Card>;
  return (
    <Card data-testid="arrival-evaluation">
      <CardHeader title="Are delivery probabilities trustworthy?"
        description={`Backtest on ${formatNumber(data.orders ?? 0)} past orders (${data.windows?.join(", ")}): each order scored with a distribution built only from orders completed before it was placed.`} />
      <CardBody className="space-y-3 text-sm">
        <p>Predicted {pct(data.mean_predicted, 1)} arrived in time vs {pct(data.observed_rate, 1)} observed. Brier score {data.brier_model?.toFixed(3)} vs {data.brier_quote_certain?.toFixed(3)} when every quote is assumed certain (lower is better).</p>
        <table className="w-full text-xs">
          <thead className="text-muted-foreground"><tr><th className="text-left">Predicted P(in time)</th><th className="text-right">Cases</th><th className="text-right">Mean predicted</th><th className="text-right">Observed</th></tr></thead>
          <tbody>
            {data.calibration?.map((b) => (
              <tr key={b.from} className="border-t"><td className="py-1">{pct(b.from)}–{pct(b.to)}</td><td className="py-1 text-right tabular-nums">{formatNumber(b.n)}</td><td className="py-1 text-right tabular-nums">{pct(b.mean_predicted, 1)}</td><td className="py-1 text-right tabular-nums">{pct(b.observed, 1)}</td></tr>
            ))}
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

/* ------------------------------------------------------------------ page */

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = params.get("tab") ?? "attention";
  const item = Number(params.get("item")) || null;
  const setParams = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params.toString());
    Object.entries(next).forEach(([k, v]) => (v == null ? p.delete(k) : p.set(k, v)));
    router.replace(`${pathname}?${p.toString()}`, { scroll: false });
  };
  const open = (id: number, t: string) => setParams({ tab: t, item: String(id) });
  const onItem = (id: number) => setParams({ item: id ? String(id) : null });
  return (
    <>
      <PageHeader
        title="Procurement intelligence"
        description="What to buy, how much, from whom and by when — scenarios costed with a configurable expected-cost model and chosen with OR-Tools. Recommendations only: every order needs a person's approval, and approved orders are recorded in MedFlow, not sent to suppliers."
      />
      <Tabs.Root value={tab} onValueChange={(v) => setParams({ tab: v })}>
        <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b">
          {[["attention", "Needs attention"], ["recommendations", "Recommendations"], ["scenarios", "Scenarios"], ["what-if", "What-if simulator"], ["approvals", "Approval queue"], ["cost-model", "Cost model"]].map(([k, label]) => (
            <Tabs.Trigger key={k} value={k}
              className="-mb-px whitespace-nowrap border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800">
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="attention"><AttentionTab onOpen={open} /></Tabs.Content>
        <Tabs.Content value="recommendations"><RecommendationsTab focus={item} onOpen={open} /></Tabs.Content>
        <Tabs.Content value="scenarios"><ScenariosTab itemId={item} onItem={onItem} /></Tabs.Content>
        <Tabs.Content value="what-if"><WhatIfTab itemId={item} onItem={onItem} /></Tabs.Content>
        <Tabs.Content value="approvals"><ApprovalTab /></Tabs.Content>
        <Tabs.Content value="cost-model"><CostModelTab /></Tabs.Content>
      </Tabs.Root>
    </>
  );
}

export default function ProcurementPage() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
