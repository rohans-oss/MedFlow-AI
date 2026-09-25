"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Boxes, CalendarClock, IndianRupee, PackageMinus, PackagePlus } from "lucide-react";
import Link from "next/link";

import { MovementBadge, RiskBadge, StockStatusBadge } from "@/components/badges";
import { ConsumptionValueChart, RankedBars } from "@/components/charts/charts";
import { IssueDialog, ReceiveDialog } from "@/components/forms/stock-dialogs";
import { SupplyNetworkArt, WaveArt } from "@/components/art";
import { Button, Card, CardBody, CardHeader, EmptyState, Skeleton, Stat, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { PERM, useMe } from "@/hooks/use-me";
import { get } from "@/lib/api";
import type { DashboardSummary, ForecastOverview, RecommendationRow, RiskOverview } from "@/lib/types";
import { formatCompactINR, formatINR, formatNumber, timeAgo } from "@/lib/utils";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function today() {
  return new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
}

export default function DashboardPage() {
  const { can, me } = useMe();
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => get<DashboardSummary>("/dashboard/summary"),
    refetchInterval: 60_000,
  });

  const forecast = useQuery({
    queryKey: ["forecasts", "overview", 14],
    queryFn: () => get<ForecastOverview>("/forecasts", { days: 14 }),
  });
  const lowCover = [...(forecast.data?.items ?? [])]
    .filter((i) => i.days_of_cover != null)
    .sort((a, b) => (a.days_of_cover ?? 0) - (b.days_of_cover ?? 0))
    .slice(0, 5);
  const risk = useQuery({ queryKey: ["stockout-risks", "overview"], queryFn: () => get<RiskOverview>("/stockout-risks") });
  const atRisk = (risk.data?.items ?? []).filter((i) => i.risk_level !== "LOW").slice(0, 6);
  const pendingRecs = useQuery({
    queryKey: ["procurement", "recommendations", "PENDING"],
    queryFn: () => get<RecommendationRow[]>("/procurement/recommendations", { status: "PENDING" }),
  });
  const pendingOrders = (pendingRecs.data ?? []).filter((r) => r.lines.length).length;

  const alertsTotal = data ? Object.values(data.open_alerts).reduce((a, b) => a + (b ?? 0), 0) : 0;
  const consumption30 = data?.daily.reduce((a, d) => a + d.issued_value, 0) ?? 0;

  return (
    <>
      <section className="relative mb-6 overflow-hidden rounded-3xl bg-brand-900 text-white shadow-xl shadow-teal-950/25 ring-1 ring-white/10">
        <div className="pointer-events-none absolute inset-0" aria-hidden>
          <div className="absolute inset-0 bg-[radial-gradient(70%_120%_at_0%_0%,rgb(20_184_166/0.45),transparent_60%),radial-gradient(60%_120%_at_100%_100%,rgb(56_189_248/0.3),transparent_60%)]" />
          <SupplyNetworkArt className="absolute inset-y-0 right-0 h-full w-full opacity-45 sm:w-3/4" ecg={false} />
          <WaveArt className="absolute inset-x-0 bottom-0 h-20 w-full" />
        </div>
        <div className="relative flex flex-wrap items-end justify-between gap-5 p-6 sm:p-8">
          <div className="min-w-0 max-w-2xl">
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-teal-200/80" suppressHydrationWarning>
              {greeting()}
              {me?.full_name ? `, ${me.full_name.split(" ")[0]}` : ""} · {today()}
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Supply health</h1>
            <p className="mt-2 text-sm text-teal-50/75">
              {me?.hospital ? `${me.hospital.name} · last 30 days` : "Last 30 days"}
              {data ? ` · ${formatNumber(data.items_monitored)} items · ${alertsTotal} open alerts` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {can(PERM.STOCK_ISSUE, PERM.STOCK_ISSUE_OWN) && (
              <IssueDialog
                trigger={
                  <Button variant="outline" className="border-white/25 bg-white/10 text-white backdrop-blur hover:border-white/50 hover:bg-white/20 hover:text-white">
                    <PackageMinus /> Issue stock
                  </Button>
                }
              />
            )}
            {can(PERM.STOCK_RECEIVE) && (
              <ReceiveDialog
                trigger={
                  <Button className="bg-gradient-to-br from-teal-300 to-cyan-400 text-brand-950 shadow-teal-400/30 hover:brightness-105">
                    <PackagePlus /> Receive stock
                  </Button>
                }
              />
            )}
          </div>
        </div>
      </section>

      {isLoading || !data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      ) : (
        <div className="stagger space-y-6">
          <div className="stagger grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Items monitored"
              icon={<Boxes />}
              value={formatNumber(data.items_monitored)}
              sub={`${data.status_counts.OK} healthy · ${data.status_counts.OVERSTOCK} overstock`}
            />
            <Stat
              label="Out of stock / low"
              icon={<AlertTriangle />}
              tone={data.status_counts.OUT_OF_STOCK ? "red" : data.status_counts.LOW ? "amber" : "green"}
              value={
                <>
                  {data.status_counts.OUT_OF_STOCK} <span className="text-base font-normal text-muted-foreground">/</span>{" "}
                  <span className="text-amber-600">{data.status_counts.LOW}</span>
                </>
              }
              sub={`${alertsTotal} open alerts`}
            />
            <Stat
              label="Usable stock value"
              icon={<IndianRupee />}
              value={formatCompactINR(data.stock_value)}
              sub={`${formatCompactINR(consumption30)} consumed in 30 days`}
            />
            <Stat
              label="Expiry exposure"
              icon={<CalendarClock />}
              tone={data.expired_value > 0 ? "red" : data.expiring_soon_value > 0 ? "amber" : "default"}
              value={formatCompactINR(data.expiring_soon_value)}
              sub={data.expired_value > 0 ? `${formatINR(data.expired_value)} already expired` : "expiring within warning window"}
            />
          </div>

          <Card data-testid="dashboard-risk">
            <CardHeader
              title="Stockout risk · next 14 days"
              description={
                risk.data?.model
                  ? `${risk.data.counts.high} high · ${risk.data.counts.medium} medium · ${risk.data.counts.low} low risk · model ${risk.data.model.name}`
                  : (risk.data?.message ?? "Loading…")
              }
              action={
                <span className="flex flex-wrap justify-end gap-3">
                  <Link href="/procurement?tab=approvals" className="text-xs font-medium text-teal-700 hover:underline" data-testid="dashboard-procurement">
                    Procurement · {pendingOrders} order{pendingOrders === 1 ? "" : "s"} awaiting approval
                  </Link>
                  <Link href="/stockout-risks" className="text-xs font-medium text-teal-700 hover:underline">Open stockout risk</Link>
                </span>
              }
            />
            {risk.data?.model ? (
              atRisk.length ? (
                <ul className="divide-y">
                  {atRisk.map((i) => (
                    <li key={i.consumable_id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-2.5 text-sm transition-colors hover:bg-teal-50/40">
                      <span className="flex min-w-0 items-center gap-2">
                        <RiskBadge level={i.risk_level} out={i.out_of_stock} />
                        <Link href={`/stockout-risks?item=${i.consumable_id}`} className="truncate hover:underline">{i.name}</Link>
                      </span>
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        {Math.round(i.probability * 100)}% ·{" "}
                        {i.out_of_stock ? "out now" : i.expected_stockout_date ? `runs out ${i.expected_stockout_date} (${i.days_of_stock_remaining} d)` : "covered 30 d"}
                        {i.shortage_quantity > 0 && ` · ${formatNumber(i.shortage_quantity)} ${i.unit} short`}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No items at medium or high risk" />
              )
            ) : (
              <EmptyState title="Stockout risk appears after the forecasting and risk models are trained" />
            )}
          </Card>

          <Card data-testid="dashboard-forecast">
            <CardHeader
              title="Demand forecast · next 14 days"
              description={
                forecast.data?.model
                  ? `Model ${forecast.data.model.name} · holdout WAPE ${((forecast.data.model.metrics.wape ?? 0) * 100).toFixed(1)}% · data through ${forecast.data.model.data_end}`
                  : "No forecasting model trained yet"
              }
              action={<Link href="/forecasts" className="text-xs font-medium text-teal-700 hover:underline">Open forecasts</Link>}
            />
            {forecast.data?.model ? (
              <div className="grid gap-6 p-5 lg:grid-cols-3">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">Expected consumption value</p>
                  <p className="mt-1 text-2xl font-semibold tracking-tight">{formatCompactINR(forecast.data.total_value)}</p>
                  <p className="mt-1 text-xs text-muted-foreground">across {forecast.data.items.length} items, at catalogue cost</p>
                </div>
                <div className="lg:col-span-2">
                  <p className="mb-2 text-xs font-medium text-muted-foreground">Lowest stock cover at forecast demand</p>
                  <ul className="divide-y rounded-lg border">
                    {lowCover.map((i) => (
                      <li key={i.consumable_id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                        <Link href={`/forecasts?item=${i.consumable_id}`} className="truncate hover:underline">{i.name}</Link>
                        <span className="shrink-0 tabular-nums text-muted-foreground">
                          {formatNumber(Math.round(i.predicted_demand))} {i.unit} needed ·{" "}
                          <b className={(i.days_of_cover ?? 0) < 7 ? "text-red-600" : (i.days_of_cover ?? 0) < 14 ? "text-amber-700" : "text-foreground"}>
                            {Math.round(i.days_of_cover ?? 0)} d cover
                          </b>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            ) : (
              <EmptyState title="Train the forecasting models from the Forecasts page" />
            )}
          </Card>

          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader title="Daily consumption value (₹)" description="Stock issued to departments net of returns, valued at catalogue cost" />
              <CardBody>
                <ConsumptionValueChart data={data.daily} />
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Open alerts" action={<Link href="/alerts" className="text-xs font-medium text-teal-700 hover:underline">View all</Link>} />
              <CardBody className="space-y-3">
                {[
                  ["OUT_OF_STOCK", "Out of stock", "bg-red-600"],
                  ["LOW_STOCK", "Below reorder level", "bg-orange-500"],
                  ["EXPIRED", "Expired batches", "bg-red-400"],
                  ["EXPIRING_SOON", "Expiring soon", "bg-amber-400"],
                ].map(([k, label, dot]) => (
                  <Link key={k} href={`/alerts?type=${k}`} className="flex items-center justify-between rounded-xl border border-slate-200/80 bg-white/60 px-3 py-2.5 text-sm transition-all hover:translate-x-0.5 hover:border-teal-300 hover:bg-white hover:shadow-md">
                    <span className="flex items-center gap-2">
                      <span className={`size-2 rounded-full ${dot}`} />
                      {label}
                    </span>
                    <span className="font-semibold tabular-nums">{data.open_alerts[k as keyof typeof data.open_alerts] ?? 0}</span>
                  </Link>
                ))}
              </CardBody>
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader title="Needs attention" description="Out-of-stock and below-reorder items, most urgent first" />
              {data.critical_items.length === 0 ? (
                <EmptyState title="All items are above reorder level" />
              ) : (
                <Table>
                  <THead>
                    <tr>
                      <TH>Item</TH>
                      <TH className="text-right">Usable</TH>
                      <TH className="text-right">Reorder at</TH>
                      <TH>Status</TH>
                    </tr>
                  </THead>
                  <tbody>
                    {data.critical_items.map((r) => (
                      <TR key={r.consumable_id}>
                        <TD>
                          <Link href={`/inventory/${r.consumable_id}`} className="font-medium hover:underline">{r.name}</Link>
                          <div className="text-xs text-muted-foreground">{r.sku}</div>
                        </TD>
                        <TD className="text-right tabular-nums">{formatNumber(r.usable_stock)} {r.unit}</TD>
                        <TD className="text-right tabular-nums text-muted-foreground">{formatNumber(r.reorder_level)}</TD>
                        <TD><StockStatusBadge status={r.status} /></TD>
                      </TR>
                    ))}
                  </tbody>
                </Table>
              )}
            </Card>
            <Card>
              <CardHeader title="Consumption by department" description="Value issued, last 30 days" />
              <CardBody>
                {data.consumption_by_department.length ? <RankedBars data={data.consumption_by_department} money /> : <EmptyState title="No issues yet" />}
              </CardBody>
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-3">
            <Card>
              <CardHeader title="Top items by consumption value" description="Last 30 days" />
              <CardBody>
                {data.top_consumed.length ? <RankedBars data={data.top_consumed} money /> : <EmptyState title="No issues yet" />}
              </CardBody>
            </Card>
            <Card className="lg:col-span-2">
              <CardHeader title="Recent stock movements" action={<Link href="/movements" className="text-xs font-medium text-teal-700 hover:underline">Full ledger</Link>} />
              <Table>
                <tbody>
                  {data.recent_movements.map((m) => (
                    <TR key={m.id}>
                      <TD className="w-28"><MovementBadge type={m.movement_type} /></TD>
                      <TD>
                        <Link href={`/inventory/${m.consumable.id}`} className="hover:underline">{m.consumable.name}</Link>
                        <div className="text-xs text-muted-foreground">
                          {m.department?.name ?? m.supplier?.name ?? m.reason ?? "—"}
                        </div>
                      </TD>
                      <TD className={`text-right tabular-nums font-medium ${m.quantity < 0 ? "text-slate-700" : "text-emerald-700"}`}>
                        {m.quantity > 0 ? "+" : ""}
                        {formatNumber(m.quantity)}
                      </TD>
                      <TD className="text-right text-xs text-muted-foreground whitespace-nowrap">{timeAgo(m.created_at)}</TD>
                    </TR>
                  ))}
                </tbody>
              </Table>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
