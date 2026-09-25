"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Info, Plus } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import {
  CONNECTOR_LABEL,
  Counts,
  HealthBadge,
  INV,
  RunBadge,
  RunDialog,
  SourcePanel,
} from "@/components/integrations";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Pagination,
  Select,
  Skeleton,
  Stat,
  Table,
  TD,
  TH,
  THead,
  TR,
  Textarea,
} from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, patch, post } from "@/lib/api";
import type {
  EntityInfo,
  IntegrationOverview,
  IntegrationSource,
  Page,
  ReconciliationIssue,
  SyncRun,
} from "@/lib/types";
import { formatDate, formatNumber, timeAgo } from "@/lib/utils";

const useEntities = () =>
  useQuery({ queryKey: ["integrations", "entities"], queryFn: () => get<EntityInfo[]>("/integrations/entities"), staleTime: 3_600_000 });

function Honesty({ simulatorOn }: { simulatorOn: boolean }) {
  return (
    <div className="mb-6 flex gap-3 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm text-sky-900" data-testid="integration-notice">
      <Info className="mt-0.5 size-4 shrink-0" />
      <p>
        MedFlow supports a validated integration framework and controlled data-import/API connectors. Production
        hospital-system integrations require access to the corresponding hospital/vendor systems and have not been claimed
        unless actually tested. Sources marked <Badge tone="violet">simulated</Badge> talk to MedFlow&apos;s local reference ERP
        simulator, not to a real hospital system{simulatorOn ? "" : " (the simulator is switched off on this server)"}.
      </p>
    </div>
  );
}

/* ---------------- Monitoring ---------------- */

function Monitoring({ onRun }: { onRun: (id: number) => void }) {
  const q = useQuery({ queryKey: ["integrations", "overview"], queryFn: () => get<IntegrationOverview>("/integrations/overview"), refetchInterval: 60_000 });
  if (!q.data) return <Skeleton className="h-64" />;
  const t = q.data.totals;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Stat label="Connected systems" value={`${t.enabled} / ${t.sources}`} sub="enabled / configured" />
        <Stat label="Healthy" value={t.healthy} tone="green" sub={`${t.degraded} degraded · ${t.failing} failing · ${t.never_run} never run`} />
        <Stat label="Records processed (7 days)" value={formatNumber(t.received_7d)} sub={`${formatNumber(t.created_7d)} created · ${formatNumber(t.updated_7d)} updated`} />
        <Stat label="Records rejected (7 days)" value={formatNumber(t.rejected_7d)} tone={t.rejected_7d ? "amber" : "default"} />
        <Stat label="Open reconciliation issues" value={t.open_reconciliation} tone={t.open_reconciliation ? "amber" : "default"} />
      </div>
      <Card>
        <CardHeader title="Sync status by source" description="Health = worst latest run across the source's entities; freshness = time since the last successful sync." />
        {q.data.sources.length === 0 ? (
          <EmptyState title="No integration sources" description="An administrator can add one on the Sources tab." />
        ) : (
          <Table data-testid="monitoring-table">
            <THead><tr><TH>Source</TH><TH>Health</TH><TH>Last sync</TH><TH>Data freshness</TH><TH>7 days</TH><TH>Entities (latest run)</TH></tr></THead>
            <tbody>
              {q.data.sources.map((s) => (
                <TR key={s.id}>
                  <TD className="max-w-64">
                    <div className="font-medium">{s.name}</div>
                    <div className="mt-0.5 flex flex-wrap gap-1">
                      <Badge>{s.connector.replace("_", " ")}</Badge>
                      {s.is_simulated && <Badge tone="violet">simulated</Badge>}
                      {s.open_reconciliation > 0 && <Badge tone="amber">{s.open_reconciliation} to reconcile</Badge>}
                    </div>
                    {s.last_error && <div className="mt-1 line-clamp-2 text-xs text-red-700" title={s.last_error}>{s.last_error}</div>}
                  </TD>
                  <TD><HealthBadge health={s.health} /></TD>
                  <TD className="whitespace-nowrap text-xs">
                    <div>{s.last_run_at ? timeAgo(s.last_run_at) : "never"}</div>
                    <div className="text-muted-foreground">✓ {s.last_success_at ? timeAgo(s.last_success_at) : "—"} · ✗ {s.last_failure_at ? timeAgo(s.last_failure_at) : "—"}</div>
                  </TD>
                  <TD className="text-xs">
                    {s.freshness.age_hours == null ? "no data yet" : `${s.freshness.age_hours} h old`}
                    {s.freshness.stale && s.freshness.age_hours != null && <Badge tone="amber" className="ml-1">stale</Badge>}
                    <div className="text-muted-foreground">{s.schedule_minutes ? `every ${s.schedule_minutes} min` : "no schedule"}</div>
                  </TD>
                  <TD className="whitespace-nowrap text-xs tabular-nums">
                    {formatNumber(s.received_7d)} in · {formatNumber(s.created_7d + s.updated_7d)} applied ·{" "}
                    <span className={s.rejected_7d ? "text-red-700" : ""}>{formatNumber(s.rejected_7d)} rejected</span>
                    <div className="text-muted-foreground">{s.runs_7d} runs</div>
                  </TD>
                  <TD>
                    <div className="flex max-w-md flex-wrap gap-1">
                      {s.entity_status.length === 0 && <span className="text-xs text-muted-foreground">—</span>}
                      {s.entity_status.map((e) => (
                        <button key={e.entity} onClick={() => onRun(e.run_id)} title={`${e.status} · ${e.rejected} rejected`}>
                          <Badge tone={e.status === "SUCCESS" ? "green" : e.status === "PARTIAL" ? "amber" : "red"}>{e.entity}</Badge>
                        </button>
                      ))}
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

/* ---------------- Sources ---------------- */

function NewSourceDialog({ entities }: { entities: EntityInfo[] }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", system_type: "erp", connector: "upload", entities: [] as string[], config: "" });
  const create = useAction(() => {
    let config: Record<string, unknown> = {};
    if (f.config.trim()) config = JSON.parse(f.config) as Record<string, unknown>;
    return post<IntegrationSource>("/integrations/sources", { ...f, config });
  }, { success: "Source created", invalidate: [["integrations"]], onSuccess: () => setOpen(false) });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button size="sm"><Plus /> Add source</Button></DialogTrigger>
      <DialogContent title="Add an integration source" description="One hospital system that exchanges data with MedFlow. Secrets are never stored in the configuration." className="max-w-2xl">
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
          <Field label="Name"><Input required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="System type">
            <Select value={f.system_type} onChange={(e) => setF({ ...f, system_type: e.target.value })}>
              {["erp", "inventory", "procurement", "spreadsheet", "other"].map((x) => <option key={x}>{x}</option>)}
            </Select>
          </Field>
          <Field label="Connector" className="sm:col-span-2">
            <Select value={f.connector} onChange={(e) => setF({ ...f, connector: e.target.value })}>
              {Object.entries(CONNECTOR_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <p className="mb-1.5 text-sm font-medium">Entities this source may send</p>
            <div className="grid grid-cols-2 gap-1 text-sm">
              {entities.map((e) => (
                <label key={e.name} className="flex items-center gap-2">
                  <input type="checkbox" checked={f.entities.includes(e.name)}
                    onChange={(ev) => setF({ ...f, entities: ev.target.checked ? [...f.entities, e.name] : f.entities.filter((x) => x !== e.name) })} />
                  {e.label}
                </label>
              ))}
            </div>
          </div>
          {f.connector === "rest_pull" && (
            <Field label="Configuration (JSON)" className="sm:col-span-2"
              hint='e.g. {"base_url": "https://erp.example/api", "auth_env": "MEDFLOW_INTEGRATION_ERP_TOKEN", "schedule_minutes": 60} — the host must be in the operator allowlist; the token lives in that environment variable'>
              <Textarea className="min-h-24 font-mono text-xs" value={f.config} onChange={(e) => setF({ ...f, config: e.target.value })} />
            </Field>
          )}
          <DialogFooter><Button type="submit" disabled={create.isPending || !f.entities.length}>Create</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Sources({ onRun }: { onRun: (id: number) => void }) {
  const { can } = useMe();
  const canManage = can(PERM.INTEGRATIONS_MANAGE);
  const q = useQuery({ queryKey: ["integrations", "sources"], queryFn: () => get<IntegrationSource[]>("/integrations/sources") });
  const ents = useEntities();
  const [sel, setSel] = useState<number | null>(null);
  const toggle = useAction((s: IntegrationSource) => patch(`/integrations/sources/${s.id}`, { enabled: !s.enabled }), {
    success: "Source updated", invalidate: [["integrations"]],
  });
  if (!q.data || !ents.data) return <Skeleton className="h-64" />;
  const current = q.data.find((s) => s.id === sel) ?? q.data[0];
  return (
    <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
      <Card className="h-fit">
        <CardHeader title="Sources" action={canManage ? <NewSourceDialog entities={ents.data} /> : undefined} />
        <div className="divide-y" data-testid="source-list">
          {q.data.map((s) => (
            <button key={s.id} onClick={() => setSel(s.id)}
              className={`block w-full px-4 py-3 text-left text-sm hover:bg-muted ${current?.id === s.id ? "bg-teal-50" : ""}`}>
              <div className="font-medium">{s.name}</div>
              <div className="mt-0.5 flex flex-wrap gap-1">
                <Badge>{s.connector.replace("_", " ")}</Badge>
                {s.is_simulated && <Badge tone="violet">simulated</Badge>}
                {!s.enabled && <Badge tone="red">disabled</Badge>}
              </div>
            </button>
          ))}
        </div>
      </Card>
      {current ? (
        <Card>
          <CardHeader
            title={<span data-testid="source-title">{current.name}</span>}
            description={`${CONNECTOR_LABEL[current.connector]} · ${current.system_type} · entities: ${current.entities.join(", ")}`}
            action={canManage ? (
              <Button size="sm" variant="outline" onClick={() => toggle.mutate(current)}>{current.enabled ? "Disable" : "Enable"}</Button>
            ) : undefined}
          />
          <CardBody>
            {current.connector === "rest_pull" && (
              <pre className="mb-4 overflow-x-auto rounded-md bg-muted p-3 text-[11px]">{JSON.stringify(current.config, null, 2)}</pre>
            )}
            <SourcePanel key={current.id} source={current} entities={ents.data} canManage={canManage} onRun={onRun} />
          </CardBody>
        </Card>
      ) : (
        <EmptyState title="No sources yet" />
      )}
    </div>
  );
}

/* ---------------- Runs ---------------- */

function Runs({ onRun }: { onRun: (id: number) => void }) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["integrations", "runs", page, status], queryFn: () => get<Page<SyncRun>>("/integrations/runs", { page, page_size: 25, status }) });
  return (
    <Card>
      <CardHeader title="Import & sync history" description="The audit trail of every data exchange: hospital, source, started / completed, records received / created / updated / rejected, status and error summary."
        action={
          <Select aria-label="Run status" className="h-8 w-36 text-xs" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All statuses</option>{["SUCCESS", "PARTIAL", "FAILED"].map((s) => <option key={s}>{s}</option>)}
          </Select>
        } />
      <Table data-testid="runs-table">
        <THead><tr><TH>#</TH><TH>Started</TH><TH>Source · entity</TH><TH>Mode</TH><TH>Status</TH><TH>Records</TH><TH>By</TH></tr></THead>
        <tbody>
          {q.data?.items.map((r) => (
            <TR key={r.id} className="cursor-pointer" onClick={() => onRun(r.id)}>
              <TD className="tabular-nums text-muted-foreground">{r.id}</TD>
              <TD className="whitespace-nowrap text-xs">{timeAgo(r.started_at)}</TD>
              <TD><div className="font-medium">{r.source_name}</div><div className="text-xs text-muted-foreground">{r.entity}{r.file_name ? ` · ${r.file_name}` : ""}</div></TD>
              <TD className="text-xs">{r.mode} · {r.trigger}</TD>
              <TD><RunBadge status={r.status} /></TD>
              <TD>{r.error_summary?.fatal ? <span className="text-xs text-red-700">{r.error_summary.fatal}</span> : <Counts r={r} />}</TD>
              <TD className="text-xs">{r.triggered_by?.full_name ?? (r.trigger === "api" ? "API key" : r.trigger === "schedule" ? "scheduler" : "—")}</TD>
            </TR>
          ))}
        </tbody>
      </Table>
      {q.data && q.data.total === 0 && <EmptyState title="No runs yet" description="Upload a file, sync a source or push data with an API key." />}
      {q.data && <Pagination page={page} pageSize={25} total={q.data.total} onPage={setPage} />}
    </Card>
  );
}

/* ---------------- Reconciliation ---------------- */

function ResolveDialog({ issue, canAdjust }: { issue: ReconciliationIssue; canAdjust: boolean }) {
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState(canAdjust ? "adjust" : "external_wrong");
  const [note, setNote] = useState("");
  const res = useAction(() => post(`/integrations/reconciliation/${issue.id}/resolve`, { action, note }), {
    success: "Reconciliation issue resolved", invalidate: INV, onSuccess: () => setOpen(false),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button size="sm" variant="outline">Resolve</Button></DialogTrigger>
      <DialogContent title={`Reconcile ${issue.item.name}`} description={`${issue.source_name} says ${issue.external_quantity} ${issue.item.unit}; MedFlow's ledger said ${issue.medflow_quantity} at that time (${issue.difference > 0 ? "+" : ""}${issue.difference}). Nothing changes until you decide.`}>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); res.mutate(undefined); }}>
          <Field label="Decision">
            <Select aria-label="Decision" value={action} onChange={(e) => setAction(e.target.value)}>
              {canAdjust && <option value="adjust">MedFlow is wrong — post a stock adjustment of {issue.difference > 0 ? "+" : ""}{issue.difference}</option>}
              <option value="external_wrong">{issue.source_name} is wrong — keep MedFlow&apos;s ledger</option>
              <option value="accept">Known difference (timing, units…) — no change</option>
            </Select>
          </Field>
          <Field label="Note (required)"><Input aria-label="Note" required minLength={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Physical recount confirmed" /></Field>
          <DialogFooter><Button type="submit" disabled={res.isPending}><CheckCircle2 /> Resolve</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Reconciliation() {
  const { can } = useMe();
  const [status, setStatus] = useState("OPEN");
  const q = useQuery({ queryKey: ["integrations", "reconciliation", status], queryFn: () => get<ReconciliationIssue[]>("/integrations/reconciliation", { status }) });
  return (
    <Card>
      <CardHeader title="Reconciliation" description="Stock counts from hospital systems compared with MedFlow's ledger at the time of the count. Differences are never corrected automatically."
        action={
          <Select aria-label="Reconciliation status" className="h-8 w-32 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="OPEN">Open</option><option value="RESOLVED">Resolved</option><option value="ALL">All</option>
          </Select>
        } />
      <Table data-testid="reconciliation-table">
        <THead><tr><TH>Item</TH><TH>Source · as of</TH><TH className="text-right">Hospital system</TH><TH className="text-right">MedFlow then</TH><TH className="text-right">Difference</TH><TH className="text-right">MedFlow now</TH><TH>Status</TH><TH /></tr></THead>
        <tbody>
          {q.data?.map((i) => (
            <TR key={i.id}>
              <TD><div className="font-medium">{i.item.name}</div><div className="font-mono text-xs text-muted-foreground">{i.item.sku}</div></TD>
              <TD className="text-xs">{i.source_name}<div className="text-muted-foreground">{formatDate(i.as_of)}</div></TD>
              <TD className="text-right tabular-nums">{formatNumber(i.external_quantity)}</TD>
              <TD className="text-right tabular-nums">{formatNumber(i.medflow_quantity)}</TD>
              <TD className={`text-right font-medium tabular-nums ${i.difference < 0 ? "text-red-700" : "text-amber-700"}`} data-testid="recon-diff">
                {i.difference > 0 ? "+" : ""}{formatNumber(i.difference)} {i.item.unit}
              </TD>
              <TD className="text-right tabular-nums">{formatNumber(i.medflow_now)}</TD>
              <TD className="text-xs">
                {i.status === "OPEN" ? <Badge tone="amber">open</Badge> : <Badge tone="green">{i.resolution?.replaceAll("_", " ")}</Badge>}
                {i.note && <div className="mt-1 max-w-56 text-muted-foreground">{i.note}{i.resolved_by ? ` — ${i.resolved_by.full_name}` : ""}</div>}
              </TD>
              <TD>{i.status === "OPEN" && <ResolveDialog issue={i} canAdjust={can(PERM.STOCK_RECEIVE)} />}</TD>
            </TR>
          ))}
        </tbody>
      </Table>
      {q.data?.length === 0 && <EmptyState title={status === "OPEN" ? "Nothing to reconcile" : "No issues"} description="Stock counts received from hospital systems match MedFlow's ledger." />}
    </Card>
  );
}

/* ---------------- page ---------------- */

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = params.get("tab") ?? "monitoring";
  const [run, setRun] = useState<number | null>(null);
  const { can } = useMe();
  const ov = useQuery({ queryKey: ["integrations", "overview"], queryFn: () => get<IntegrationOverview>("/integrations/overview") });
  const setTab = (v: string) => router.replace(`${pathname}?tab=${v}`, { scroll: false });
  return (
    <>
      <PageHeader title="Integrations"
        description="How MedFlow receives operational data from the hospital's own systems — CSV / Excel files, API push, scheduled REST pulls — mapped, validated and applied through MedFlow's existing stock ledger and supplier-order log." />
      <Honesty simulatorOn={ov.data?.reference_erp_enabled ?? true} />
      <Tabs.Root value={tab} onValueChange={setTab}>
        <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b">
          {[["monitoring", "Monitoring"], ["sources", "Sources & mapping"], ["runs", "Runs & audit"], ["reconciliation", "Reconciliation"]].map(([k, label]) => (
            <Tabs.Trigger key={k} value={k}
              className="-mb-px whitespace-nowrap border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800">
              {label}
              {k === "reconciliation" && !!ov.data?.totals.open_reconciliation && <Badge tone="amber" className="ml-1.5">{ov.data.totals.open_reconciliation}</Badge>}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="monitoring"><Monitoring onRun={setRun} /></Tabs.Content>
        <Tabs.Content value="sources"><Sources onRun={setRun} /></Tabs.Content>
        <Tabs.Content value="runs"><Runs onRun={setRun} /></Tabs.Content>
        <Tabs.Content value="reconciliation"><Reconciliation /></Tabs.Content>
      </Tabs.Root>
      <RunDialog runId={run} onClose={() => setRun(null)} canRun={can(PERM.INTEGRATIONS_RUN)} />
    </>
  );
}

export default function IntegrationsPage() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
