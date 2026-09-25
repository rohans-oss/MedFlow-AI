"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { Building2, Plus } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { AuditTable, MembersDialog } from "@/components/tenancy";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import {
  Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Select, Skeleton, Stat, Table, TD, TH, THead, TR,
} from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { useMe } from "@/hooks/use-me";
import { get, patch, post } from "@/lib/api";
import type { HospitalAdmin, OnboardResult, OrgOverview } from "@/lib/types";
import { formatINR, ROLE_LABELS } from "@/lib/utils";

const inr = (v: number) => formatINR(v);
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Overview({ orgId }: { orgId: number }) {
  const q = useQuery({ queryKey: ["organization", orgId, "overview"], queryFn: () => get<OrgOverview>(`/organizations/${orgId}/overview`) });
  if (q.isLoading) return <Skeleton className="h-64" />;
  const d = q.data;
  if (!d) return <EmptyState title="Could not load the overview" />;
  const t = d.totals;
  return (
    <div className="space-y-6" data-testid="org-overview">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Hospitals" value={t.hospitals ?? 0} sub={`${t.hospitals_with_high_risk ?? 0} with HIGH-risk items`} />
        <Stat label="Items at HIGH stockout risk" value={t.risk_high ?? 0} tone={t.risk_high ? "red" : "default"} sub={`${t.risk_medium ?? 0} MEDIUM`} />
        <Stat label="Active alerts" value={t.active_alerts ?? 0} sub={`${t.critical_alerts ?? 0} critical`} tone={t.critical_alerts ? "amber" : "default"} />
        <Stat label="Pending procurement" value={inr(t.pending_purchase_value ?? 0)} sub={`${t.pending_recommendations ?? 0} recommendations awaiting approval`} />
      </div>
      <Card>
        <CardHeader title="By hospital" description={`Contributing hospitals: ${d.contributing_hospitals.join(", ")} — totals above are sums of these rows.`} />
        <Table data-testid="org-hospitals-table">
          <THead><tr><TH>Hospital</TH><TH>Status</TH><TH className="text-right">Members</TH><TH className="text-right">Items</TH>
            <TH className="text-right">HIGH / MED risk</TH><TH>Risk as of</TH><TH className="text-right">Active alerts</TH>
            <TH className="text-right">Pending recs</TH><TH className="text-right">Pending value</TH><TH className="text-right">Supplier OTIF</TH></tr></THead>
          <tbody>
            {d.hospitals.map((h) => (
              <TR key={h.hospital_id}>
                <TD><div className="font-medium">{h.hospital}</div><div className="text-xs text-muted-foreground">{h.code}{h.city ? ` · ${h.city}` : ""}</div></TD>
                <TD>{h.status === "ACTIVE" ? <Badge tone="green">Active</Badge> : <Badge tone="red">Suspended</Badge>}</TD>
                <TD className="text-right">{h.active_members}</TD>
                <TD className="text-right">{h.items}</TD>
                <TD className="text-right"><span className="font-medium text-red-600">{h.risk_high}</span> / <span className="text-amber-600">{h.risk_medium}</span></TD>
                <TD className="text-muted-foreground">{h.risk_as_of ?? "no risk run"}</TD>
                <TD className="text-right">{h.active_alerts}</TD>
                <TD className="text-right">{h.pending_recommendations}</TD>
                <TD className="text-right">{inr(h.pending_purchase_value)}</TD>
                <TD className="text-right">{pct(h.supplier_otif_rate)} <span className="text-xs text-muted-foreground">({h.supplier_orders_decided})</span></TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>
      {d.supplier_comparison.length > 0 && (
        <Card>
          <CardHeader title="Same supplier, different hospitals" description="Each hospital's own OTIF on its own orders — shown side by side, never pooled." />
          <Table data-testid="supplier-comparison">
            <THead><tr><TH>Supplier</TH>{d.hospitals.map((h) => <TH key={h.hospital_id} className="text-right">{h.hospital}</TH>)}</tr></THead>
            <tbody>
              {d.supplier_comparison.map((s) => (
                <TR key={s.code}>
                  <TD><div className="font-medium">{s.code}</div><div className="text-xs text-muted-foreground">{s.names.join(" / ")}</div></TD>
                  {d.hospitals.map((h) => {
                    const p = s.by_hospital.find((x) => x.hospital_id === h.hospital_id);
                    return <TD key={h.hospital_id} className="text-right">{p ? <>{pct(p.otif_rate)} <span className="text-xs text-muted-foreground">{p.grade ?? ""} · {p.orders} orders</span></> : "—"}</TD>;
                  })}
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </div>
  );
}

function OnboardDialog({ orgId }: { orgId: number }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", code: "", city: "", state: "", bed_count: "", admin_email: "", admin_name: "", admin_password: "",
    departments: "ICU, Intensive Care Unit\nER, Emergency\nWARD, General Wards", service_level: "0.95" });
  const [result, setResult] = useState<OnboardResult | null>(null);
  const action = useAction(() => post<OnboardResult>(`/organizations/${orgId}/hospitals`, {
    name: f.name, code: f.code, city: f.city || null, state: f.state || null, bed_count: f.bed_count ? Number(f.bed_count) : null,
    admin: { email: f.admin_email, full_name: f.admin_name, password: f.admin_password },
    departments: f.departments.split("\n").map((l) => l.split(",").map((x) => x.trim())).filter((p) => p[0] && p[1]).map(([code, name]) => ({ code, name })),
    procurement: { service_level: Number(f.service_level) },
  }), { success: (r) => `${r.hospital.name} is ready`, invalidate: [["organization"], ["hospitals"]], onSuccess: (r) => setResult(r) });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setResult(null); }}>
      <DialogTrigger asChild><Button size="sm" data-testid="onboard-hospital"><Plus /> Onboard hospital</Button></DialogTrigger>
      <DialogContent title="Onboard a hospital" description="Hospital → administrator → departments → procurement settings. Inventory is imported afterwards (CSV) by the hospital." className="max-w-2xl">
        {result ? (
          <div className="space-y-3 text-sm" data-testid="onboard-result">
            <p><b>{result.hospital.name}</b> created with {result.departments} department(s); administrator {result.admin_created ? "account created" : "existing account added"}; procurement settings: {result.procurement_settings}.</p>
            <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">{result.next_steps.map((s) => <li key={s}>{s}</li>)}</ol>
          </div>
        ) : (
          <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); action.mutate(undefined); }}>
            <Field label="Hospital name"><Input required value={f.name} onChange={set("name")} /></Field>
            <Field label="Code" hint="Unique, e.g. RIVERSIDE-PUNE"><Input required value={f.code} onChange={set("code")} /></Field>
            <Field label="City"><Input value={f.city} onChange={set("city")} /></Field>
            <Field label="State"><Input value={f.state} onChange={set("state")} /></Field>
            <Field label="Beds"><Input type="number" min={0} value={f.bed_count} onChange={set("bed_count")} /></Field>
            <Field label="Service level (procurement)"><Input type="number" step="0.01" min={0.5} max={0.999} value={f.service_level} onChange={set("service_level")} /></Field>
            <Field label="Administrator email"><Input type="email" required value={f.admin_email} onChange={set("admin_email")} /></Field>
            <Field label="Administrator name"><Input required value={f.admin_name} onChange={set("admin_name")} /></Field>
            <Field label="Administrator initial password" hint="Letters and digits, 8+ characters"><Input type="password" required minLength={8} value={f.admin_password} onChange={set("admin_password")} /></Field>
            <Field label="Departments" hint="One per line: CODE, Name" className="sm:col-span-2">
              <textarea className="min-h-24 w-full rounded-md border px-3 py-2 text-sm" value={f.departments} onChange={set("departments")} />
            </Field>
            <DialogFooter><Button type="submit" disabled={action.isPending}>Create hospital</Button></DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Hospitals({ orgId }: { orgId: number }) {
  const q = useQuery({ queryKey: ["organization", orgId, "hospitals"], queryFn: () => get<HospitalAdmin[]>(`/organizations/${orgId}/hospitals`) });
  const status = useAction((v: { id: number; status: string }) => patch(`/hospitals/${v.id}`, { status: v.status }), {
    success: "Hospital status changed", invalidate: [["organization"]],
  });
  return (
    <Card>
      <CardHeader title="Hospitals" description="Operational data needs a membership in the hospital — administering it does not grant one." action={<OnboardDialog orgId={orgId} />} />
      <Table data-testid="org-hospitals">
        <THead><tr><TH>Hospital</TH><TH>Status</TH><TH className="text-right">Members</TH><TH>My role there</TH><TH /></tr></THead>
        <tbody>
          {q.data?.map((h) => (
            <TR key={h.id}>
              <TD><div className="font-medium">{h.name}</div><div className="text-xs text-muted-foreground">{h.code}{h.city ? ` · ${h.city}` : ""}</div></TD>
              <TD>{h.status === "ACTIVE" ? <Badge tone="green">Active</Badge> : <Badge tone="red">Suspended</Badge>}</TD>
              <TD className="text-right">{h.member_count}</TD>
              <TD>{h.my_role ? ROLE_LABELS[h.my_role] : <span className="text-muted-foreground">not a member</span>}</TD>
              <TD className="space-x-2 text-right">
                <MembersDialog hospitalId={h.id} name={h.name} />
                <Button size="sm" variant="ghost" onClick={() => status.mutate({ id: h.id, status: h.status === "ACTIVE" ? "SUSPENDED" : "ACTIVE" })}>
                  {h.status === "ACTIVE" ? "Suspend" : "Activate"}
                </Button>
              </TD>
            </TR>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

function OrganizationPage() {
  const { me } = useMe();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const orgs = me?.admin_organizations ?? [];
  const orgId = Number(params.get("org")) || orgs[0]?.id;
  const tab = params.get("tab") ?? "overview";
  const go = (o: Record<string, string>) => router.replace(`${pathname}?${new URLSearchParams({ org: String(orgId), tab, ...o })}`);
  if (!me) return <Skeleton className="h-40" />;
  if (!orgs.length) return <EmptyState title="You do not administer an organization" />;
  const org = orgs.find((o) => o.id === orgId) ?? orgs[0];
  return (
    <div>
      <PageHeader
        title={<span className="inline-flex items-center gap-2"><Building2 className="size-5 text-teal-700" /> {org.name}</span>}
        description="Organization view — aggregates over this organization's hospitals, each computed from its own data. Hospital views show one hospital only."
        actions={orgs.length > 1 && (
          <Select aria-label="Organization" value={org.id} onChange={(e) => go({ org: e.target.value })}>
            {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select>
        )}
      />
      <Tabs.Root value={tab} onValueChange={(v) => go({ tab: v })}>
        <Tabs.List className="mb-6 flex gap-1 border-b">
          {[["overview", "Overview"], ["hospitals", "Hospitals & members"], ["audit", "Audit"]].map(([k, l]) => (
            <Tabs.Trigger key={k} value={k} className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800">{l}</Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="overview"><Overview orgId={org.id} /></Tabs.Content>
        <Tabs.Content value="hospitals"><Hospitals orgId={org.id} /></Tabs.Content>
        <Tabs.Content value="audit"><AuditTable url={`/organizations/${org.id}/audit-logs`} queryKey={["organization", org.id, "audit"]} /></Tabs.Content>
      </Tabs.Root>
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Skeleton className="h-40" />}><OrganizationPage /></Suspense>;
}
