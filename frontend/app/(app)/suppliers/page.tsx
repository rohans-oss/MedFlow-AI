"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { GradeBadge } from "@/components/badges";
import { SupplierDialog } from "@/components/forms/entity-dialogs";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useSuppliers } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get } from "@/lib/api";
import type { Scorecards } from "@/lib/types";
import { timeAgo } from "@/lib/utils";

export default function SuppliersPage() {
  const { can } = useMe();
  const { data, isLoading } = useSuppliers();
  const scores = useQuery({ queryKey: ["supplier-intel", "scorecards", 365], queryFn: () => get<Scorecards>("/supplier-intelligence/scorecards") });
  const score = new Map(scores.data?.suppliers.map((r) => [r.supplier_id, r.metrics]) ?? []);
  const [search, setSearch] = useState("");
  const s = search.toLowerCase();
  const rows = (data ?? []).filter((x) => !s || x.name.toLowerCase().includes(s) || x.code.toLowerCase().includes(s) || x.city?.toLowerCase().includes(s));

  return (
    <>
      <PageHeader
        title="Suppliers"
        description="Supplier directory, catalogue and measured reliability (on time and in full, last 365 days)."
        actions={can(PERM.MANAGE_SUPPLIERS) && <SupplierDialog trigger={<Button><Plus /> New supplier</Button>} />}
      />
      <Card>
        <div className="border-b p-4">
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input placeholder="Search name, code or city" className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>
        {isLoading ? (
          <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : !rows.length ? (
          <EmptyState title="No suppliers" />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Supplier</TH>
                <TH>Reliability</TH>
                <TH className="text-right">On time</TH>
                <TH>City</TH>
                <TH>Contact</TH>
                <TH className="text-right">Items listed</TH>
                <TH className="text-right">Default lead time</TH>
                <TH className="text-right">Deliveries (90d)</TH>
                <TH>Last delivery</TH>
              </tr>
            </THead>
            <tbody>
              {rows.map((x) => (
                <TR key={x.id}>
                  <TD>
                    <Link href={`/suppliers/${x.id}`} className="font-medium hover:underline">{x.name}</Link>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {x.code} {!x.is_active && <Badge>Inactive</Badge>}
                    </div>
                  </TD>
                  <TD>{score.get(x.id) ? <GradeBadge score={score.get(x.id)!.reliability_score} grade={score.get(x.id)!.grade} limited={score.get(x.id)!.limited_evidence} /> : <span className="text-xs text-muted-foreground">no orders</span>}</TD>
                  <TD className="text-right tabular-nums">{score.get(x.id)?.on_time_rate == null ? "—" : `${Math.round(score.get(x.id)!.on_time_rate! * 100)}%`}</TD>
                  <TD>{x.city ?? "—"}</TD>
                  <TD className="text-sm">
                    {x.contact_person ?? "—"}
                    <div className="text-xs text-muted-foreground">{x.phone ?? x.email ?? ""}</div>
                  </TD>
                  <TD className="text-right tabular-nums">{x.product_count}</TD>
                  <TD className="text-right tabular-nums">{x.default_lead_time_days} d</TD>
                  <TD className="text-right tabular-nums">{x.receipts_90d}</TD>
                  <TD className="text-muted-foreground">{timeAgo(x.last_receipt_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
