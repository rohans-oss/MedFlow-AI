"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Check, CheckCheck, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ALERT_TYPE_LABEL, AlertStatusBadge, SeverityBadge } from "@/components/badges";
import { Button, Card, EmptyState, PageHeader, Pagination, Select, Skeleton } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, post } from "@/lib/api";
import type { Alert, Page } from "@/lib/types";
import { timeAgo } from "@/lib/utils";

const PAGE_SIZE = 25;

function AlertsList() {
  const params = useSearchParams();
  const { can } = useMe();
  const manage = can(PERM.MANAGE_ALERTS);
  const [status, setStatus] = useState("active");
  const [type, setType] = useState(params.get("type") ?? "");
  const [severity, setSeverity] = useState("");
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: ["alerts", "list", status, type, severity, page],
    queryFn: () => get<Page<Alert>>("/alerts", { status, alert_type: type, severity, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const keys = [["alerts"], ["dashboard"], ["inventory"]];
  const ack = useAction((id: number) => post(`/alerts/${id}/acknowledge`), { invalidate: keys, success: "Alert acknowledged" });
  const resolve = useAction((id: number) => post(`/alerts/${id}/resolve`), { invalidate: keys, success: "Alert resolved" });
  const evaluate = useAction(() => post<{ created: number; resolved: number; open_total: number }>("/alerts/evaluate"), {
    invalidate: keys,
    success: (r) => `Evaluation complete: ${r.created} new, ${r.resolved} auto-resolved, ${r.open_total} active`,
  });

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Rule-based: out of stock, below reorder level, expired and expiring batches. Alerts auto-resolve when the condition clears."
        actions={
          manage && (
            <Button variant="outline" onClick={() => evaluate.mutate()} disabled={evaluate.isPending}>
              <RefreshCw className={evaluate.isPending ? "animate-spin" : ""} /> Re-evaluate now
            </Button>
          )
        }
      />
      <Card>
        <div className="flex flex-wrap gap-3 border-b p-4">
          <Select className="w-52" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status">
            <option value="active">Active (open + ack.)</option>
            <option value="OPEN">Open</option>
            <option value="ACKNOWLEDGED">Acknowledged</option>
            <option value="RESOLVED">Resolved</option>
            <option value="all">All</option>
          </Select>
          <Select className="w-44" value={type} onChange={(e) => { setType(e.target.value); setPage(1); }} aria-label="Type">
            <option value="">All types</option>
            {Object.entries(ALERT_TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <Select className="w-40" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }} aria-label="Severity">
            <option value="">All severities</option>
            {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => <option key={s} value={s}>{s.charAt(0) + s.slice(1).toLowerCase()}</option>)}
          </Select>
        </div>
        {isLoading ? (
          <div className="space-y-2 p-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>
        ) : !data?.items.length ? (
          <EmptyState title="No alerts" description="Nothing needs attention with these filters." />
        ) : (
          <>
            <ul className="divide-y">
              {data.items.map((a) => (
                <li key={a.id} className="flex flex-wrap items-start gap-4 px-5 py-4" data-testid="alert-row">
                  <div className="w-20 shrink-0 pt-0.5"><SeverityBadge severity={a.severity} /></div>
                  <div className="min-w-64 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{a.title}</span>
                      <AlertStatusBadge status={a.status} />
                    </div>
                    <p className="mt-0.5 text-sm text-muted-foreground">{a.message}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {ALERT_TYPE_LABEL[a.alert_type]} · raised {timeAgo(a.created_at)}
                      {a.resolved_at && ` · resolved ${timeAgo(a.resolved_at)}`}
                      {a.consumable && (
                        <>
                          {" · "}
                          <Link href={`/inventory/${a.consumable.id}`} className="font-medium text-teal-700 hover:underline">Open item</Link>
                        </>
                      )}
                    </p>
                  </div>
                  {manage && a.status !== "RESOLVED" && (
                    <div className="flex gap-2">
                      {a.status === "OPEN" && (
                        <Button size="sm" variant="outline" onClick={() => ack.mutate(a.id)}><Check /> Acknowledge</Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => resolve.mutate(a.id)} title="If the condition still holds, a new alert is raised on next evaluation">
                        <CheckCheck /> Resolve
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
            <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
          </>
        )}
      </Card>
    </>
  );
}

export default function AlertsPage() {
  return (
    <Suspense>
      <AlertsList />
    </Suspense>
  );
}
