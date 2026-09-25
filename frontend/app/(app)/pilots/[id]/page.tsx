"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { ClassificationBanner, FeedbackForm, MetricTile, PilotNav, SECTION_TITLES } from "@/components/pilots";
import { Badge, Button, Card, CardBody, CardHeader, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, patch, post } from "@/lib/api";
import type { DecisionRow, PilotDashboard, PilotStatus } from "@/lib/types";
import { formatDate, timeAgo } from "@/lib/utils";

const NEXT: Record<PilotStatus, [PilotStatus, string][]> = {
  planned: [["active", "Start pilot"], ["cancelled", "Cancel"]],
  active: [["paused", "Pause"], ["completed", "Complete"], ["cancelled", "Cancel"]],
  paused: [["active", "Resume"], ["completed", "Complete"], ["cancelled", "Cancel"]],
  completed: [], cancelled: [],
};
const OUTCOME_TONE = { approved: "green", modified: "blue", rejected: "red", expired: "neutral", pending: "amber" } as const;

export default function PilotDashboardPage() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const { can } = useMe();
  const q = useQuery({ queryKey: ["pilots", pid, "dashboard"], queryFn: () => get<PilotDashboard>(`/pilots/${pid}/dashboard`) });
  const dec = useQuery({ queryKey: ["pilots", pid, "decisions"], queryFn: () => get<DecisionRow[]>(`/pilots/${pid}/decisions`) });
  const status = useAction((s: PilotStatus) => patch(`/pilots/${pid}`, { status: s }), { success: "Pilot updated", invalidate: [["pilots"]] });
  const sync = useAction(() => post<{ results: { source: string; error?: string; runs: { status: string }[] }[] }>(`/pilots/${pid}/sync`), {
    success: (r) => `Synced ${r.results.length} source(s) through V9`, invalidate: [["pilots"], ["integrations"]],
  });
  if (!q.data) return <Skeleton className="h-96" />;
  const { pilot, period } = q.data;
  const bySection = (s: string) => period.metrics.filter((m) => m.section === s);
  const pick = (s: string, keys: string[]) => bySection(s).filter((m) => keys.includes(m.key));
  const sections: [string, string[]][] = [
    ["data_quality", ["data_quality_pct", "records_received", "records_rejected", "dq_reconciliation", "dq_stale_sources"]],
    ["inventory", ["inventory_accuracy", "stock_discrepancies", "usable_stock", "expiring_stock", "expired_stock", "inventory_value"]],
    ["stockouts", ["stockout_days", "stockout_events", "affected_items", "emergency_stockouts"]],
    ["forecasting", ["forecast_wape", "forecast_bias", "holdout_wape", "forecast_coverage"]],
    ["stockout_prediction", ["warning_precision", "warning_recall", "events_warned", "warning_lead_days"]],
    ["supplier", ["otif_rate", "avg_days_late", "cancellation_rate", "fill_rate"]],
    ["procurement", ["recs_generated", "recs_approved", "recs_modified", "recs_rejected", "hours_to_decision", "emergency_purchases"]],
    ["adoption", ["active_users", "recommendation_views", "decisions_made", "feedback_submitted"]],
  ];
  const models = period.metrics.find((m) => m.key === "forecast_wape")?.models ?? [];
  return (
    <>
      <PilotNav pilot={pilot} />
      <ClassificationBanner pilot={pilot} />
      <div className="mb-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Pilot status" description={pilot.description ?? undefined}
            action={can(PERM.PILOTS_MANAGE) ? (
              <div className="flex flex-wrap gap-2">
                {NEXT[pilot.status].map(([s, label]) => (
                  <Button key={s} size="sm" variant={s === "cancelled" ? "ghost" : "outline"} onClick={() => status.mutate(s)} disabled={status.isPending}>{label}</Button>
                ))}
              </div>
            ) : undefined} />
          <CardBody className="grid gap-3 text-sm sm:grid-cols-2">
            <div><span className="text-muted-foreground">Hospital:</span> {pilot.hospital_name}</div>
            <div><span className="text-muted-foreground">Owner:</span> {pilot.owner?.name ?? "—"}</div>
            <div data-testid="baseline-dates"><span className="text-muted-foreground">Baseline:</span> {formatDate(pilot.baseline_start)} → {formatDate(pilot.baseline_end)}</div>
            <div><span className="text-muted-foreground">Pilot:</span> {formatDate(pilot.pilot_start)} → {formatDate(pilot.pilot_end)}
              {pilot.actual_end && ` (ended ${formatDate(pilot.actual_end)})`}{period.period.partial && <Badge tone="amber" className="ml-1">running</Badge>}</div>
            <div className="sm:col-span-2"><span className="text-muted-foreground">Departments:</span> {pilot.departments.map((d) => d.name).join(", ") || "—"}</div>
            <div className="sm:col-span-2"><span className="text-muted-foreground">Users:</span> {pilot.users.map((u) => u.name).join(", ") || "—"}</div>
            <div className="sm:col-span-2 flex flex-wrap items-center gap-2">
              <span className="text-muted-foreground">Data sources:</span> {pilot.data_sources.map((s) => s.name).join(", ") || "none"}
              {can(PERM.INTEGRATIONS_RUN) && pilot.data_sources.length > 0 && (
                <Button size="sm" variant="outline" onClick={() => sync.mutate(undefined)} disabled={sync.isPending} data-testid="pilot-sync">
                  {sync.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Sync REST sources (V9)
                </Button>
              )}
            </div>
            <p className="sm:col-span-2 text-xs text-muted-foreground">
              Ledger metrics use complete days up to {period.period.ledger_end ? formatDate(period.period.ledger_end) : "—"} ({period.period.ledger_days} days);
              activity metrics run up to now. Hover a tile to see its formula and source.
            </p>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Issues & readiness" />
          <CardBody className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-2" data-testid="issue-counts">
              <Badge tone="red">{q.data.issues.open} open</Badge><Badge tone="amber">{q.data.issues.investigating} investigating</Badge>
              <Badge tone="green">{q.data.issues.resolved} resolved</Badge><Badge>{q.data.issues.ignored} ignored</Badge>
            </div>
            <Link className="text-teal-800 hover:underline" href={`/pilots/${pid}/issues`}>Open issues →</Link>
            <div>Readiness: <strong>{q.data.readiness.done}</strong> of {q.data.readiness.total} items
              <Link className="ml-2 text-teal-800 hover:underline" href={`/pilots/${pid}/readiness`}>checklist →</Link></div>
          </CardBody>
        </Card>
      </div>
      <p className="mb-3 text-sm font-medium">Pilot period — {q.data.classification_label}</p>
      <div className="space-y-6" data-testid="pilot-dashboard">
        {sections.map(([s, keys]) => (
          <Card key={s}>
            <CardHeader title={SECTION_TITLES[s]} />
            <CardBody>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">{pick(s, keys).map((m) => <MetricTile key={m.key} m={m} />)}</div>
              {s === "forecasting" && models.length > 0 && (
                <p className="mt-3 text-xs text-muted-foreground">Model used (V2, unchanged): {models.map((m) => `${m.name} (${m.type}, data to ${formatDate(m.data_end)})`).join("; ")}</p>
              )}
              {s === "data_quality" && period.integration_sources.length > 0 && (
                <div className="mt-4 rounded-lg border">
                  <Table data-testid="reliability-table">
                    <THead><tr><TH>Source</TH><TH>Runs</TH><TH>Failed</TH><TH>Records</TH><TH>Rejected</TH><TH>Avg duration</TH><TH>Last success</TH><TH>Freshness</TH></tr></THead>
                    <tbody>
                      {period.integration_sources.map((x) => (
                        <TR key={x.id}>
                          <TD className="font-medium">{x.name}{x.is_simulated && <Badge tone="violet" className="ml-1">simulated</Badge>}</TD>
                          <TD className="tabular-nums">{x.runs}</TD><TD className="tabular-nums">{x.failed_runs}</TD>
                          <TD className="tabular-nums">{x.records_processed}</TD><TD className="tabular-nums">{x.records_rejected}</TD>
                          <TD className="text-xs">{x.avg_duration_s === null ? "—" : `${x.avg_duration_s} s`}</TD>
                          <TD className="text-xs">{x.last_success_at ? timeAgo(x.last_success_at) : "—"}</TD>
                          <TD className="text-xs">{x.freshness.age_hours === null ? "no data" : `${x.freshness.age_hours} h`}{x.freshness.stale && <Badge tone="amber" className="ml-1">stale</Badge>}</TD>
                        </TR>
                      ))}
                    </tbody>
                  </Table>
                </div>
              )}
            </CardBody>
          </Card>
        ))}
        <Card>
          <CardHeader title="Recommendation decisions (pilot period)" description="Every V5 recommendation: viewed? decided how, by whom, how fast. Approval stays with people." />
          {dec.data && dec.data.length > 0 ? (
            <Table data-testid="decisions-table">
              <THead><tr><TH>Item</TH><TH>Generated</TH><TH>Viewed</TH><TH>Outcome</TH><TH>By</TH><TH>Time to decision</TH><TH>Reason</TH></tr></THead>
              <tbody>
                {dec.data.slice(0, 25).map((r) => (
                  <TR key={r.id}>
                    <TD><div className="font-medium">{r.item}</div><div className="font-mono text-xs text-muted-foreground">{r.sku}</div></TD>
                    <TD className="text-xs">{timeAgo(r.created_at)}</TD>
                    <TD className="text-xs">{r.viewed ? `${r.views}×` : "no"}</TD>
                    <TD><Badge tone={OUTCOME_TONE[r.outcome]}>{r.outcome}</Badge></TD>
                    <TD className="text-xs">{r.decided_by?.name ?? "—"}</TD>
                    <TD className="text-xs">{r.hours_to_decision === null ? "—" : `${r.hours_to_decision} h`}</TD>
                    <TD className="max-w-xs truncate text-xs">{r.reason ?? ""}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          ) : <CardBody className="text-sm text-muted-foreground">No recommendations generated in the pilot period yet.</CardBody>}
        </Card>
        {can(PERM.PILOTS_CONTRIBUTE) && (pilot.status === "active" || pilot.status === "paused") && (
          <Card>
            <CardHeader title="Give feedback" description="Was MedFlow useful in this pilot? Operational learning — short and specific." />
            <CardBody><FeedbackForm pilotId={pid} /></CardBody>
          </Card>
        )}
      </div>
    </>
  );
}
