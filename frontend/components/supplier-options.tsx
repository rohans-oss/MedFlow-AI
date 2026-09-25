"use client";

import { useQuery } from "@tanstack/react-query";
import { Star } from "lucide-react";
import Link from "next/link";

import { GradeBadge, VerdictBadge } from "@/components/badges";
import { Badge, EmptyState, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { ApiError, get } from "@/lib/api";
import type { ItemSupplierOptions } from "@/lib/types";
import { formatDate, formatNumber } from "@/lib/utils";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

/** V4: every supplier of an item — price, MOQ, quoted vs actual delivery, reliability for THIS item, and whether
 *  it can deliver before the V3 projected stockout based on its own history. Information only; nothing is ordered. */
export function ItemSupplierOptionsView({ itemId, compact = false }: { itemId: number; compact?: boolean }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["supplier-intel", "item", itemId],
    queryFn: () => get<ItemSupplierOptions>(`/supplier-intelligence/items/${itemId}`),
  });
  if (error) return <EmptyState title="No supplier data" description={error instanceof ApiError ? error.message : undefined} />;
  if (isLoading || !data) return <Skeleton className="h-48" />;
  if (!data.options.length) return <EmptyState title="No supplier lists this item" />;
  const unit = data.item.unit;
  return (
    <div className="space-y-4" data-testid="supplier-options">
      <ul className="list-disc space-y-1 pl-5 text-sm" data-testid="supplier-summary">
        {data.summary.slice(0, compact ? 4 : undefined).map((s) => <li key={s}>{s}</li>)}
      </ul>
      <Table>
        <THead>
          <tr>
            <TH>Supplier</TH>
            <TH className="text-right">Price / {unit}</TH>
            <TH className="text-right">MOQ</TH>
            <TH className="text-right">Delivery: quoted · typical · 90%</TH>
            <TH className="text-right">On time</TH>
            <TH>Reliability</TH>
            {data.deadline_days != null && <TH className="text-right">In {data.deadline_days} days</TH>}
            {data.deadline_days != null && <TH>Before stockout?</TH>}
          </tr>
        </THead>
        <tbody>
          {data.options.map((o) => (
            <TR key={o.supplier_id} className={o.is_active ? "" : "opacity-60"}>
              <TD>
                <Link href={`/suppliers/${o.supplier_id}`} className="font-medium hover:underline">{o.supplier}</Link>
                {o.is_preferred && <Star className="ml-1 inline size-3 fill-amber-400 text-amber-500" aria-label="Preferred" />}
                <div className="text-xs text-muted-foreground">
                  evidence: {o.evidence_orders} orders{o.evidence_basis === "item" ? " for this item" : o.evidence_basis === "supplier" ? " (all items)" : ""}
                </div>
              </TD>
              <TD className="text-right tabular-nums">
                ₹{o.unit_price.toFixed(2)}
                <div className="text-xs text-muted-foreground">{o.price_vs_cheapest ? `+${Math.round(o.price_vs_cheapest * 100)}% vs cheapest` : "cheapest"}</div>
              </TD>
              <TD className="text-right tabular-nums">{formatNumber(o.moq)}</TD>
              <TD className="whitespace-nowrap text-right tabular-nums">
                {o.quoted_lead_time_days} · <b>{o.typical_lead_time_days}</b> · {o.p90_lead_time_days} d
                <div className="text-xs text-muted-foreground">arrives ~{formatDate(o.arrives_typically)}</div>
              </TD>
              <TD className="text-right tabular-nums">{pct(o.on_time_rate)}</TD>
              <TD><GradeBadge score={o.reliability_score} grade={o.grade} limited={o.evidence_orders < 5} /></TD>
              {data.deadline_days != null && (
                <TD className="text-right tabular-nums">
                  {o.in_time_n ? <>{o.in_time_k}/{o.in_time_n}<div className="text-xs text-muted-foreground">{pct(o.p_in_time)}</div></> : "—"}
                </TD>
              )}
              {data.deadline_days != null && <TD><VerdictBadge verdict={o.verdict} /></TD>}
            </TR>
          ))}
        </tbody>
      </Table>
      {data.open_orders.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-semibold">Orders in transit</h4>
          <Table>
            <THead><tr><TH>Order</TH><TH>Supplier</TH><TH className="text-right">Outstanding</TH><TH>Expected</TH><TH>Predicted arrival</TH><TH>Before stockout?</TH></tr></THead>
            <tbody>
              {data.open_orders.map((o) => (
                <TR key={o.id}>
                  <TD className="font-mono text-xs">{o.reference}</TD>
                  <TD>{o.supplier}</TD>
                  <TD className="text-right tabular-nums">{formatNumber(o.quantity_ordered - o.quantity_received)} {unit}</TD>
                  <TD className="whitespace-nowrap">{formatDate(o.expected_date)} {o.overdue_days > 0 && <Badge tone="red" className="ml-1">{o.overdue_days} d overdue</Badge>}</TD>
                  <TD className="whitespace-nowrap">{formatDate(o.predicted_arrival)}</TD>
                  <TD className="tabular-nums">{o.p_before_stockout == null ? <span className="text-xs text-muted-foreground">{o.overdue_days > 0 ? "no comparable delay in history" : "—"}</span> : `${pct(o.p_before_stockout)} (${o.before_stockout_k}/${o.before_stockout_n})`}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Typical = median of actual delivery times; 90% = 90th percentile. &quot;In N days&quot; counts past orders that arrived within the
        time left before the projected stockout (cancelled orders count as not arriving). Nothing is ordered automatically.
      </p>
    </div>
  );
}
