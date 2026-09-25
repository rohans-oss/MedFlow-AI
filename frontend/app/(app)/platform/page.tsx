"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { AuditTable } from "@/components/tenancy";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, Field, Input, PageHeader, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { useMe } from "@/hooks/use-me";
import { get, patch, post } from "@/lib/api";
import type { Organization } from "@/lib/types";

/** V8 — platform administration: organizations and their administrators. Platform admins see no hospital's
 * operational data (that requires a hospital membership). */
export default function PlatformPage() {
  const { me } = useMe();
  const orgs = useQuery({ queryKey: ["organizations"], queryFn: () => get<Organization[]>("/organizations"), enabled: !!me?.is_platform_admin });
  const [f, setF] = useState({ name: "", code: "" });
  const [admin, setAdmin] = useState<Record<number, string>>({});
  const create = useAction(() => post("/organizations", f), { success: "Organization created", invalidate: [["organizations"]], onSuccess: () => setF({ name: "", code: "" }) });
  const status = useAction((v: { id: number; status: string }) => patch(`/organizations/${v.id}`, { status: v.status }), { success: "Status changed", invalidate: [["organizations"]] });
  const addAdmin = useAction((v: { id: number; email: string }) => post(`/organizations/${v.id}/admins`, { email: v.email }), {
    success: "Organization admin added", invalidate: [["organizations"]],
  });
  if (!me) return <Skeleton className="h-40" />;
  if (!me.is_platform_admin) return <EmptyState title="Platform administrators only" />;
  return (
    <div className="space-y-6">
      <PageHeader title={<span className="inline-flex items-center gap-2"><ShieldCheck className="size-5 text-teal-700" /> Platform</span>}
        description="Organizations (hospital groups) and their administrators. No hospital data is visible here." />
      <Card>
        <CardHeader title="Organizations" />
        <Table data-testid="platform-orgs">
          <THead><tr><TH>Organization</TH><TH>Status</TH><TH className="text-right">Hospitals</TH><TH className="text-right">Members</TH><TH className="text-right">Admins</TH><TH>Add organization admin</TH><TH /></tr></THead>
          <tbody>
            {orgs.data?.map((o) => (
              <TR key={o.id}>
                <TD><div className="font-medium">{o.name}</div><div className="text-xs text-muted-foreground">{o.code}{o.is_demo ? " · demo" : ""}</div></TD>
                <TD>{o.status === "ACTIVE" ? <Badge tone="green">Active</Badge> : <Badge tone="red">Suspended</Badge>}</TD>
                <TD className="text-right">{o.hospital_count}</TD>
                <TD className="text-right">{o.member_count}</TD>
                <TD className="text-right">{o.admin_count}</TD>
                <TD>
                  <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); addAdmin.mutate({ id: o.id, email: admin[o.id] ?? "" }); }}>
                    <Input type="email" placeholder="existing account email" className="h-8" value={admin[o.id] ?? ""} onChange={(e) => setAdmin({ ...admin, [o.id]: e.target.value })} />
                    <Button size="sm" variant="outline" type="submit">Add</Button>
                  </form>
                </TD>
                <TD className="text-right">
                  <Button size="sm" variant="ghost" onClick={() => status.mutate({ id: o.id, status: o.status === "ACTIVE" ? "SUSPENDED" : "ACTIVE" })}>
                    {o.status === "ACTIVE" ? "Suspend" : "Activate"}
                  </Button>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
        <CardBody>
          <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
            <Field label="Name"><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label="Code"><Input required value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
            <Button type="submit" disabled={create.isPending}><Plus /> Create organization</Button>
          </form>
          <p className="mt-2 text-xs text-muted-foreground">Organization admins then onboard hospitals from their Organization page. No billing or subscriptions exist in MedFlow.</p>
        </CardBody>
      </Card>
      <div>
        <h2 className="mb-3 text-sm font-semibold">Platform audit (organization- and platform-level events)</h2>
        <AuditTable url="/platform/audit-logs" queryKey={["platform", "audit"]} />
      </div>
    </div>
  );
}
