"use client";

import { useQuery } from "@tanstack/react-query";
import { Bell, Download, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { ExpiryText, StockStatusBadge } from "@/components/badges";
import { ConsumableDialog } from "@/components/forms/entity-dialogs";
import { IssueDialog, ReceiveDialog } from "@/components/forms/stock-dialogs";
import { Button, Card, EmptyState, Input, PageHeader, Select, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useCategories } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get } from "@/lib/api";
import type { InventoryRow, StockStatus } from "@/lib/types";
import { formatINR, formatNumber } from "@/lib/utils";

function toCsv(rows: InventoryRow[]) {
  const head = ["SKU", "Name", "Category", "Unit", "Usable", "Expired", "Reorder level", "Max level", "Status", "Stock value (INR)", "Next expiry"];
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = rows.map((r) =>
    [r.sku, r.name, r.category, r.unit, r.usable_stock, r.expired_stock, r.reorder_level, r.max_level, r.status, r.stock_value, r.next_expiry].map(esc).join(","),
  );
  return [head.join(","), ...lines].join("\n");
}

function InventoryList() {
  const params = useSearchParams();
  const { can } = useMe();
  const cats = useCategories();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState<string>(params.get("status") ?? "");
  const [inactive, setInactive] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["inventory", "list", inactive],
    queryFn: () => get<InventoryRow[]>("/inventory", { include_inactive: inactive }),
  });

  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    return (data ?? []).filter(
      (r) =>
        (!s || r.name.toLowerCase().includes(s) || r.sku.toLowerCase().includes(s)) &&
        (!category || String(r.category_id) === category) &&
        (!status || r.status === status),
    );
  }, [data, search, category, status]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of data ?? []) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [data]);

  const exportCsv = () => {
    const blob = new Blob([toCsv(rows)], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `inventory-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const statusTabs: [string, string][] = [
    ["", "All"],
    ["OUT_OF_STOCK", "Out of stock"],
    ["LOW", "Low"],
    ["OK", "Healthy"],
    ["OVERSTOCK", "Overstock"],
  ];

  return (
    <>
      <PageHeader
        title="Inventory"
        description="Usable stock excludes expired batches. Values at batch cost."
        actions={
          <>
            <Button variant="outline" onClick={exportCsv} disabled={!rows.length}><Download /> Export CSV</Button>
            {can(PERM.MANAGE_CATALOG) && <ConsumableDialog trigger={<Button><Plus /> New item</Button>} />}
          </>
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <div className="relative min-w-56 flex-1">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input placeholder="Search name or SKU" className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <Select className="w-52" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
            <option value="">All categories</option>
            {cats.data?.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" className="accent-teal-700" checked={inactive} onChange={(e) => setInactive(e.target.checked)} /> Show inactive
          </label>
        </div>
        <div className="flex gap-1 overflow-x-auto border-b px-4 py-2">
          {statusTabs.map(([k, label]) => (
            <button
              key={k || "all"}
              onClick={() => setStatus(k)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium ${status === k ? "bg-teal-50 text-teal-800" : "text-muted-foreground hover:bg-muted"}`}
            >
              {label}
              <span className="ml-1.5 tabular-nums opacity-70">{k ? (counts[k] ?? 0) : (data?.length ?? 0)}</span>
            </button>
          ))}
        </div>

        {isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState title="No items match" description="Try a different search or filter." />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Item</TH>
                <TH>Category</TH>
                <TH className="text-right">Usable</TH>
                <TH className="text-right">Reorder at</TH>
                <TH>Next expiry</TH>
                <TH className="text-right">Value</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {rows.map((r) => (
                <TR key={r.consumable_id} className={!r.is_active ? "opacity-60" : ""}>
                  <TD>
                    <Link href={`/inventory/${r.consumable_id}`} className="font-medium hover:underline">{r.name}</Link>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {r.sku}
                      {r.open_alerts > 0 && (
                        <span className="inline-flex items-center gap-0.5 text-orange-600"><Bell className="size-3" />{r.open_alerts}</span>
                      )}
                    </div>
                  </TD>
                  <TD className="text-muted-foreground">{r.category ?? "—"}</TD>
                  <TD className="text-right tabular-nums">
                    <span className="font-medium">{formatNumber(r.usable_stock)}</span> <span className="text-xs text-muted-foreground">{r.unit}</span>
                    {r.expired_stock > 0 && <div className="text-xs text-red-600">+{formatNumber(r.expired_stock)} expired</div>}
                  </TD>
                  <TD className="text-right tabular-nums text-muted-foreground">{formatNumber(r.reorder_level)}</TD>
                  <TD className="whitespace-nowrap text-sm">{r.next_expiry ? <ExpiryText date={r.next_expiry} /> : <span className="text-muted-foreground">—</span>}</TD>
                  <TD className="text-right tabular-nums">{formatINR(r.stock_value)}</TD>
                  <TD><StockStatusBadge status={r.status as StockStatus} /></TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-1">
                      {can(PERM.STOCK_ISSUE, PERM.STOCK_ISSUE_OWN) && r.is_active && (
                        <IssueDialog consumableId={r.consumable_id} trigger={<Button size="sm" variant="ghost">Issue</Button>} />
                      )}
                      {can(PERM.STOCK_RECEIVE) && r.is_active && (
                        <ReceiveDialog consumableId={r.consumable_id} trigger={<Button size="sm" variant="ghost">Receive</Button>} />
                      )}
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
        {!!rows.length && (
          <div className="border-t px-4 py-3 text-xs text-muted-foreground">
            {rows.length} items · {formatINR(rows.reduce((a, r) => a + r.stock_value, 0))} usable value
          </div>
        )}
      </Card>
    </>
  );
}

export default function InventoryPage() {
  return (
    <Suspense>
      <InventoryList />
    </Suspense>
  );
}
