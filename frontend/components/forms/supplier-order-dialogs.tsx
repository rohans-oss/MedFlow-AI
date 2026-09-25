"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { asInt, useAction, useConsumables, useSuppliers } from "@/hooks/use-lookups";
import { post } from "@/lib/api";
import type { SupplierOrderRow } from "@/lib/types";
import { todayISO } from "@/lib/utils";

export const ORDER_KEYS = [["supplier-orders"], ["supplier-intel"], ["alerts"], ["stockout-risks"]];

const schema = z.object({
  supplier_id: z.number({ error: "Select a supplier" }).int().positive("Select a supplier"),
  consumable_id: z.number({ error: "Select an item" }).int().positive("Select an item"),
  quantity_ordered: z.number({ error: "Enter a quantity" }).int("Whole units").min(1, "At least 1"),
  unit_price: z.number().min(0).optional().nullable(),
  ordered_date: z.string().min(1, "Pick a date"),
  expected_date: z.string().optional().nullable(),
  reference: z.string().max(64).optional().nullable(),
  notes: z.string().max(1000).optional().nullable(),
});
type Values = z.infer<typeof schema>;

/** Record an order that was placed with a supplier (tracking only — this does not send anything to the supplier). */
export function RecordOrderDialog({ trigger, consumableId, supplierId }: { trigger: React.ReactNode; consumableId?: number; supplierId?: number }) {
  const [open, setOpen] = useState(false);
  const suppliers = useSuppliers();
  const items = useConsumables();
  const { register, handleSubmit, reset, formState, control, setError } = useForm<Values>({ resolver: zodResolver(schema) });
  useEffect(() => {
    if (open) reset({ consumable_id: consumableId, supplier_id: supplierId, ordered_date: todayISO(), expected_date: "", reference: "", notes: "" });
  }, [open, reset, consumableId, supplierId]);
  const ordered = useWatch({ control, name: "ordered_date" });
  const action = useAction(
    (v: Values) =>
      post<SupplierOrderRow>("/supplier-orders", {
        ...v, unit_price: v.unit_price ?? null, expected_date: v.expected_date || null,
        reference: v.reference?.trim() || null, notes: v.notes?.trim() || null,
      }),
    { invalidate: ORDER_KEYS, success: (o) => `Order ${o.reference} recorded — expected ${o.expected_date}`, onSuccess: () => setOpen(false) },
  );
  const submit = (v: Values) => {
    if (v.ordered_date > todayISO()) return setError("ordered_date", { message: "Order date can't be in the future" });
    if (v.expected_date && v.expected_date < v.ordered_date) return setError("expected_date", { message: "Before the order date" });
    action.mutate(v);
  };
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title="Record supplier order" description="Log an order you placed with a supplier, so deliveries can be tracked against it. MedFlow does not send orders.">
        <form onSubmit={handleSubmit(submit)} className="grid gap-4 sm:grid-cols-2">
          <Field label="Supplier" htmlFor="so_sup" error={e.supplier_id?.message}>
            <Select id="so_sup" {...register("supplier_id", { setValueAs: asInt })}>
              <option value="">Select…</option>
              {suppliers.data?.filter((s) => s.is_active).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
          </Field>
          <Field label="Item" htmlFor="so_item" error={e.consumable_id?.message}>
            <Select id="so_item" {...register("consumable_id", { setValueAs: asInt })}>
              <option value="">Select…</option>
              {items.data?.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.sku})</option>)}
            </Select>
          </Field>
          <Field label="Quantity ordered" htmlFor="so_qty" error={e.quantity_ordered?.message}>
            <Input id="so_qty" type="number" min={1} {...register("quantity_ordered", { setValueAs: asInt })} />
          </Field>
          <Field label="Unit price (₹)" htmlFor="so_price" hint="Defaults to the supplier's catalogue price" error={e.unit_price?.message}>
            <Input id="so_price" type="number" step="0.01" min={0} {...register("unit_price", { setValueAs: asInt })} />
          </Field>
          <Field label="Order date" htmlFor="so_date" error={e.ordered_date?.message}>
            <Input id="so_date" type="date" max={todayISO()} {...register("ordered_date")} />
          </Field>
          <Field label="Expected delivery" htmlFor="so_exp" hint="Defaults to order date + quoted lead time" error={e.expected_date?.message}>
            <Input id="so_exp" type="date" min={ordered || undefined} {...register("expected_date")} />
          </Field>
          <Field label="Reference (PO number)" htmlFor="so_ref" hint="Auto-generated if empty">
            <Input id="so_ref" {...register("reference")} />
          </Field>
          <Field label="Notes" htmlFor="so_notes" className="sm:col-span-2">
            <Textarea id="so_notes" rows={2} {...register("notes")} />
          </Field>
          <DialogFooter>
            <DialogClose asChild><Button variant="outline" type="button">Cancel</Button></DialogClose>
            <Button type="submit" disabled={action.isPending}>{action.isPending && <Loader2 className="animate-spin" />} Record order</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** OPEN → cancelled; PARTIAL → closed short (the rest will not come). */
export function CloseOrderDialog({ order, trigger }: { order: SupplierOrderRow; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const partial = order.status === "PARTIAL";
  const action = useAction(() => post<SupplierOrderRow>(`/supplier-orders/${order.id}/cancel`, { reason: reason || null }), {
    invalidate: ORDER_KEYS,
    success: partial ? `Order ${order.reference} closed short` : `Order ${order.reference} cancelled`,
    onSuccess: () => setOpen(false),
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent
        title={partial ? "Close order short" : "Cancel order"}
        description={`${order.reference}: ${order.quantity_ordered} × ${order.item} from ${order.supplier}${partial ? ` — ${order.quantity_received} received; the rest will be marked as not supplied.` : "."} This counts against the supplier's reliability.`}
      >
        <form onSubmit={(ev) => { ev.preventDefault(); action.mutate(); }} className="space-y-4">
          <Field label="Reason" htmlFor="co_reason">
            <Input id="co_reason" value={reason} onChange={(ev) => setReason(ev.target.value)} placeholder="e.g. Supplier backordered" />
          </Field>
          <DialogFooter>
            <DialogClose asChild><Button variant="outline" type="button">Keep order</Button></DialogClose>
            <Button type="submit" variant="danger" disabled={action.isPending}>{partial ? "Close short" : "Cancel order"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
