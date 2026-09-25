"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ClipboardPlus, FlaskConical, PackagePlus, X, XCircle } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { GradeBadge, RiskBadge, VerdictBadge } from "@/components/badges";
import { CloseOrderDialog, RecordOrderDialog } from "@/components/forms/supplier-order-dialogs";
import { ReceiveDialog } from "@/components/forms/stock-dialogs";
import { ItemSupplierOptionsView } from "@/components/supplier-options";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  PageHeader,
  Pagination,
  Select,
  Skeleton,
  Stat,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/primitives";
import { useSuppliers } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get } from "@/lib/api";
import type { Page, Scorecards, SupplierAtRiskRow, SupplierEvaluation, SupplierOrderRow } from "@/lib/types";
import { formatDate, formatNumber } from "@/lib/utils";

const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);

/* ------------------------------------------------------------------ items at risk (V3 → V4) */

function AtRiskTab({ selected, onSelect }: { selected: number | null; onSelect: (id: number | null) => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["supplier-intel", "at-risk"], queryFn: () => get<SupplierAtRiskRow[]>("/supplier-intelligence/at-risk") });
  if (isLoading || !data) return <Skeleton className="h-96" />;
  const noOption = data.filter((r) => r.deadline_days != null && r.n_likely === 0).length;
  const overdue = data.reduce((a, r) => a + r.overdue_orders, 0);
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Items at medium / high stockout risk" value={data.length} sub="from V3 stockout risk" />
        <Stat label="No supplier likely in time" value={<span data-testid="count-no-option">{noOption}</span>} tone={noOption ? "red" : "green"} sub="based on each supplier's delivery history" />
        <Stat label="Overdue orders on these items" value={overdue} tone={overdue ? "amber" : "default"} sub="already placed, not yet delivered" />
      </div>
      {selected != null && (
        <Card data-testid="item-suppliers">
          <CardHeader
            title={`Supplier options · ${data.find((r) => r.item.id === selected)?.item.name ?? ""}`}
            description="Can each supplier deliver before the projected stockout? Evidence: its own past orders."
            action={<Button size="sm" variant="ghost" onClick={() => onSelect(null)} aria-label="Close"><X /></Button>}
          />
          <CardBody><ItemSupplierOptionsView itemId={selected} /></CardBody>
        </Card>
      )}
      <Card>
        {!data.length ? (
          <EmptyState title="No items at medium or high stockout risk" description="Supplier options appear here when V3 flags an item." />
        ) : (
          <Table data-testid="at-risk-table">
            <THead>
              <tr>
                <TH>Item</TH>
                <TH>Risk</TH>
                <TH>Deadline</TH>
                <TH className="text-right">Suppliers likely in time</TH>
                <TH>Best option (history)</TH>
                <TH className="text-right">Open orders</TH>
                <TH>Finding</TH>
              </tr>
            </THead>
            <tbody>
              {data.map((r) => (
                <TR key={r.item.id} className={`cursor-pointer ${selected === r.item.id ? "bg-teal-50/60" : ""}`} onClick={() => onSelect(r.item.id)}>
                  <TD className="min-w-52"><span className="font-medium text-teal-800 hover:underline">{r.item.name}</span><div className="text-xs text-muted-foreground">{r.item.sku}</div></TD>
                  <TD><RiskBadge level={r.risk.risk_level} out={r.risk.out_of_stock} /></TD>
                  <TD className="whitespace-nowrap">{r.risk.out_of_stock ? <span className="text-red-600">Out of stock</span> : r.deadline_days != null ? `${r.deadline_days} days (${formatDate(r.risk.expected_stockout_date)})` : "—"}</TD>
                  <TD className="text-right tabular-nums">{r.deadline_days != null ? <span className={r.n_likely ? "" : "font-semibold text-red-600"}>{r.n_likely} / {r.n_suppliers}</span> : "—"}</TD>
                  <TD className="text-sm">
                    {r.best ? (
                      <>
                        {r.best.supplier} <span className="text-xs text-muted-foreground">₹{r.best.unit_price.toFixed(2)} · ~{r.best.typical_lead_time_days} d</span>
                        {r.best.verdict !== "no_deadline" && <div><VerdictBadge verdict={r.best.verdict} /></div>}
                      </>
                    ) : "—"}
                  </TD>
                  <TD className="text-right tabular-nums">{r.open_orders}{r.overdue_orders > 0 && <Badge tone="red" className="ml-1">{r.overdue_orders} overdue</Badge>}</TD>
                  <TD className="min-w-80 max-w-md text-xs text-muted-foreground">{r.headline}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ scorecards */

function ScorecardsTab() {
  const [windowDays, setWindowDays] = useState(365);
  const { data, isLoading } = useQuery({ queryKey: ["supplier-intel", "scorecards", windowDays], queryFn: () => get<Scorecards>("/supplier-intelligence/scorecards", { window_days: windowDays }), placeholderData: keepPreviousData });
  if (isLoading || !data) return <Skeleton className="h-96" />;
  const h = data.hospital;
  return (
    <div className="space-y-6">
      <Card data-testid="score-method">
        <CardHeader title="How the reliability score is calculated" description="One measured rate — no invented weights" />
        <CardBody className="grid gap-2 text-sm md:grid-cols-2">
          <p><b>Score</b> = {data.method.formula.replace("score = ", "")}</p>
          <p><b>OTIF</b>: {data.method.otif.replace("OTIF = ", "")}</p>
          <p className="text-muted-foreground">{data.method.decided}</p>
          <p className="text-muted-foreground">{data.method.prior} — currently <b className="text-foreground">{pct(data.prior, 1)}</b></p>
          <p className="text-muted-foreground">{data.method.interval}</p>
          <p className="text-muted-foreground">{data.method.grades}</p>
        </CardBody>
      </Card>
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <Select className="w-48" value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))} aria-label="Window">
            {[90, 180, 365, 730].map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </Select>
          <span className="text-sm text-muted-foreground">Hospital-wide: {formatNumber(h.orders)} orders · OTIF <b className="text-foreground">{pct(h.otif_rate)}</b> · on time <b className="text-foreground">{pct(h.on_time_rate)}</b></span>
        </div>
        <Table data-testid="scorecard-table">
          <THead>
            <tr>
              <TH>Supplier</TH>
              <TH>Reliability</TH>
              <TH className="text-right">Decided orders</TH>
              <TH className="text-right">OTIF (95% range)</TH>
              <TH className="text-right">On time</TH>
              <TH className="text-right">Avg days late</TH>
              <TH className="text-right">Lead time: quoted · median · 90%</TH>
              <TH className="text-right">Cancelled</TH>
              <TH className="text-right">Fill rate</TH>
              <TH className="text-right">Price stability</TH>
              <TH className="text-right">Overdue</TH>
            </tr>
          </THead>
          <tbody>
            {data.suppliers.map((r) => {
              const m = r.metrics;
              return (
                <TR key={r.supplier_id}>
                  <TD><Link href={`/suppliers/${r.supplier_id}`} className="font-medium hover:underline">{r.name}</Link><div className="text-xs text-muted-foreground">{r.code} · {r.city}</div></TD>
                  <TD>{m ? <GradeBadge score={m.reliability_score} grade={m.grade} limited={m.limited_evidence} /> : <Badge>No orders</Badge>}</TD>
                  <TD className="text-right tabular-nums">{m?.decided ?? 0}</TD>
                  <TD className="whitespace-nowrap text-right tabular-nums">{m ? <>{pct(m.otif_rate)} <span className="text-xs text-muted-foreground">({pct(m.otif_ci_low)}–{pct(m.otif_ci_high)})</span></> : "—"}</TD>
                  <TD className="text-right tabular-nums">{pct(m?.on_time_rate)}</TD>
                  <TD className="text-right tabular-nums">{m?.avg_days_late == null ? "—" : m.avg_days_late.toFixed(1)}</TD>
                  <TD className="whitespace-nowrap text-right tabular-nums">{m ? `${m.quoted_lead_time ?? "—"} · ${m.lead_time_median ?? "—"} · ${m.lead_time_p90 ?? "—"} d` : "—"}</TD>
                  <TD className="text-right tabular-nums">{pct(m?.cancellation_rate, 1)}</TD>
                  <TD className="text-right tabular-nums">{pct(m?.fill_rate, 1)}</TD>
                  <TD className="text-right tabular-nums" title={m?.price_change_pct != null ? `price trend ${pct(m.price_change_pct, 1)}` : undefined}>{pct(m?.price_stability)}</TD>
                  <TD className={`text-right tabular-nums ${m?.overdue ? "font-medium text-red-600" : ""}`}>{m?.overdue ?? 0}</TD>
                </TR>
              );
            })}
          </tbody>
        </Table>
        <p className="border-t px-5 py-3 text-xs text-muted-foreground">
          * limited evidence (fewer than 5 decided orders). Demo order history is synthetic (12 months imported-style + the simulated period) — not real supplier data.
        </p>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ orders */

function OrdersTab() {
  const { can } = useMe();
  const suppliers = useSuppliers();
  const [f, setF] = useState({ status: "active", supplier_id: "" });
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({
    queryKey: ["supplier-orders", f, page],
    queryFn: () => get<Page<SupplierOrderRow>>("/supplier-orders", { ...f, page, page_size: 50 }),
    placeholderData: keepPreviousData,
  });
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3 border-b p-4">
        <Select className="w-48" value={f.status} onChange={(e) => { setF({ ...f, status: e.target.value }); setPage(1); }} aria-label="Order status">
          <option value="active">Open (not complete)</option>
          <option value="overdue">Overdue</option>
          <option value="RECEIVED">Received</option>
          <option value="CANCELLED">Cancelled</option>
          <option value="all">All</option>
        </Select>
        <Select className="w-64" value={f.supplier_id} onChange={(e) => { setF({ ...f, supplier_id: e.target.value }); setPage(1); }} aria-label="Supplier">
          <option value="">All suppliers</option>
          {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </Select>
      </div>
      {isLoading || !data ? <Skeleton className="m-4 h-64" /> : !data.items.length ? <EmptyState title="No orders" /> : (
        <>
          <Table data-testid="orders-table">
            <THead>
              <tr>
                <TH>Order</TH><TH>Item</TH><TH>Supplier</TH><TH className="text-right">Qty (received)</TH><TH className="text-right">Price</TH>
                <TH>Ordered</TH><TH>Expected</TH><TH>Delivered</TH><TH>Status</TH><TH />
              </tr>
            </THead>
            <tbody>
              {data.items.map((o) => (
                <TR key={o.id}>
                  <TD className="font-mono text-xs">{o.reference}{o.is_synthetic && <Badge tone="violet" className="ml-1">synthetic</Badge>}</TD>
                  <TD>{o.item}<div className="text-xs text-muted-foreground">{o.sku}</div></TD>
                  <TD className="text-sm">{o.supplier}</TD>
                  <TD className="text-right tabular-nums">{formatNumber(o.quantity_ordered)} <span className="text-xs text-muted-foreground">({formatNumber(o.quantity_received)})</span></TD>
                  <TD className="text-right tabular-nums">₹{o.unit_price.toFixed(2)}</TD>
                  <TD className="whitespace-nowrap">{formatDate(o.ordered_date)}</TD>
                  <TD className="whitespace-nowrap">{formatDate(o.expected_date)}{o.overdue_days > 0 && <Badge tone="red" className="ml-1">{o.overdue_days} d overdue</Badge>}</TD>
                  <TD className="whitespace-nowrap text-sm">
                    {o.first_delivery_date ? <>{formatDate(o.first_delivery_date)} <span className="text-xs text-muted-foreground">({o.lead_time_days} d{o.days_late ? `, ${o.days_late} late` : ""})</span></> : "—"}
                  </TD>
                  <TD><Badge tone={o.status === "RECEIVED" ? "green" : o.status === "CANCELLED" ? "neutral" : o.status === "PARTIAL" ? "amber" : "blue"}>{o.status.toLowerCase()}</Badge></TD>
                  <TD className="whitespace-nowrap text-right">
                    {(o.status === "OPEN" || o.status === "PARTIAL") && (
                      <>
                        {can(PERM.STOCK_RECEIVE) && <ReceiveDialog consumableId={o.consumable_id} orderId={o.id} trigger={<Button size="sm" variant="ghost" aria-label={`Receive ${o.reference}`}><PackagePlus /></Button>} />}
                        {can(PERM.MANAGE_SUPPLIERS) && <CloseOrderDialog order={o} trigger={<Button size="sm" variant="ghost" aria-label={`Cancel ${o.reference}`}><XCircle /></Button>} />}
                      </>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
          <Pagination page={page} pageSize={50} total={data.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ evaluation */

function EvaluationTab() {
  const { data } = useQuery({ queryKey: ["supplier-intel", "evaluation"], queryFn: () => get<SupplierEvaluation>("/supplier-intelligence/evaluation") });
  if (!data) return <Skeleton className="h-96" />;
  if (!data.n_test || !data.lead_time || !data.delay) return <Card><EmptyState title="Not enough order history to evaluate yet" /></Card>;
  const LT: [keyof NonNullable<SupplierEvaluation["lead_time"]>, string][] = [["quoted", "Quoted lead time (catalogue)"], ["supplier_history", "Supplier history"], ["supplier_item_history", "Supplier × item history"]];
  const DL: [keyof NonNullable<SupplierEvaluation["delay"]>, string][] = [["hospital_rate", "Hospital-wide late rate"], ["supplier_rate", "Supplier late rate"], ["supplier_item_rate", "Supplier × item late rate"]];
  return (
    <div className="space-y-6">
      <div className="flex items-start gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm text-violet-900">
        <FlaskConical className="mt-0.5 size-4 shrink-0" />
        <span>
          Backtest on {formatNumber(data.n_test)} delivered orders placed {formatDate(data.test_start)} – {formatDate(data.test_end)}; each predicted only from orders
          whose outcome was known on its order date. {pct(data.late_rate_test)} of them arrived late. Demo order history is synthetic — the suppliers
          were generated with different reliabilities, so these numbers show the method works, not how real suppliers behave.
        </span>
      </div>
      <Card data-testid="leadtime-eval">
        <CardHeader title="Lead-time prediction" description="How many days will a delivery take? Lower MAE is better; the 90th percentile should cover ~90% of deliveries." />
        <Table>
          <THead><tr><TH>Predictor</TH><TH className="text-right">MAE (days)</TH><TH className="text-right">Bias (days)</TH><TH className="text-right">Within ±1 day</TH><TH className="text-right">Covered by 90th pct</TH></tr></THead>
          <tbody>
            {LT.map(([k, label]) => {
              const m = data.lead_time![k];
              return (
                <TR key={k}>
                  <TD>{label}</TD>
                  <TD className="text-right tabular-nums">{m.mae.toFixed(2)}</TD>
                  <TD className="text-right tabular-nums">{m.bias.toFixed(2)}</TD>
                  <TD className="text-right tabular-nums">{pct(m.within_1_day)}</TD>
                  <TD className="text-right tabular-nums">{pct(m.p90_coverage)}</TD>
                </TR>
              );
            })}
          </tbody>
        </Table>
      </Card>
      <Card data-testid="delay-eval">
        <CardHeader title="Delay prediction" description="Probability that the first delivery comes after the expected date. Lower Brier / log loss is better; ROC-AUC 0.5 = no skill." />
        <Table>
          <THead><tr><TH>Predictor</TH><TH className="text-right">Brier</TH><TH className="text-right">ROC-AUC</TH><TH className="text-right">Log loss</TH><TH className="text-right">Mean predicted</TH><TH className="text-right">Observed</TH></tr></THead>
          <tbody>
            {DL.map(([k, label]) => {
              const m = data.delay![k];
              return (
                <TR key={k}>
                  <TD>{label}</TD>
                  <TD className="text-right tabular-nums">{m.brier.toFixed(4)}</TD>
                  <TD className="text-right tabular-nums">{m.roc_auc == null ? "—" : m.roc_auc.toFixed(2)}</TD>
                  <TD className="text-right tabular-nums">{m.log_loss.toFixed(3)}</TD>
                  <TD className="text-right tabular-nums">{pct(m.mean_predicted, 1)}</TD>
                  <TD className="text-right tabular-nums">{pct(m.observed_rate, 1)}</TD>
                </TR>
              );
            })}
          </tbody>
        </Table>
      </Card>
      <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">{data.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </div>
  );
}

/* ------------------------------------------------------------------ page */

function Inner() {
  const { can } = useMe();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = params.get("tab") ?? "at-risk";
  const selected = Number(params.get("item")) || null;
  const setParams = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params.toString());
    Object.entries(next).forEach(([k, v]) => (v == null ? p.delete(k) : p.set(k, v)));
    router.replace(`${pathname}?${p.toString()}`, { scroll: false });
  };
  return (
    <>
      <PageHeader
        title="Supplier intelligence"
        description="Which suppliers can be relied on to prevent a predicted stockout? Reliability measured from actual orders and deliveries — information for procurement, nothing is ordered automatically."
        actions={can(PERM.MANAGE_SUPPLIERS) && <RecordOrderDialog trigger={<Button><ClipboardPlus /> Record order</Button>} />}
      />
      <Tabs.Root value={tab} onValueChange={(v) => setParams({ tab: v, item: null })}>
        <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b">
          {[["at-risk", "Items at risk"], ["scorecards", "Supplier scorecards"], ["orders", "Orders & deliveries"], ["evaluation", "Evaluation"]].map(([k, label]) => (
            <Tabs.Trigger key={k} value={k}
              className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800">
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="at-risk"><AtRiskTab selected={selected} onSelect={(id) => setParams({ item: id == null ? null : String(id) })} /></Tabs.Content>
        <Tabs.Content value="scorecards"><ScorecardsTab /></Tabs.Content>
        <Tabs.Content value="orders"><OrdersTab /></Tabs.Content>
        <Tabs.Content value="evaluation"><EvaluationTab /></Tabs.Content>
      </Tabs.Root>
    </>
  );
}

export default function SupplierIntelligencePage() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
