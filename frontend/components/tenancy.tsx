"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Users } from "lucide-react";
import { useState } from "react";

import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Badge, Button, Card, Field, Input, Select, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { get, patch, post } from "@/lib/api";
import type { AuditEntry, HospitalDetail, Member, Page, Role } from "@/lib/types";
import { formatDateTime, ROLE_LABELS, timeAgo } from "@/lib/utils";

const ROLES: Role[] = ["admin", "procurement_manager", "inventory_manager", "department_manager", "viewer"];

/** V8 — members of one hospital (hospital admins: their active hospital; organization admins: any hospital of their
 * organization). Uses /hospitals/{id}/members — role, department and status are per hospital. */
export function MembersPanel({ hospitalId }: { hospitalId: number }) {
  const detail = useQuery({ queryKey: ["hospital-detail", hospitalId], queryFn: () => get<HospitalDetail>(`/hospitals/${hospitalId}`) });
  const members = useQuery({ queryKey: ["members", hospitalId], queryFn: () => get<Member[]>(`/hospitals/${hospitalId}/members`) });
  const inv = [["members", String(hospitalId)], ["hospitals"], ["organization"]];
  const update = useAction((v: { user_id: number; body: Record<string, unknown> }) => patch(`/hospitals/${hospitalId}/members/${v.user_id}`, v.body), {
    success: "Member updated", invalidate: inv,
  });
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "viewer" as Role, department_id: "" });
  const add = useAction(() => post(`/hospitals/${hospitalId}/members`, {
    ...form, department_id: form.department_id ? Number(form.department_id) : null,
  }), { success: "Member added", invalidate: inv, onSuccess: () => setForm({ email: "", full_name: "", password: "", role: "viewer", department_id: "" }) });
  const depts = detail.data?.departments.filter((d) => d.is_active) ?? [];
  return (
    <div className="space-y-4" data-testid="members-panel">
      <Table>
        <THead><tr><TH>Member</TH><TH>Role here</TH><TH>Department</TH><TH>Status</TH><TH>Last sign-in</TH></tr></THead>
        <tbody>
          {members.data?.map((m) => (
            <TR key={m.membership_id}>
              <TD>
                <div className="font-medium">{m.full_name}</div>
                <div className="text-xs text-muted-foreground">{m.email}{m.other_hospitals > 0 && ` · also in ${m.other_hospitals} other hospital(s)`}</div>
              </TD>
              <TD>
                <Select aria-label={`Role of ${m.email}`} value={m.role} className="h-8 text-xs"
                  onChange={(e) => update.mutate({ user_id: m.user_id, body: { role: e.target.value } })}>
                  {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                </Select>
              </TD>
              <TD className="text-muted-foreground">{m.department ?? "—"}</TD>
              <TD>
                <button onClick={() => update.mutate({ user_id: m.user_id, body: { is_active: m.status !== "ACTIVE" } })} title="Toggle access to this hospital">
                  {m.status === "ACTIVE" ? <Badge tone="green">Active</Badge> : <Badge tone="red">Suspended</Badge>}
                </button>
              </TD>
              <TD className="text-muted-foreground">{m.last_login_at ? timeAgo(m.last_login_at) : "Never"}</TD>
            </TR>
          ))}
        </tbody>
      </Table>
      <form className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); add.mutate(undefined); }}>
        <p className="text-sm font-medium sm:col-span-2">Add a member</p>
        <Field label="Email"><Input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        <Field label="Full name"><Input required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
        <Field label="Initial password" hint="Ignored if the account already exists in this organization">
          <Input type="password" required minLength={8} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </Field>
        <Field label="Role">
          <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
          </Select>
        </Field>
        <Field label="Department" hint="Required for department managers">
          <Select value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
            <option value="">—</option>
            {depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </Select>
        </Field>
        <div className="flex items-end justify-end"><Button type="submit" disabled={add.isPending}><Plus /> Add member</Button></div>
      </form>
    </div>
  );
}

export function MembersDialog({ hospitalId, name }: { hospitalId: number; name: string }) {
  return (
    <Dialog>
      <DialogTrigger asChild><Button size="sm" variant="outline"><Users /> Members</Button></DialogTrigger>
      <DialogContent title={`Members — ${name}`} description="Access, role and department in this hospital only." className="max-w-3xl">
        <MembersPanel hospitalId={hospitalId} />
        <DialogFooter><span /></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function AuditTable({ url, queryKey }: { url: string; queryKey: unknown[] }) {
  const q = useQuery({ queryKey, queryFn: () => get<Page<AuditEntry>>(url, { page_size: 100 }) });
  return (
    <Card>
      <Table>
        <THead><tr><TH>When</TH><TH>Action</TH><TH>Entity</TH><TH>By</TH><TH>Details</TH></tr></THead>
        <tbody>
          {q.data?.items.map((a) => (
            <TR key={a.id}>
              <TD className="whitespace-nowrap text-muted-foreground">{formatDateTime(a.created_at)}</TD>
              <TD className="font-mono text-xs">{a.action}</TD>
              <TD className="text-xs">{a.entity_type}{a.entity_id ? ` #${a.entity_id}` : ""}</TD>
              <TD>{a.user?.full_name ?? "—"}</TD>
              <TD className="max-w-md truncate text-xs text-muted-foreground">{a.details ? JSON.stringify(a.details) : ""}</TD>
            </TR>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

