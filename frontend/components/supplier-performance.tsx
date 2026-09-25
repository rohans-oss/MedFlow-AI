"use client";

import { useQuery } from "@tanstack/react-query";
import { Star } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { GradeBadge } from "@/components/badges";
import { LeadTimeHistogram, PriceHistoryChart, ReliabilityTrendChart } from "@/components/charts/supplier-charts";
import { Badge, Card, CardBody, CardHeader, EmptyState, Select, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { get } from "@/lib/api";
import type { SupplierDetailPerf } from "@/lib/types";
import { formatDate, formatNumber } from "@/lib/utils";

const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);

/** V4: evidence-based supplier performance — every component shown next to the score it produces. */
export function SupplierPerformance({ supplierId }: { supplierId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["supplier-intel", "supplier", supplierId],
    queryFn: () => get<SupplierDetailPerf>(`/supplier-intelligence/suppliers/${supplierId}`),
  });
  const [itemId, setItemId] = useState<number | null>(null);
  if (isLoading || !data) return <Skeleton className="h-72" />;
  const m = data.metrics;
  if (!m.orders) return <Card><EmptyState title="No order history yet" description="Reliability appears once orders are recorded and delivered (Supplier intelligence → Record order)." /></Card>;
  const withHistory = data.items.filter((i) => i.price_history.length > 1);
  const priceItem = withHistory.find((i) => i.consumable_id === itemId) ?? withHistory[0];
  const components: [string, string, string][] = [
    ["On time and in full (OTIF)", `${pct(m.otif_rate, 1)}`, `${m.otif_successes} of ${m.decided} decided orders · 95% range ${pct(m.otif_ci_low)}–${pct(m.otif_ci_high)}`],
    ["On-time delivery", pct(m.on_time_rate, 1), m.avg_days_late != null ? `late deliveries average ${m.avg_days_late.toFixed(1)} days late` : "no late deliveries"],
    ["Lead time (median · 90%)", `${m.lead_time_median ?? "—"} · ${m.lead_time_p90 ?? "—"} d`, `quoted ${m.quoted_lead_time ?? "—"} d`],
    ["Cancellation rate", pct(m.cancellation_rate, 1), `${m.cancelled} of ${m.received + m.cancelled} closed orders`],
    ["Quantity fill rate", pct(m.fill_rate, 1), `${pct(m.in_full_rate)} of received orders complete`],
    ["Price stability", pct(m.price_stability), m.price_change_pct != null ? `${m.price_changes} of ${m.price_pairs} consecutive orders moved >2% · trend ${m.price_change_pct >= 0 ? "+" : ""}${pct(m.price_change_pct, 1)}` : "—"],
  ];
  return (
    <div className="space-y-6" data-testid="supplier-performance">
      <Card>
        <CardHeader
          title={<span className="flex items-center gap-2">Reliability <GradeBadge score={m.reliability_score} grade={m.grade} limited={m.limited_evidence} /></span>}
          description={`Last ${data.window_days} days · ${formatNumber(m.orders)} orders (${m.open} open, ${m.overdue} overdue) · score = 100 × (OTIF orders + 5 × ${pct(data.prior, 1)}) ÷ (decided orders + 5)`}
        />
        <CardBody>
          <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3" data-testid="score-components">
            {components.map(([k, v, sub]) => (
              <div key={k} className="rounded-lg border p-3">
                <dt className="text-xs text-muted-foreground">{k}</dt>
                <dd className="mt-0.5 text-lg font-semibold tabular-nums">{v}</dd>
                <dd className="text-xs text-muted-foreground">{sub}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            The score is the OTIF rate itself, pulled towards the hospital-wide rate ({pct(data.prior, 1)}) by 5 pseudo-orders so a supplier with
            only a few orders cannot look perfect. The other components explain it; they are not weighted into it.
          </p>
        </CardBody>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Reliability by month" description="Share of that month's decided orders" />
          <CardBody><ReliabilityTrendChart data={data.monthly} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Actual delivery time" description="Days from order to first delivery vs the quoted lead time" />
          <CardBody><LeadTimeHistogram data={data.lead_time_histogram} quoted={m.quoted_lead_time} /></CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Reliability per item" description="The same components for each item this supplier delivers" />
        <Table data-testid="supplier-items-table">
          <THead>
            <tr>
              <TH>Item</TH><TH className="text-right">Price now</TH><TH className="text-right">Orders</TH><TH>Reliability</TH>
              <TH className="text-right">On time</TH><TH className="text-right">Lead time quoted · median · 90%</TH><TH className="text-right">Cancelled</TH><TH className="text-right">Price trend</TH>
            </tr>
          </THead>
          <tbody>
            {data.items.map((i) => (
              <TR key={i.consumable_id} className="cursor-pointer" onClick={() => setItemId(i.consumable_id)}>
                <TD>
                  <Link href={`/inventory/${i.consumable_id}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>{i.name}</Link>
                  {i.is_preferred && <Star className="ml-1 inline size-3 fill-amber-400 text-amber-500" aria-label="Preferred" />}
                  <div className="text-xs text-muted-foreground">{i.sku}</div>
                </TD>
                <TD className="text-right tabular-nums">{i.catalogue_price == null ? "—" : `₹${i.catalogue_price.toFixed(2)}`}</TD>
                <TD className="text-right tabular-nums">{i.metrics?.orders ?? 0}</TD>
                <TD>{i.metrics ? <GradeBadge score={i.metrics.reliability_score} grade={i.metrics.grade} limited={i.metrics.limited_evidence} /> : <Badge>No orders</Badge>}</TD>
                <TD className="text-right tabular-nums">{pct(i.metrics?.on_time_rate)}</TD>
                <TD className="whitespace-nowrap text-right tabular-nums">{i.quoted_lead_time_days ?? "—"} · {i.metrics?.lead_time_median ?? "—"} · {i.metrics?.lead_time_p90 ?? "—"} d</TD>
                <TD className="text-right tabular-nums">{pct(i.metrics?.cancellation_rate)}</TD>
                <TD className="text-right tabular-nums">{i.metrics?.price_change_pct == null ? "—" : `${i.metrics.price_change_pct >= 0 ? "+" : ""}${pct(i.metrics.price_change_pct, 1)}`}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>

      {priceItem && (
        <Card>
          <CardHeader
            title="Price history"
            description="Unit price paid per order"
            action={
              <Select className="w-64" value={priceItem.consumable_id} onChange={(e) => setItemId(Number(e.target.value))} aria-label="Item for price history">
                {withHistory.map((i) => <option key={i.consumable_id} value={i.consumable_id}>{i.name}</option>)}
              </Select>
            }
          />
          <CardBody><PriceHistoryChart data={priceItem.price_history} unit={priceItem.unit} /></CardBody>
        </Card>
      )}

      <Card>
        <CardHeader title="Recent orders" description="Order evidence behind the score (latest 25)" />
        <Table>
          <THead><tr><TH>Order</TH><TH>Item</TH><TH className="text-right">Qty (received)</TH><TH>Ordered</TH><TH>Expected</TH><TH>Delivered</TH><TH>Status</TH></tr></THead>
          <tbody>
            {data.recent_orders.map((o) => (
              <TR key={o.id}>
                <TD className="font-mono text-xs">{o.reference}</TD>
                <TD>{o.item}</TD>
                <TD className="text-right tabular-nums">{formatNumber(o.quantity_ordered)} ({formatNumber(o.quantity_received)})</TD>
                <TD className="whitespace-nowrap">{formatDate(o.ordered_date)}</TD>
                <TD className="whitespace-nowrap">{formatDate(o.expected_date)}{o.overdue_days > 0 && <Badge tone="red" className="ml-1">{o.overdue_days} d overdue</Badge>}</TD>
                <TD className="whitespace-nowrap">{o.first_delivery_date ? `${formatDate(o.first_delivery_date)}${o.days_late ? ` (${o.days_late} d late)` : ""}` : "—"}</TD>
                <TD><Badge tone={o.status === "RECEIVED" ? "green" : o.status === "CANCELLED" ? "neutral" : o.status === "PARTIAL" ? "amber" : "blue"}>{o.status.toLowerCase()}</Badge></TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
