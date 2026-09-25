"use client";

import { useQuery } from "@tanstack/react-query";
import { KeyRound, Loader2, Play, PlugZap, RotateCcw, Save, Upload } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { Badge, Button, Field, Input, Select, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { ApiError, del, get, post, put, upload } from "@/lib/api";
import type {
  EntityInfo,
  FieldMapping,
  IntegrationCredential,
  IntegrationSource,
  RunStatus,
  SourceHealth,
  SyncRun,
  SyncRunDetail,
} from "@/lib/types";
import { formatDateTime, timeAgo } from "@/lib/utils";

export const INV = [["integrations"], ["inventory"], ["movements"], ["dashboard"], ["alerts"], ["suppliers"]];

export const CONNECTOR_LABEL: Record<string, string> = {
  upload: "File upload (CSV / Excel)",
  api_push: "API push (hospital system → MedFlow)",
  rest_pull: "REST pull (MedFlow → hospital system)",
};

export function HealthBadge({ health }: { health: SourceHealth }) {
  const tone = { healthy: "green", degraded: "amber", failing: "red", never_run: "neutral", disabled: "neutral" } as const;
  const label = { healthy: "Healthy", degraded: "Degraded", failing: "Failing", never_run: "Never run", disabled: "Disabled" };
  return <Badge tone={tone[health]} data-testid="source-health">{label[health]}</Badge>;
}

export function RunBadge({ status }: { status: RunStatus }) {
  const tone = { SUCCESS: "green", PARTIAL: "amber", FAILED: "red", RUNNING: "blue" } as const;
  return <Badge tone={tone[status]}>{status.toLowerCase()}</Badge>;
}

export function Counts({ r }: { r: SyncRun }) {
  return (
    <span className="whitespace-nowrap text-xs tabular-nums">
      {r.records_received} in · <span className="text-emerald-700">{r.records_created} new</span> · {r.records_updated} upd ·{" "}
      {r.records_unchanged} same · <span className={r.records_rejected ? "font-medium text-red-700" : ""}>{r.records_rejected} rejected</span>
    </span>
  );
}

/** One run with its rejected records (row, external id, reasons, raw values) and a retry button. */
export function RunDetail({ run, canRun, onRetried }: { run: SyncRunDetail; canRun: boolean; onRetried?: (r: SyncRunDetail) => void }) {
  const retry = useAction(() => post<SyncRunDetail>(`/integrations/runs/${run.id}/retry`), {
    success: (r) => `Retry: ${r.records_created + r.records_updated} applied, ${r.records_rejected} still rejected`,
    invalidate: INV, onSuccess: onRetried,
  });
  const s = run.error_summary;
  const open = run.rejected.filter((x) => x.status === "REJECTED").length;
  return (
    <div className="space-y-3 text-sm" data-testid="run-detail">
      <div className="flex flex-wrap items-center gap-2">
        <RunBadge status={run.status} />
        <span className="font-medium">{run.source_name} · {run.entity}</span>
        <Badge>{run.mode}</Badge><Badge>{run.trigger}</Badge>
        {run.file_name && <span className="text-xs text-muted-foreground">{run.file_name}</span>}
      </div>
      <Counts r={run} />
      <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
        <span>Started {formatDateTime(run.started_at)} · completed {formatDateTime(run.completed_at)}</span>
        <span>By {run.triggered_by?.full_name ?? (run.trigger === "api" ? "API key" : run.trigger === "schedule" ? "scheduler" : "—")}</span>
        {run.checkpoint_after && <span>Checkpoint {run.checkpoint_before ?? "—"} → {run.checkpoint_after}</span>}
        {run.file_sha256 && <span className="truncate">SHA-256 {run.file_sha256.slice(0, 16)}…</span>}
      </div>
      {s?.fatal && <p className="rounded-md bg-red-50 p-2 text-red-800" data-testid="run-fatal">{s.fatal}</p>}
      {s?.reasons && Object.keys(s.reasons).length > 0 && (
        <div className="flex flex-wrap gap-1">{Object.entries(s.reasons).map(([k, v]) => <Badge key={k} tone="red">{k.replaceAll("_", " ")} ×{v}</Badge>)}</div>
      )}
      {s?.info && Object.keys(s.info).length > 0 && (
        <div className="flex flex-wrap gap-1">{Object.entries(s.info).map(([k, v]) => <Badge key={k} tone="blue">{k.replaceAll("_", " ")} ×{v}</Badge>)}</div>
      )}
      {run.rejected.length > 0 && (
        <div className="rounded-lg border">
          <Table data-testid="rejected-records">
            <THead><tr><TH>Row</TH><TH>Record</TH><TH>Why it was rejected</TH><TH>Status</TH></tr></THead>
            <tbody>
              {run.rejected.map((x) => (
                <TR key={x.id}>
                  <TD className="tabular-nums">{x.row_number}</TD>
                  <TD className="max-w-xs">
                    <div className="font-mono text-xs">{x.external_id ?? "—"}</div>
                    <div className="truncate font-mono text-[11px] text-muted-foreground" title={JSON.stringify(x.raw)}>{JSON.stringify(x.raw)}</div>
                  </TD>
                  <TD className="text-xs">{x.errors.map((e, i) => <div key={i}><Badge tone="red" className="mr-1">{e.code.replaceAll("_", " ")}</Badge>{e.message}</div>)}</TD>
                  <TD>{x.status === "RETRIED" ? <Badge tone="blue">retried in #{x.retried_in_run_id}</Badge> : <Badge tone="amber">rejected</Badge>}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      {canRun && open > 0 && run.mode !== "dry_run" && (
        <Button size="sm" variant="outline" onClick={() => retry.mutate(undefined)} disabled={retry.isPending}>
          {retry.isPending ? <Loader2 className="animate-spin" /> : <RotateCcw />} Retry {open} rejected record{open === 1 ? "" : "s"}
        </Button>
      )}
    </div>
  );
}

export function RunDialog({ runId, onClose, canRun }: { runId: number | null; onClose: () => void; canRun: boolean }) {
  const q = useQuery({ queryKey: ["integrations", "run", runId], queryFn: () => get<SyncRunDetail>(`/integrations/runs/${runId}`), enabled: runId != null });
  const [shown, setShown] = useState<number | null>(null);
  const id = shown ?? runId;
  const q2 = useQuery({ queryKey: ["integrations", "run", id], queryFn: () => get<SyncRunDetail>(`/integrations/runs/${id}`), enabled: id != null && id !== runId });
  const run = id === runId ? q.data : q2.data;
  return (
    <Dialog open={runId != null} onOpenChange={(o) => { if (!o) { setShown(null); onClose(); } }}>
      <DialogContent title={`Sync run #${id ?? ""}`} description="Every import and sync is recorded: source, times, counts, status and each rejected record." className="max-w-4xl">
        {run ? <RunDetail run={run} canRun={canRun} onRetried={(r) => setShown(r.id)} /> : <Loader2 className="animate-spin" />}
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- one source: actions, upload, mapping, keys ---------------- */

export function SourcePanel({ source, entities, canManage, onRun }: {
  source: IntegrationSource; entities: EntityInfo[]; canManage: boolean; onRun: (runId: number) => void;
}) {
  const [entity, setEntity] = useState(source.entities[0] ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState(false);
  const [last, setLast] = useState<SyncRunDetail | null>(null);
  const [pulled, setPulled] = useState<SyncRun[] | null>(null);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; message: string } | null>(null);
  const test = useAction(() => post<{ ok: boolean; message: string; latency_ms: number | null }>(`/integrations/sources/${source.id}/test`), {
    onSuccess: (r) => setTestMsg(r),
  });
  const sync = useAction((v: { full: boolean; dry_run: boolean }) => post<SyncRun[]>(`/integrations/sources/${source.id}/sync`, v), {
    success: (rs) => `${rs.length} entit${rs.length === 1 ? "y" : "ies"} synced — ${rs.filter((r) => r.status === "FAILED").length} failed`,
    invalidate: INV, onSuccess: (rs) => setPulled(rs),
  });
  const up = useAction(() => {
    const f = new FormData();
    f.set("entity", entity);
    f.set("dry_run", String(dryRun));
    f.set("file", file as File);
    return upload<SyncRunDetail>(`/integrations/sources/${source.id}/upload`, f);
  }, { success: (r) => `${r.mode === "dry_run" ? "Dry run" : "Import"}: ${r.status.toLowerCase()}`, invalidate: INV, onSuccess: (r) => setLast(r) });

  return (
    <div className="space-y-5" data-testid={`source-${source.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => test.mutate(undefined)} disabled={test.isPending}>
          {test.isPending ? <Loader2 className="animate-spin" /> : <PlugZap />} Test connection
        </Button>
        {source.connector === "rest_pull" && (
          <>
            <Button size="sm" onClick={() => sync.mutate({ full: false, dry_run: false })} disabled={sync.isPending || !source.enabled} data-testid="sync-now">
              {sync.isPending ? <Loader2 className="animate-spin" /> : <Play />} Sync now
            </Button>
            <Button size="sm" variant="outline" onClick={() => sync.mutate({ full: true, dry_run: true })} disabled={sync.isPending || !source.enabled}>
              Dry run (full)
            </Button>
            <Button size="sm" variant="outline" onClick={() => sync.mutate({ full: true, dry_run: false })} disabled={sync.isPending || !source.enabled}>
              Full resync
            </Button>
          </>
        )}
        {testMsg && <span className={`text-xs ${testMsg.ok ? "text-emerald-700" : "text-red-700"}`} data-testid="test-result">{testMsg.message}</span>}
      </div>
      {pulled && (
        <div className="rounded-lg border" data-testid="sync-result">
          <Table>
            <THead><tr><TH>Entity</TH><TH>Status</TH><TH>Records</TH><TH /></tr></THead>
            <tbody>
              {pulled.map((r) => (
                <TR key={r.id}>
                  <TD>{r.entity}</TD><TD><RunBadge status={r.status} /></TD>
                  <TD>{r.error_summary?.fatal ? <span className="text-xs text-red-700">{r.error_summary.fatal}</span> : <Counts r={r} />}</TD>
                  <TD><Button size="sm" variant="ghost" onClick={() => onRun(r.id)}>Details</Button></TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </div>
      )}

      <div className="rounded-lg border p-4">
        <p className="mb-3 text-sm font-medium">Upload a file (CSV or Excel .xlsx)</p>
        <form className="grid items-end gap-3 sm:grid-cols-[180px_1fr_auto_auto]" onSubmit={(e) => { e.preventDefault(); if (file) up.mutate(undefined); }}>
          <Field label="Entity">
            <Select aria-label="Upload entity" value={entity} onChange={(e) => setEntity(e.target.value)}>
              {source.entities.map((e) => <option key={e} value={e}>{entities.find((x) => x.name === e)?.label ?? e}</option>)}
            </Select>
          </Field>
          <Field label="File"><Input aria-label="Upload file" type="file" accept=".csv,.xlsx,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
          <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Dry run</label>
          <Button type="submit" disabled={!file || up.isPending || !source.enabled} data-testid="upload-submit">
            {up.isPending ? <Loader2 className="animate-spin" /> : <Upload />} Import
          </Button>
        </form>
        <p className="mt-2 text-xs text-muted-foreground">
          Row 1 = column names. Columns are matched through this source&apos;s field mapping below. A dry run validates and shows
          what would change without writing anything.
        </p>
        {last && <div className="mt-4 border-t pt-4" data-testid="upload-result"><RunDetail run={last} canRun onRetried={setLast} /></div>}
      </div>

      <MappingEditor source={source} entities={entities} canManage={canManage} />
      {source.connector === "api_push" && canManage && <KeysPanel source={source} />}
    </div>
  );
}

function MappingEditor({ source, entities, canManage }: { source: IntegrationSource; entities: EntityInfo[]; canManage: boolean }) {
  const q = useQuery({ queryKey: ["integrations", "mappings", source.id], queryFn: () => get<FieldMapping[]>(`/integrations/sources/${source.id}/mappings`) });
  const [entity, setEntity] = useState(source.entities[0] ?? "");
  const current = q.data?.find((m) => m.entity === entity);
  const info = entities.find((e) => e.name === entity);
  const [draft, setDraft] = useState<Record<string, Record<string, string>>>({});
  const [fmt, setFmt] = useState<Record<string, string>>({});
  const map = { ...(current?.field_map ?? {}), ...(draft[entity] ?? {}) };
  const save = useAction(() => put<FieldMapping>(`/integrations/sources/${source.id}/mappings/${entity}`, {
    field_map: map, defaults: current?.defaults ?? {}, date_format: (fmt[entity] ?? current?.date_format ?? "") || null,
  }), { success: "Mapping saved", invalidate: [["integrations", "mappings", String(source.id)], ["integrations"]],
    onSuccess: () => setDraft({ ...draft, [entity]: {} }) });
  const reset = useAction(() => del<FieldMapping>(`/integrations/sources/${source.id}/mappings/${entity}`), {
    success: "Mapping reset to MedFlow field names", invalidate: [["integrations"]], onSuccess: () => setDraft({ ...draft, [entity]: {} }),
  });
  return (
    <div className="rounded-lg border p-4" data-testid="mapping-editor">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">Field mapping <span className="font-normal text-muted-foreground">— source field → MedFlow field</span></p>
        <Select aria-label="Mapping entity" className="h-8 w-56 text-xs" value={entity} onChange={(e) => setEntity(e.target.value)}>
          {source.entities.map((e) => <option key={e} value={e}>{entities.find((x) => x.name === e)?.label ?? e}</option>)}
        </Select>
      </div>
      {info && <p className="mb-2 text-xs text-muted-foreground">{info.description}</p>}
      <Table>
        <THead><tr><TH>Source field</TH><TH>→ MedFlow field</TH><TH>Type</TH><TH>Meaning</TH></tr></THead>
        <tbody>
          {info?.fields.map((f) => (
            <TR key={f.name}>
              <TD>
                <Input aria-label={`Source field for ${f.name}`} className="h-8 font-mono text-xs" disabled={!canManage} value={map[f.name] ?? f.name}
                  onChange={(e) => setDraft({ ...draft, [entity]: { ...(draft[entity] ?? {}), [f.name]: e.target.value } })} />
              </TD>
              <TD className="font-mono text-xs">{f.name}{f.required && <span className="text-red-600"> *</span>}</TD>
              <TD className="text-xs text-muted-foreground">{f.kind}</TD>
              <TD className="text-xs text-muted-foreground">{f.description}</TD>
            </TR>
          ))}
        </tbody>
      </Table>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="Date format (optional)" hint="e.g. %d/%m/%Y — ISO 8601 is always accepted">
          <Input className="h-8 w-44 font-mono text-xs" disabled={!canManage} value={fmt[entity] ?? current?.date_format ?? ""} onChange={(e) => setFmt({ ...fmt, [entity]: e.target.value })} />
        </Field>
        {canManage && (
          <>
            <Button size="sm" onClick={() => save.mutate(undefined)} disabled={save.isPending}><Save /> Save mapping</Button>
            {current?.customized && <Button size="sm" variant="outline" onClick={() => reset.mutate(undefined)}>Reset</Button>}
          </>
        )}
        {current && <Badge tone={current.customized ? "blue" : "neutral"}>{current.customized ? "custom mapping" : "MedFlow field names"}</Badge>}
      </div>
    </div>
  );
}

function KeysPanel({ source }: { source: IntegrationSource }) {
  const q = useQuery({ queryKey: ["integrations", "keys", source.id], queryFn: () => get<IntegrationCredential[]>(`/integrations/sources/${source.id}/credentials`) });
  const [label, setLabel] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const create = useAction(() => post<IntegrationCredential & { api_key: string }>(`/integrations/sources/${source.id}/credentials`, { label: label || "API key" }), {
    invalidate: [["integrations"]], onSuccess: (r) => { setShown(r.api_key); setLabel(""); },
  });
  const revoke = useAction((id: number) => post(`/integrations/credentials/${id}/revoke`), { success: "Key revoked", invalidate: [["integrations"]] });
  return (
    <div className="rounded-lg border p-4" data-testid="keys-panel">
      <p className="mb-1 text-sm font-medium">API keys</p>
      <p className="mb-3 text-xs text-muted-foreground">
        The hospital system sends <span className="font-mono">POST /api/ingest/v1/&lt;entity&gt;</span> with{" "}
        <span className="font-mono">Authorization: Bearer &lt;key&gt;</span> and a body <span className="font-mono">{"{\"records\": [...]}"}</span>.
        Only a hash of the key is stored — it is shown once.
      </p>
      <Table>
        <THead><tr><TH>Label</TH><TH>Key</TH><TH>Created</TH><TH>Last used</TH><TH /></tr></THead>
        <tbody>
          {q.data?.map((k) => (
            <TR key={k.id}>
              <TD>{k.label}</TD><TD className="font-mono text-xs">mfk_{k.key_prefix}_…</TD>
              <TD className="text-xs">{formatDateTime(k.created_at)}</TD>
              <TD className="text-xs">{k.last_used_at ? timeAgo(k.last_used_at) : "never"}</TD>
              <TD>{k.revoked_at ? <Badge>revoked</Badge> : <Button size="sm" variant="outline" onClick={() => revoke.mutate(k.id)}>Revoke</Button>}</TD>
            </TR>
          ))}
        </tbody>
      </Table>
      <form className="mt-3 flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
        <Field label="New key label"><Input className="h-8" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. SAP PI production" /></Field>
        <Button size="sm" type="submit" disabled={create.isPending}><KeyRound /> Create key</Button>
      </form>
      <Dialog open={shown != null} onOpenChange={(o) => !o && setShown(null)}>
        <DialogContent title="Copy the API key now" description="It will not be shown again. Store it in the hospital system's secret store — never in source code or GitHub.">
          <code className="block break-all rounded-md bg-muted p-3 text-xs" data-testid="new-api-key">{shown}</code>
          <DialogFooter>
            <Button variant="outline" onClick={() => { void navigator.clipboard?.writeText(shown ?? "").then(() => toast.success("Copied")); }}>Copy</Button>
            <Button onClick={() => setShown(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function errText(e: unknown) {
  return e instanceof ApiError ? e.message : String(e);
}
