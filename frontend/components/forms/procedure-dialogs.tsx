"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Button, Field, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { asInt, useAction, useConsumables, useDepartments } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, patch, post } from "@/lib/api";
import type { MappingRow, ProcedureTypeRow, ScheduleRow } from "@/lib/types";
import { todayISO } from "@/lib/utils";

const PROC_KEYS = [["procedures"], ["forecast"], ["forecasts"]];
const nn = (v?: string | null) => (v && v.trim() ? v.trim() : null);

export function useProcedureTypes(includeInactive = false) {
  return useQuery({
    queryKey: ["procedures", "types", includeInactive],
    queryFn: () => get<ProcedureTypeRow[]>("/procedures/types", { include_inactive: includeInactive }),
  });
}

/** Department managers may only manage their own department. */
export function useProcedureScope() {
  const { me, can } = useMe();
  const all = can(PERM.MANAGE_PROCEDURES);
  const own = !all && can(PERM.MANAGE_PROCEDURES_OWN);
  return {
    canManage: all || own,
    ownDeptOnly: own,
    ownDeptId: me?.department_id ?? null,
    canManageDept: (deptId: number) => all || (own && me?.department_id === deptId),
  };
}

function Footer({ pending, label }: { pending: boolean; label: string }) {
  return (
    <DialogFooter>
      <DialogClose asChild>
        <Button variant="outline" type="button">Cancel</Button>
      </DialogClose>
      <Button type="submit" disabled={pending}>
        {pending && <Loader2 className="animate-spin" />} {label}
      </Button>
    </DialogFooter>
  );
}

const lock = (locked: boolean) => ({
  "aria-readonly": locked,
  tabIndex: locked ? -1 : undefined,
  className: locked ? "pointer-events-none bg-muted" : "",
});

/* ---------------- Procedure type ---------------- */

const typeSchema = z.object({
  code: z.string().trim().min(2, "At least 2 characters").max(32).regex(/^[A-Za-z0-9_-]+$/, "Letters, digits, _ - only"),
  name: z.string().trim().min(2, "Required").max(200),
  department_id: z.number({ error: "Select a department" }).int().positive("Select a department"),
  description: z.string().optional().nullable(),
  avg_duration_minutes: z.number().int().min(1).max(1440).optional().nullable(),
  is_active: z.boolean(),
});
type TypeValues = z.infer<typeof typeSchema>;

export function ProcedureTypeDialog({ type, trigger }: { type?: ProcedureTypeRow; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const depts = useDepartments();
  const scope = useProcedureScope();
  const { register, handleSubmit, reset, formState } = useForm<TypeValues>({ resolver: zodResolver(typeSchema) });
  useEffect(() => {
    if (open)
      reset(
        type
          ? { ...type, department_id: type.department.id }
          : { code: "", name: "", department_id: scope.ownDeptOnly ? (scope.ownDeptId ?? undefined) : undefined, description: "", is_active: true },
      );
  }, [open, type, reset, scope.ownDeptOnly, scope.ownDeptId]);
  const action = useAction(
    (v: TypeValues) => {
      const body = { ...v, description: nn(v.description), avg_duration_minutes: v.avg_duration_minutes ?? null };
      if (type) {
        const { code: _c, ...rest } = body;
        void _c;
        return patch(`/procedures/types/${type.id}`, rest);
      }
      return post("/procedures/types", body);
    },
    { invalidate: PROC_KEYS, success: type ? "Procedure type updated" : "Procedure type created", onSuccess: () => setOpen(false) },
  );
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={type ? "Edit procedure type" : "New procedure type"} description="A kind of procedure a department performs. Counts only — no patient data.">
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="grid gap-4 sm:grid-cols-2">
          <Field label="Code" htmlFor="pt_code" error={e.code?.message}>
            <Input id="pt_code" readOnly={!!type} className={type ? "bg-muted" : ""} {...register("code")} />
          </Field>
          <Field label="Department" htmlFor="pt_dept" error={e.department_id?.message}>
            <Select id="pt_dept" {...lock(scope.ownDeptOnly)} {...register("department_id", { setValueAs: asInt })}>
              <option value="">Select…</option>
              {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </Select>
          </Field>
          <Field label="Name" htmlFor="pt_name" error={e.name?.message} className="sm:col-span-2">
            <Input id="pt_name" {...register("name")} />
          </Field>
          <Field label="Average duration (minutes)" htmlFor="pt_dur" error={e.avg_duration_minutes?.message}>
            <Input id="pt_dur" type="number" min={1} {...register("avg_duration_minutes", { setValueAs: asInt })} />
          </Field>
          <div className="flex items-end gap-2 pb-2">
            <input id="pt_active" type="checkbox" className="size-4 accent-teal-700" {...register("is_active")} />
            <Label htmlFor="pt_active" className="font-normal">Active (can be scheduled)</Label>
          </div>
          <Field label="Description" htmlFor="pt_desc" className="sm:col-span-2">
            <Textarea id="pt_desc" rows={2} {...register("description")} />
          </Field>
          <div className="sm:col-span-2"><Footer pending={action.isPending} label="Save" /></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- Schedule entry ---------------- */

const scheduleSchema = z.object({
  procedure_type_id: z.number({ error: "Select a procedure" }).int().positive("Select a procedure"),
  scheduled_date: z.string().min(1, "Pick a date"),
  count: z.number({ error: "Enter a count" }).int("Whole procedures only").min(1, "At least 1").max(500, "At most 500"),
  status: z.enum(["SCHEDULED", "COMPLETED"]),
  notes: z.string().max(500).optional().nullable(),
});
type ScheduleValues = z.infer<typeof scheduleSchema>;

export function ScheduleDialog({ row, trigger }: { row?: ScheduleRow; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const types = useProcedureTypes();
  const scope = useProcedureScope();
  const { register, handleSubmit, reset, setError, formState } = useForm<ScheduleValues>({ resolver: zodResolver(scheduleSchema) });
  useEffect(() => {
    if (open)
      reset(
        row
          ? { procedure_type_id: row.procedure_type.id, scheduled_date: row.scheduled_date, count: row.count,
              status: row.status === "COMPLETED" ? "COMPLETED" : "SCHEDULED", notes: row.notes ?? "" }
          : { scheduled_date: todayISO(), status: "SCHEDULED", notes: "" },
      );
  }, [open, row, reset]);
  const action = useAction(
    (v: ScheduleValues) =>
      row
        ? patch(`/procedures/schedule/${row.id}`, { scheduled_date: v.scheduled_date, count: v.count, status: v.status, notes: nn(v.notes) })
        : post("/procedures/schedule", { ...v, notes: nn(v.notes) }),
    { invalidate: PROC_KEYS, success: row ? "Schedule updated" : "Procedures scheduled", onSuccess: () => setOpen(false) },
  );
  const submit = (v: ScheduleValues) => {
    const today = todayISO();
    if (v.status === "SCHEDULED" && v.scheduled_date < today) return setError("scheduled_date", { message: "Past dates must be recorded as COMPLETED" });
    if (v.status === "COMPLETED" && v.scheduled_date > today) return setError("scheduled_date", { message: "Future procedures cannot be COMPLETED" });
    action.mutate(v);
  };
  const selectable = (types.data ?? []).filter((t) => scope.canManageDept(t.department.id));
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={row ? "Edit schedule entry" : "Schedule procedures"} description="How many procedures of one type a department has on a day. One entry per procedure, department and date.">
        <form onSubmit={handleSubmit(submit)} className="grid gap-4 sm:grid-cols-2">
          <Field label="Procedure" htmlFor="sc_type" error={e.procedure_type_id?.message} className="sm:col-span-2">
            <Select id="sc_type" {...lock(!!row)} {...register("procedure_type_id", { setValueAs: asInt })}>
              <option value="">Select…</option>
              {selectable.map((t) => <option key={t.id} value={t.id}>{t.name} — {t.department.name}</option>)}
            </Select>
          </Field>
          <Field label="Date" htmlFor="sc_date" error={e.scheduled_date?.message}>
            <Input id="sc_date" type="date" {...register("scheduled_date")} />
          </Field>
          <Field label="Number of procedures" htmlFor="sc_count" error={e.count?.message}>
            <Input id="sc_count" type="number" min={1} max={500} {...register("count", { setValueAs: asInt })} />
          </Field>
          <Field label="Status" htmlFor="sc_status">
            <Select id="sc_status" {...register("status")}>
              <option value="SCHEDULED">Scheduled (planned)</option>
              <option value="COMPLETED">Completed (performed)</option>
            </Select>
          </Field>
          <Field label="Notes" htmlFor="sc_notes">
            <Input id="sc_notes" {...register("notes")} />
          </Field>
          <div className="sm:col-span-2"><Footer pending={action.isPending} label={row ? "Save" : "Schedule"} /></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function CancelScheduleDialog({ row, trigger }: { row: ScheduleRow; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const action = useAction(() => post(`/procedures/schedule/${row.id}/cancel`, { reason: reason || null }), {
    invalidate: PROC_KEYS,
    success: "Procedures cancelled — they no longer count towards expected demand",
    onSuccess: () => setOpen(false),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title="Cancel scheduled procedures" description={`${row.count} × ${row.procedure_type.name} on ${row.scheduled_date} (${row.department.name}).`}>
        <form onSubmit={(ev) => { ev.preventDefault(); action.mutate(); }} className="space-y-4">
          <Field label="Reason (optional)" htmlFor="cx_reason">
            <Input id="cx_reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Surgeon on leave" />
          </Field>
          <DialogFooter>
            <DialogClose asChild><Button variant="outline" type="button">Keep</Button></DialogClose>
            <Button type="submit" variant="danger" disabled={action.isPending}>Cancel procedures</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- Procedure → item mapping ---------------- */

const mappingSchema = z.object({
  procedure_type_id: z.number({ error: "Select a procedure" }).int().positive("Select a procedure"),
  consumable_id: z.number({ error: "Select an item" }).int().positive("Select an item"),
  quantity_per_procedure: z.number({ error: "Enter a quantity" }).positive("Must be greater than 0").max(10000),
  notes: z.string().max(500).optional().nullable(),
  is_active: z.boolean(),
});
type MappingValues = z.infer<typeof mappingSchema>;

export function MappingDialog({ mapping, trigger }: { mapping?: MappingRow; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const types = useProcedureTypes();
  const items = useConsumables();
  const scope = useProcedureScope();
  const { register, handleSubmit, reset, formState } = useForm<MappingValues>({ resolver: zodResolver(mappingSchema) });
  useEffect(() => {
    if (open)
      reset(
        mapping
          ? { procedure_type_id: mapping.procedure_type.id, consumable_id: mapping.consumable.id,
              quantity_per_procedure: mapping.quantity_per_procedure, notes: mapping.notes ?? "", is_active: mapping.is_active }
          : { notes: "", is_active: true },
      );
  }, [open, mapping, reset]);
  const action = useAction(
    (v: MappingValues) =>
      mapping
        ? patch(`/procedures/mappings/${mapping.id}`, { quantity_per_procedure: v.quantity_per_procedure, notes: nn(v.notes), is_active: v.is_active })
        : post("/procedures/mappings", { ...v, notes: nn(v.notes) }),
    { invalidate: PROC_KEYS, success: mapping ? "Mapping updated" : "Item mapped to procedure", onSuccess: () => setOpen(false) },
  );
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={mapping ? "Edit item mapping" : "Map an item to a procedure"} description="Expected units of this item used per procedure. Replace demo values with your hospital's own kits.">
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="grid gap-4 sm:grid-cols-2">
          <Field label="Procedure" htmlFor="mp_type" error={e.procedure_type_id?.message} className="sm:col-span-2">
            <Select id="mp_type" {...lock(!!mapping)} {...register("procedure_type_id", { setValueAs: asInt })}>
              <option value="">Select…</option>
              {(types.data ?? []).filter((t) => scope.canManageDept(t.department.id)).map((t) => (
                <option key={t.id} value={t.id}>{t.name} — {t.department.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Item" htmlFor="mp_item" error={e.consumable_id?.message} className="sm:col-span-2">
            <Select id="mp_item" {...lock(!!mapping)} {...register("consumable_id", { setValueAs: asInt })}>
              <option value="">Select…</option>
              {items.data?.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.sku})</option>)}
            </Select>
          </Field>
          <Field label="Quantity per procedure" htmlFor="mp_qty" error={e.quantity_per_procedure?.message}>
            <Input id="mp_qty" type="number" step="0.01" min={0} {...register("quantity_per_procedure", { setValueAs: asInt })} />
          </Field>
          <div className="flex items-end gap-2 pb-2">
            <input id="mp_active" type="checkbox" className="size-4 accent-teal-700" {...register("is_active")} />
            <Label htmlFor="mp_active" className="font-normal">Active (used in forecasts)</Label>
          </div>
          <Field label="Notes" htmlFor="mp_notes" className="sm:col-span-2">
            <Input id="mp_notes" {...register("notes")} />
          </Field>
          <div className="sm:col-span-2"><Footer pending={action.isPending} label="Save" /></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
