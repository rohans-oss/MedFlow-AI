"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, EmptyState, PageHeader, Pagination, Select, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useMe, PERM } from "@/hooks/use-me";
import { get } from "@/lib/api";
import type { AuditEntry, Page } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const PAGE_SIZE = 50;
const ACTIONS = ["auth", "stock", "consumable", "supplier", "alert", "user", "department", "category", "hospital"];

function summarise(details: Record<string, unknown> | null): string {
  if (!details) return "";
  return Object.entries(details)
    .filter(([k]) => k !== "movement_ids")
    .map(([k, v]) => {
      if (v && typeof v === "object" && "from" in v && "to" in v) {
        const c = v as { from: unknown; to: unknown };
        return `${k}: ${String(c.from)} → ${String(c.to)}`;
      }
      return `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`;
    })
    .join(" · ");
}

export default function AuditLogsPage() {
  const { can, isLoading: meLoading } = useMe();
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const allowed = can(PERM.READ_AUDIT);
  const { data, isLoading } = useQuery({
    queryKey: ["audit", action, page],
    queryFn: () => get<Page<AuditEntry>>("/audit-logs", { action, page, page_size: PAGE_SIZE }),
    enabled: allowed,
    placeholderData: keepPreviousData,
  });

  if (!meLoading && !allowed) return <EmptyState title="Administrators only" description="You do not have access to the audit log." />;

  return (
    <>
      <PageHeader title="Audit log" description="Every change and sign-in, with who, when and what changed." />
      <Card>
        <div className="border-b p-4">
          <Select className="w-52" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} aria-label="Action">
            <option value="">All actions</option>
            {ACTIONS.map((a) => <option key={a} value={a}>{a}.*</option>)}
          </Select>
        </div>
        {isLoading ? (
          <div className="space-y-2 p-4">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-8" />)}</div>
        ) : !data?.items.length ? (
          <EmptyState title="No entries" />
        ) : (
          <>
            <Table>
              <THead><tr><TH>When</TH><TH>User</TH><TH>Action</TH><TH>Entity</TH><TH>Details</TH><TH>IP</TH></tr></THead>
              <tbody>
                {data.items.map((a) => (
                  <TR key={a.id}>
                    <TD className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(a.created_at)}</TD>
                    <TD className="whitespace-nowrap">{a.user?.full_name ?? "System"}</TD>
                    <TD><Badge tone={a.action.includes("failed") ? "red" : "neutral"} className="font-mono">{a.action}</Badge></TD>
                    <TD className="whitespace-nowrap text-xs">{a.entity_type}{a.entity_id ? ` #${a.entity_id}` : ""}</TD>
                    <TD className="max-w-md truncate text-xs text-muted-foreground" title={summarise(a.details)}>{summarise(a.details) || "—"}</TD>
                    <TD className="font-mono text-xs text-muted-foreground">{a.ip_address ?? "—"}</TD>
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
