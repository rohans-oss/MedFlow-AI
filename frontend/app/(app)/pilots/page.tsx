"use client";

import { useQuery } from "@tanstack/react-query";
import { FlaskConical, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { STATUS_TONE } from "@/components/pilots";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Skeleton, Table, TD, TH, THead, TR, Textarea } from "@/components/ui/primitives";
import { useAction, useDepartments } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, post } from "@/lib/api";
import type { IntegrationSource, Pilot, User } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const iso = (d: Date) => d.toISOString().slice(0, 10);
const shift = (days: number) => iso(new Date(Date.now() + days * 86_400_000));

function NewPilotDialog() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const depts = useDepartments();
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<User[]>("/users"), enabled: open });
  const sources = useQuery({ queryKey: ["integrations", "sources"], queryFn: () => get<IntegrationSource[]>("/integrations/sources"), enabled: open });
  const [f, setF] = useState({
    name: "", description: "", baseline_start: shift(-60), baseline_end: shift(-31), pilot_start: shift(-30), pilot_end: shift(29),
    department_ids: [] as number[], source_ids: [] as number[], user_ids: [] as number[],
  });
  const create = useAction(() => post<Pilot>("/pilots", { ...f, description: f.description || null }), {
    success: "Pilot created", invalidate: [["pilots"]], onSuccess: (p) => { setOpen(false); router.push(`/pilots/${p.id}`); },
  });
  const toggle = (k: "department_ids" | "source_ids" | "user_ids", id: number) =>
    setF({ ...f, [k]: f[k].includes(id) ? f[k].filter((x) => x !== id) : [...f[k], id] });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button><Plus /> New pilot</Button></DialogTrigger>
      <DialogContent title="Create a pilot" description="One hospital (this one). The baseline period must end before the pilot starts — the two are never mixed." className="max-w-2xl">
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
          <Field label="Name" className="sm:col-span-2"><Input aria-label="Pilot name" required minLength={3} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Description" className="sm:col-span-2"><Textarea className="min-h-16" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
          <Field label="Baseline start"><Input aria-label="Baseline start" type="date" value={f.baseline_start} onChange={(e) => setF({ ...f, baseline_start: e.target.value })} /></Field>
          <Field label="Baseline end"><Input aria-label="Baseline end" type="date" value={f.baseline_end} onChange={(e) => setF({ ...f, baseline_end: e.target.value })} /></Field>
          <Field label="Pilot start"><Input aria-label="Pilot start" type="date" value={f.pilot_start} onChange={(e) => setF({ ...f, pilot_start: e.target.value })} /></Field>
          <Field label="Planned end"><Input aria-label="Pilot end" type="date" value={f.pilot_end} onChange={(e) => setF({ ...f, pilot_end: e.target.value })} /></Field>
          <div className="sm:col-span-2">
            <p className="mb-1.5 text-sm font-medium">Participating departments</p>
            <div className="grid grid-cols-2 gap-1 text-sm">
              {depts.data?.map((d) => (
                <label key={d.id} className="flex items-center gap-2"><input type="checkbox" checked={f.department_ids.includes(d.id)} onChange={() => toggle("department_ids", d.id)} />{d.name}</label>
              ))}
            </div>
          </div>
          <div className="sm:col-span-2">
            <p className="mb-1.5 text-sm font-medium">Participating users</p>
            <div className="grid grid-cols-2 gap-1 text-sm">
              {users.data?.filter((u) => u.is_active).map((u) => (
                <label key={u.id} className="flex items-center gap-2"><input type="checkbox" checked={f.user_ids.includes(u.id)} onChange={() => toggle("user_ids", u.id)} />{u.full_name}</label>
              ))}
            </div>
          </div>
          <div className="sm:col-span-2">
            <p className="mb-1.5 text-sm font-medium">V9 data sources</p>
            <div className="grid grid-cols-1 gap-1 text-sm">
              {sources.data?.map((s) => (
                <label key={s.id} className="flex items-center gap-2"><input type="checkbox" checked={f.source_ids.includes(s.id)} onChange={() => toggle("source_ids", s.id)} />{s.name}{s.is_simulated && <Badge tone="violet">simulated</Badge>}</label>
              ))}
              {sources.data?.length === 0 && <span className="text-xs text-muted-foreground">No integration sources configured.</span>}
            </div>
          </div>
          <DialogFooter><Button type="submit" disabled={create.isPending} data-testid="create-pilot">Create pilot</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function PilotsPage() {
  const router = useRouter();
  const { me, can } = useMe();
  const q = useQuery({ queryKey: ["pilots"], queryFn: () => get<Pilot[]>("/pilots") });
  const sandbox = useAction(() => post<Pilot>("/pilots/sandbox"), {
    success: "Sandbox pilot created (synthetic data)", invalidate: [["pilots"]], onSuccess: (p) => router.push(`/pilots/${p.id}`),
  });
  const manage = can(PERM.PILOTS_MANAGE);
  return (
    <>
      <PageHeader title="Pilots"
        description="Run MedFlow in this hospital for a defined period and measure it against a baseline period: data quality, inventory, stockouts, forecasts, warnings, suppliers, procurement decisions, adoption, issues. Results are descriptive."
        actions={manage ? (
          <>
            {me?.hospital?.is_demo && (
              <Button variant="outline" onClick={() => sandbox.mutate(undefined)} disabled={sandbox.isPending} data-testid="create-sandbox">
                <FlaskConical /> Pilot sandbox (synthetic)
              </Button>
            )}
            <NewPilotDialog />
          </>
        ) : undefined} />
      <p className="mb-4 text-sm text-muted-foreground">
        No real hospital pilot has been run with MedFlow yet. Pilots in demo hospitals are labelled <Badge tone="violet">synthetic data</Badge> and never produce an outcome claim.
      </p>
      <Card>
        <CardHeader title="Pilots in this hospital" />
        {!q.data ? <Skeleton className="m-4 h-24" /> : q.data.length === 0 ? (
          <EmptyState title="No pilots yet" description={manage ? "Create a pilot, or try the sandbox in a demo hospital." : "An administrator can create one."} />
        ) : (
          <Table data-testid="pilots-table">
            <THead><tr><TH>Pilot</TH><TH>Status</TH><TH>Baseline</TH><TH>Pilot period</TH><TH>Data</TH><TH>Open issues</TH></tr></THead>
            <tbody>
              {q.data.map((p) => (
                <TR key={p.id}>
                  <TD><Link href={`/pilots/${p.id}`} className="font-medium text-teal-800 hover:underline">{p.name}</Link></TD>
                  <TD><Badge tone={STATUS_TONE[p.status]}>{p.status}</Badge></TD>
                  <TD className="text-xs">{formatDate(p.baseline_start)} → {formatDate(p.baseline_end)}</TD>
                  <TD className="text-xs">{formatDate(p.pilot_start)} → {formatDate(p.pilot_end)}</TD>
                  <TD>{p.data_classification === "synthetic" ? <Badge tone="violet">synthetic</Badge> : <Badge tone="blue">observed</Badge>}</TD>
                  <TD className="tabular-nums">{p.open_issues}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
