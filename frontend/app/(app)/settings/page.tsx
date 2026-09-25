"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Pencil, Plus } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { CategoryDialog, DepartmentDialog, UserDialog } from "@/components/forms/entity-dialogs";
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, PageHeader, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { asInt, useAction, useCategories, useDepartments } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, patch, post } from "@/lib/api";
import type { Hospital, ImportResult, User } from "@/lib/types";
import { ROLE_LABELS, timeAgo } from "@/lib/utils";

/* ---------------- Account ---------------- */

const pwSchema = z
  .object({
    current_password: z.string().min(1, "Required"),
    new_password: z.string().min(8, "At least 8 characters").max(72).regex(/[A-Za-z]/, "Include a letter").regex(/\d/, "Include a digit"),
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });

function AccountTab() {
  const { me } = useMe();
  const { register, handleSubmit, reset, formState } = useForm<z.infer<typeof pwSchema>>({ resolver: zodResolver(pwSchema) });
  const action = useAction((v: z.infer<typeof pwSchema>) => post("/auth/change-password", { current_password: v.current_password, new_password: v.new_password }), {
    success: "Password changed. Other sessions have been signed out.",
    onSuccess: () => reset({ current_password: "", new_password: "", confirm: "" }),
  });
  const e = formState.errors;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Profile" />
        <CardBody className="space-y-2 text-sm">
          {[
            ["Name", me?.full_name],
            ["Email", me?.email],
            ["Role", me?.role ? ROLE_LABELS[me.role] : "—"],
            ["Hospital", me?.hospital?.name ?? "—"],
            ["Department", me?.department?.name ?? "—"],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between"><span className="text-muted-foreground">{k}</span><span>{v}</span></div>
          ))}
          <div className="pt-2">
            <p className="mb-1 text-xs text-muted-foreground">Permissions</p>
            <div className="flex flex-wrap gap-1">{me?.permissions.map((p) => <Badge key={p}>{p}</Badge>)}</div>
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Change password" description="Signs out your other sessions." />
        <CardBody>
          <form onSubmit={handleSubmit((v) => action.mutate(v))} className="space-y-4">
            <Field label="Current password" htmlFor="cp" error={e.current_password?.message}>
              <Input id="cp" type="password" autoComplete="current-password" {...register("current_password")} />
            </Field>
            <Field label="New password" htmlFor="np" error={e.new_password?.message}>
              <Input id="np" type="password" autoComplete="new-password" {...register("new_password")} />
            </Field>
            <Field label="Confirm new password" htmlFor="cf" error={e.confirm?.message}>
              <Input id="cf" type="password" autoComplete="new-password" {...register("confirm")} />
            </Field>
            <Button type="submit" disabled={action.isPending}>{action.isPending && <Loader2 className="animate-spin" />} Update password</Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

/* ---------------- Hospital ---------------- */

const hospitalSchema = z.object({
  name: z.string().trim().min(2).max(200),
  city: z.string().max(100).optional().nullable(),
  state: z.string().max(100).optional().nullable(),
  bed_count: z.number().int().min(0).max(10000).optional().nullable(),
  expiry_warning_days: z.number({ error: "Required" }).int().min(1).max(365),
});

function HospitalTab() {
  const { can } = useMe();
  const editable = can(PERM.MANAGE_HOSPITAL);
  const { data } = useQuery({ queryKey: ["hospital"], queryFn: () => get<Hospital>("/hospitals/current") });
  const { register, handleSubmit, reset, formState } = useForm<z.infer<typeof hospitalSchema>>({ resolver: zodResolver(hospitalSchema) });
  useEffect(() => {
    if (data) reset(data);
  }, [data, reset]);
  const action = useAction((v: z.infer<typeof hospitalSchema>) => patch<Hospital>("/hospitals/current", v), {
    invalidate: [["hospital"], ["me"], ["alerts"]],
    success: "Hospital settings saved",
  });
  const e = formState.errors;
  return (
    <Card className="max-w-2xl">
      <CardHeader title="Hospital profile & inventory policy" description={data ? `Code ${data.code}` : undefined} />
      <CardBody>
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" htmlFor="h_name" error={e.name?.message} className="sm:col-span-2">
            <Input id="h_name" disabled={!editable} {...register("name")} />
          </Field>
          <Field label="City" htmlFor="h_city"><Input id="h_city" disabled={!editable} {...register("city")} /></Field>
          <Field label="State" htmlFor="h_state"><Input id="h_state" disabled={!editable} {...register("state")} /></Field>
          <Field label="Beds" htmlFor="h_beds"><Input id="h_beds" type="number" disabled={!editable} {...register("bed_count", { setValueAs: asInt })} /></Field>
          <Field label="Expiry warning window (days)" htmlFor="h_exp" error={e.expiry_warning_days?.message} hint="Batches expiring within this window raise an alert">
            <Input id="h_exp" type="number" disabled={!editable} {...register("expiry_warning_days", { setValueAs: asInt })} />
          </Field>
          {editable && (
            <div className="sm:col-span-2">
              <Button type="submit" disabled={action.isPending}>{action.isPending && <Loader2 className="animate-spin" />} Save</Button>
            </div>
          )}
        </form>
      </CardBody>
    </Card>
  );
}

/* ---------------- Departments & categories ---------------- */

function DepartmentsTab() {
  const { can } = useMe();
  const manage = can(PERM.MANAGE_DEPARTMENTS);
  const { data } = useDepartments(true);
  return (
    <Card>
      <CardHeader title="Departments" description="Departments are the consumers stock is issued to." action={manage && <DepartmentDialog trigger={<Button size="sm"><Plus /> New department</Button>} />} />
      <Table>
        <THead><tr><TH>Code</TH><TH>Name</TH><TH>Description</TH><TH>Status</TH>{manage && <TH />}</tr></THead>
        <tbody>
          {data?.map((d) => (
            <TR key={d.id}>
              <TD className="font-mono text-xs">{d.code}</TD>
              <TD className="font-medium">{d.name}</TD>
              <TD className="text-muted-foreground">{d.description ?? "—"}</TD>
              <TD>{d.is_active ? <Badge tone="green">Active</Badge> : <Badge>Inactive</Badge>}</TD>
              {manage && <TD className="text-right"><DepartmentDialog dept={d} trigger={<Button size="icon" variant="ghost" aria-label="Edit"><Pencil /></Button>} /></TD>}
            </TR>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

function CategoriesTab() {
  const { can } = useMe();
  const manage = can(PERM.MANAGE_CATALOG);
  const { data } = useCategories();
  return (
    <Card>
      <CardHeader title="Consumable categories" action={manage && <CategoryDialog trigger={<Button size="sm"><Plus /> New category</Button>} />} />
      <Table>
        <THead><tr><TH>Name</TH><TH>Description</TH>{manage && <TH />}</tr></THead>
        <tbody>
          {data?.map((c) => (
            <TR key={c.id}>
              <TD className="font-medium">{c.name}</TD>
              <TD className="text-muted-foreground">{c.description ?? "—"}</TD>
              {manage && <TD className="text-right"><CategoryDialog category={c} trigger={<Button size="icon" variant="ghost" aria-label="Edit"><Pencil /></Button>} /></TD>}
            </TR>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

/* ---------------- Users ---------------- */

function UsersTab() {
  const { data } = useQuery({ queryKey: ["users"], queryFn: () => get<User[]>("/users") });
  return (
    <Card>
      <CardHeader title="Members of this hospital" description="Role, department and access apply to this hospital only. Deactivated members lose access immediately." action={<UserDialog trigger={<Button size="sm"><Plus /> Invite user</Button>} />} />
      <Table>
        <THead><tr><TH>Name</TH><TH>Role</TH><TH>Department</TH><TH>Last sign-in</TH><TH>Status</TH><TH /></tr></THead>
        <tbody>
          {data?.map((u) => (
            <TR key={u.id}>
              <TD><div className="font-medium">{u.full_name}</div><div className="text-xs text-muted-foreground">{u.email}</div></TD>
              <TD>{ROLE_LABELS[u.role]}</TD>
              <TD>{u.department?.name ?? "—"}</TD>
              <TD className="text-muted-foreground">{u.last_login_at ? timeAgo(u.last_login_at) : "Never"}</TD>
              <TD>{u.is_active ? <Badge tone="green">Active</Badge> : <Badge tone="red">Deactivated</Badge>}</TD>
              <TD className="text-right"><UserDialog user={u} trigger={<Button size="icon" variant="ghost" aria-label="Edit"><Pencil /></Button>} /></TD>
            </TR>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

/* ---------------- V8: CSV import (into the active hospital only) ---------------- */

const IMPORT_KINDS = {
  departments: { label: "Departments", perm: PERM.MANAGE_DEPARTMENTS, sample: "code,name,description\nPAED,Paediatrics,Children's ward" },
  suppliers: { label: "Suppliers", perm: PERM.MANAGE_SUPPLIERS, sample: "code,name,city,default_lead_time_days,email\nABC,ABC Surgicals,Pune,4,orders@abc.example" },
  items: { label: "Items", perm: PERM.MANAGE_CATALOG, sample: "sku,name,unit,category,unit_cost,reorder_level,max_level\nGLV-EXM-S,Examination gloves small,pair,Gloves & PPE,6.5,200,2000" },
  opening_stock: { label: "Opening stock", perm: PERM.STOCK_RECEIVE, sample: "sku,lot_number,quantity,expiry_date,unit_cost,supplier_code\nGLV-EXM-S,LOT-001,500,2028-03-31,6.2,ABC" },
} as const;

function ImportTab() {
  const { me, can } = useMe();
  const kinds = (Object.keys(IMPORT_KINDS) as (keyof typeof IMPORT_KINDS)[]).filter((k) => can(IMPORT_KINDS[k].perm));
  const [kind, setKind] = useState<keyof typeof IMPORT_KINDS>(kinds[0] ?? "items");
  const [csv, setCsv] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const run = useAction((dry_run: boolean) => post<ImportResult>(`/imports/${kind}`, { csv, dry_run }), {
    success: (r) => (r.dry_run ? `${r.valid} of ${r.rows} rows valid` : r.created ? `${r.created} rows imported` : "Nothing imported — fix the errors"),
    invalidate: [["consumables"], ["inventory"], ["suppliers"], ["departments"], ["categories"]],
    onSuccess: (r) => setResult(r),
  });
  if (!kinds.length) return <Card><CardBody className="text-sm text-muted-foreground">You do not have permission to import data.</CardBody></Card>;
  return (
    <Card data-testid="import-tab">
      <CardHeader title={`Import CSV into ${me?.hospital?.name ?? "this hospital"}`}
        description="Rows always go into the hospital you are working in. Everything is validated first; one bad row means nothing is written; existing codes / SKUs are never overwritten." />
      <CardBody className="space-y-4">
        <div className="flex flex-wrap gap-3">
          <Field label="Type">
            <select className="h-9 rounded-md border px-2 text-sm" value={kind} onChange={(e) => { setKind(e.target.value as keyof typeof IMPORT_KINDS); setResult(null); }}>
              {kinds.map((k) => <option key={k} value={k}>{IMPORT_KINDS[k].label}</option>)}
            </select>
          </Field>
          <Field label="File"><Input type="file" accept=".csv,text/csv" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setCsv(await f.text()); }} /></Field>
        </div>
        <Field label="CSV" hint={`Columns: ${IMPORT_KINDS[kind].sample.split("\n")[0]}`}>
          <textarea aria-label="CSV" className="min-h-40 w-full rounded-md border px-3 py-2 font-mono text-xs" value={csv} placeholder={IMPORT_KINDS[kind].sample} onChange={(e) => setCsv(e.target.value)} />
        </Field>
        <div className="flex gap-2">
          <Button variant="outline" disabled={!csv || run.isPending} onClick={() => run.mutate(true)}>Validate (dry run)</Button>
          <Button disabled={!csv || run.isPending || !result || !result.dry_run || result.errors.length > 0} onClick={() => run.mutate(false)}>Import</Button>
        </div>
        {result && (
          <div className="space-y-2 text-sm" data-testid="import-result">
            <p>{result.rows} rows · {result.valid} valid · {result.dry_run ? "dry run — nothing written" : `${result.created} imported`}
              {result.created_categories.length > 0 && ` · new categories: ${result.created_categories.join(", ")}`}</p>
            {result.errors.length > 0 && (
              <Table>
                <THead><tr><TH>Line</TH><TH>Error</TH></tr></THead>
                <tbody>{result.errors.map((e) => <TR key={e.line}><TD>{e.line}</TD><TD className="text-red-700">{e.error}</TD></TR>)}</tbody>
              </Table>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/* ---------------- Page ---------------- */

function SettingsTabs() {
  const { can } = useMe();
  const params = useSearchParams();
  const router = useRouter();
  const tabs = [
    { key: "account", label: "My account", el: <AccountTab />, show: true },
    { key: "hospital", label: "Hospital", el: <HospitalTab />, show: true },
    { key: "departments", label: "Departments", el: <DepartmentsTab />, show: true },
    { key: "categories", label: "Categories", el: <CategoriesTab />, show: true },
    { key: "users", label: "Members", el: <UsersTab />, show: can(PERM.MANAGE_USERS) },
    { key: "import", label: "Import CSV", el: <ImportTab />, show: can(PERM.MANAGE_CATALOG, PERM.MANAGE_SUPPLIERS, PERM.MANAGE_DEPARTMENTS, PERM.STOCK_RECEIVE) },
  ].filter((t) => t.show);
  const current = tabs.find((t) => t.key === params.get("tab"))?.key ?? tabs[0].key;

  return (
    <>
      <PageHeader title="Settings" />
      <Tabs.Root value={current} onValueChange={(v) => router.replace(`/settings?tab=${v}`)}>
        <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b">
          {tabs.map((t) => (
            <Tabs.Trigger
              key={t.key}
              value={t.key}
              className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800"
            >
              {t.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        {tabs.map((t) => (
          <Tabs.Content key={t.key} value={t.key}>{t.el}</Tabs.Content>
        ))}
      </Tabs.Root>
    </>
  );
}

export default function SettingsPage() {
  return (
    <Suspense>
      <SettingsTabs />
    </Suspense>
  );
}
