"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { PackageMinus, PackagePlus, Search } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { MovementBadge } from "@/components/badges";
import { IssueDialog, ReceiveDialog } from "@/components/forms/stock-dialogs";
import { Button, Card, EmptyState, Input, PageHeader, Pagination, Select, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useConsumables, useDepartments, useSuppliers } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get } from "@/lib/api";
import type { Movement, Page } from "@/lib/types";
import { formatDateTime, formatNumber } from "@/lib/utils";

const PAGE_SIZE = 50;

function Ledger() {
  const params = useSearchParams();
  const { can } = useMe();
  const depts = useDepartments(true);
  const suppliers = useSuppliers();
  const items = useConsumables();
  const [f, setF] = useState({
    search: "",
    consumable_id: params.get("consumable_id") ?? "",
    department_id: "",
    supplier_id: "",
    movement_type: "",
    date_from: "",
    date_to: "",
  });
  const [debounced, setDebounced] = useState(f);
  const [page, setPage] = useState(1);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(f);
      setPage(1);
    }, 250);
    return () => clearTimeout(t);
  }, [f]);

  const { data, isLoading } = useQuery({
    queryKey: ["movements", debounced, page],
    queryFn: () => get<Page<Movement>>("/inventory/movements", { ...debounced, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  return (
    <>
      <PageHeader
        title="Stock movements"
        description="Append-only ledger. Every receipt, issue, return, wastage and count adjustment — who, when, which batch."
        actions={
          <>
            {can(PERM.STOCK_ISSUE, PERM.STOCK_ISSUE_OWN) && <IssueDialog trigger={<Button variant="outline"><PackageMinus /> Issue</Button>} />}
            {can(PERM.STOCK_RECEIVE) && <ReceiveDialog trigger={<Button><PackagePlus /> Receive</Button>} />}
          </>
        }
      />
      <Card>
        <div className="grid gap-3 border-b p-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input placeholder="Item, SKU or reference" className="pl-9" value={f.search} onChange={set("search")} />
          </div>
          <Select value={f.movement_type} onChange={set("movement_type")} aria-label="Type">
            <option value="">All types</option>
            {["RECEIPT", "ISSUE", "RETURN", "WASTAGE", "ADJUSTMENT"].map((t) => (
              <option key={t} value={t}>{t.charAt(0) + t.slice(1).toLowerCase()}</option>
            ))}
          </Select>
          <Select value={f.consumable_id} onChange={set("consumable_id")} aria-label="Item">
            <option value="">All items</option>
            {items.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          <Select value={f.department_id} onChange={set("department_id")} aria-label="Department">
            <option value="">All departments</option>
            {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </Select>
          <Select value={f.supplier_id} onChange={set("supplier_id")} aria-label="Supplier">
            <option value="">All suppliers</option>
            {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>
          <Input type="date" value={f.date_from} onChange={set("date_from")} aria-label="From date" />
          <Input type="date" value={f.date_to} onChange={set("date_to")} aria-label="To date" />
          <Button
            variant="ghost"
            onClick={() => setF({ search: "", consumable_id: "", department_id: "", supplier_id: "", movement_type: "", date_from: "", date_to: "" })}
          >
            Clear filters
          </Button>
        </div>
        {isLoading ? (
          <div className="space-y-2 p-4">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-9" />)}</div>
        ) : !data?.items.length ? (
          <EmptyState title="No movements match these filters" />
        ) : (
          <>
            <Table>
              <THead>
                <tr>
                  <TH>When</TH>
                  <TH>Type</TH>
                  <TH>Item</TH>
                  <TH>Lot</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Balance after</TH>
                  <TH>Department / supplier</TH>
                  <TH>Reference / reason</TH>
                  <TH>By</TH>
                </tr>
              </THead>
              <tbody>
                {data.items.map((m) => (
                  <TR key={m.id}>
                    <TD className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(m.created_at)}</TD>
                    <TD><MovementBadge type={m.movement_type} /></TD>
                    <TD>
                      <Link href={`/inventory/${m.consumable.id}`} className="hover:underline">{m.consumable.name}</Link>
                    </TD>
                    <TD className="font-mono text-xs">{m.batch?.lot_number ?? "—"}</TD>
                    <TD className={`text-right font-medium tabular-nums ${m.quantity > 0 ? "text-emerald-700" : ""}`}>
                      {m.quantity > 0 ? "+" : ""}{formatNumber(m.quantity)} <span className="text-xs font-normal text-muted-foreground">{m.consumable.unit}</span>
                    </TD>
                    <TD className="text-right tabular-nums text-muted-foreground">{formatNumber(m.balance_after)}</TD>
                    <TD className="text-sm">{m.department?.name ?? m.supplier?.name ?? "—"}</TD>
                    <TD className="max-w-56 truncate text-xs text-muted-foreground" title={[m.reference, m.reason].filter(Boolean).join(" · ")}>
                      {[m.reference, m.reason].filter(Boolean).join(" · ") || "—"}
                    </TD>
                    <TD className="whitespace-nowrap text-xs">{m.performed_by?.full_name ?? "—"}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
          </>
        )}
      </Card>
    </>
  );
}

export default function MovementsPage() {
  return (
    <Suspense>
      <Ledger />
    </Suspense>
  );
}
