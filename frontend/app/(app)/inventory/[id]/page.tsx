"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, PackageMinus, PackagePlus, Pencil, Star, TrendingUp } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ExpiryText, MovementBadge, SeverityBadge, StockStatusBadge } from "@/components/badges";
import { DailyIssueChart } from "@/components/charts/charts";
import { ConsumableDialog } from "@/components/forms/entity-dialogs";
import { BatchActionDialog, IssueDialog, ReceiveDialog } from "@/components/forms/stock-dialogs";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, Skeleton, Stat, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { PERM, useMe } from "@/hooks/use-me";
import { get } from "@/lib/api";
import type { Alert, InventoryDetail, Page } from "@/lib/types";
import { formatDate, formatDateTime, formatINR, formatNumber } from "@/lib/utils";

export default function InventoryDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useMe();
  const { data, isLoading, error } = useQuery({
    queryKey: ["inventory", "detail", Number(id)],
    queryFn: () => get<InventoryDetail>(`/inventory/${id}`),
  });
  const alerts = useQuery({
    queryKey: ["alerts", "item", Number(id)],
    queryFn: () => get<Page<Alert>>("/alerts", { consumable_id: id }),
  });

  if (error) return <EmptyState title="Item not found" action={<Button asChild variant="outline"><Link href="/inventory">Back to inventory</Link></Button>} />;
  if (isLoading || !data) return <Skeleton className="h-96" />;

  const { item, stock } = data;
  const canIssue = can(PERM.STOCK_ISSUE, PERM.STOCK_ISSUE_OWN);
  const canStore = can(PERM.STOCK_RECEIVE);

  return (
    <div className="space-y-6">
      <div>
        <Link href="/inventory" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Inventory
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold tracking-tight">{item.name}</h1>
              <StockStatusBadge status={stock.status} />
              {!item.is_active && <Badge>Inactive</Badge>}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {item.sku} · {item.category?.name ?? "Uncategorised"} · unit: {item.unit} · catalogue cost {formatINR(item.unit_cost, true)}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline"><Link href={`/forecasts?item=${item.id}`}><TrendingUp /> Forecast</Link></Button>
            {can(PERM.MANAGE_CATALOG) && <ConsumableDialog item={item} trigger={<Button variant="outline"><Pencil /> Edit</Button>} />}
            {canIssue && item.is_active && <IssueDialog consumableId={item.id} trigger={<Button variant="outline"><PackageMinus /> Issue</Button>} />}
            {canStore && item.is_active && <ReceiveDialog consumableId={item.id} trigger={<Button><PackagePlus /> Receive</Button>} />}
          </div>
        </div>
      </div>

      {!!alerts.data?.items.length && (
        <div className="space-y-2">
          {alerts.data.items.map((a) => (
            <div key={a.id} className="flex items-start gap-3 rounded-lg border border-orange-200 bg-orange-50/60 px-4 py-3 text-sm">
              <SeverityBadge severity={a.severity} />
              <div>
                <div className="font-medium">{a.title}</div>
                <div className="text-muted-foreground">{a.message}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Usable stock"
          value={`${formatNumber(stock.usable_stock)} ${item.unit}`}
          tone={stock.status === "OUT_OF_STOCK" ? "red" : stock.status === "LOW" ? "amber" : "default"}
          sub={`Reorder at ${formatNumber(item.reorder_level)}${item.max_level ? ` · max ${formatNumber(item.max_level)}` : ""}`}
        />
        <Stat
          label="Days of cover"
          value={data.days_of_cover === null ? "—" : `${data.days_of_cover} days`}
          sub={`Avg ${formatNumber(data.avg_daily_issue_30d)} ${item.unit}/day issued (30d)`}
        />
        <Stat label="Stock value" value={formatINR(stock.stock_value)} sub={`${stock.batch_count} batch${stock.batch_count === 1 ? "" : "es"} on hand`} />
        <Stat
          label="Expired on hand"
          value={`${formatNumber(stock.expired_stock)} ${item.unit}`}
          tone={stock.expired_stock ? "red" : "default"}
          sub={stock.expired_stock ? "Record wastage to remove" : "None"}
        />
      </div>

      <Card>
        <CardHeader title={`Units issued per day (${item.unit})`} description="Last 30 days, net of returns" />
        <CardBody>
          <DailyIssueChart data={data.daily} unit={item.unit} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Batches on hand" description="Issued first-expiry-first-out. Expired batches cannot be issued." />
        {data.batches.length === 0 ? (
          <EmptyState title="No stock on hand" />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Lot</TH>
                <TH>Expiry</TH>
                <TH className="text-right">Quantity</TH>
                <TH className="text-right">Unit cost</TH>
                <TH>Supplier</TH>
                <TH>Received</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {data.batches.map((b) => (
                <TR key={b.id} className={b.is_expired ? "bg-red-50/50" : ""}>
                  <TD className="font-mono text-xs">{b.lot_number}</TD>
                  <TD className="whitespace-nowrap"><ExpiryText date={b.expiry_date} days={b.days_to_expiry} /></TD>
                  <TD className="text-right tabular-nums">
                    {formatNumber(b.quantity)} <span className="text-xs text-muted-foreground">/ {formatNumber(b.initial_quantity)}</span>
                  </TD>
                  <TD className="text-right tabular-nums">{formatINR(b.unit_cost, true)}</TD>
                  <TD>{b.supplier?.name ?? "—"}</TD>
                  <TD className="text-muted-foreground">{formatDate(b.received_at)}</TD>
                  <TD>
                    <div className="flex justify-end gap-1">
                      {canIssue && (
                        <BatchActionDialog batch={b} unit={item.unit} mode="return" trigger={<Button size="sm" variant="ghost">Return</Button>} />
                      )}
                      {canStore && (
                        <>
                          <BatchActionDialog batch={b} unit={item.unit} mode="adjust" trigger={<Button size="sm" variant="ghost">Count</Button>} />
                          <BatchActionDialog
                            batch={b}
                            unit={item.unit}
                            mode="wastage"
                            trigger={<Button size="sm" variant={b.is_expired ? "danger" : "ghost"}>Wastage</Button>}
                          />
                        </>
                      )}
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardHeader title="Suppliers" description="Catalogue price, lead time and MOQ" />
          {data.suppliers.length === 0 ? (
            <EmptyState title="No supplier lists this item" />
          ) : (
            <Table>
              <tbody>
                {data.suppliers.map((s) => (
                  <TR key={s.id}>
                    <TD>
                      <Link href={`/suppliers/${s.supplier.id}`} className="inline-flex items-center gap-1 font-medium hover:underline">
                        {s.is_preferred && <Star className="size-3.5 fill-amber-400 text-amber-400" aria-label="Preferred" />}
                        {s.supplier.name}
                      </Link>
                      <div className="text-xs text-muted-foreground">
                        {s.lead_time_days}d lead · MOQ {formatNumber(s.moq)}
                      </div>
                    </TD>
                    <TD className="text-right tabular-nums">{formatINR(s.unit_price, true)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <Card className="lg:col-span-3">
          <CardHeader
            title="Recent movements"
            action={<Link href={`/movements?consumable_id=${item.id}`} className="text-xs font-medium text-teal-700 hover:underline">All movements</Link>}
          />
          <Table>
            <THead>
              <tr>
                <TH>When</TH>
                <TH>Type</TH>
                <TH className="text-right">Qty</TH>
                <TH className="text-right">Balance</TH>
                <TH>Detail</TH>
              </tr>
            </THead>
            <tbody>
              {data.recent_movements.map((m) => (
                <TR key={m.id}>
                  <TD className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(m.created_at)}</TD>
                  <TD><MovementBadge type={m.movement_type} /></TD>
                  <TD className="text-right tabular-nums">{m.quantity > 0 ? "+" : ""}{formatNumber(m.quantity)}</TD>
                  <TD className="text-right tabular-nums text-muted-foreground">{formatNumber(m.balance_after)}</TD>
                  <TD className="text-xs">
                    {m.department?.name ?? m.supplier?.name ?? ""}
                    {m.reason && <span className="text-muted-foreground"> · {m.reason}</span>}
                    {m.batch && <span className="text-muted-foreground"> · {m.batch.lot_number}</span>}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      </div>
    </div>
  );
}
