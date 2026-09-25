"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { PERM, useMe } from "@/hooks/use-me";
import { asInt, asOptionalId, STOCK_KEYS, useAction, useConsumables, useDepartments, useSuppliers } from "@/hooks/use-lookups";
import { get, post } from "@/lib/api";
import type { Batch, InventoryDetail, MovementResult, Page, SupplierOrderRow, SupplierProduct } from "@/lib/types";
import { formatNumber, todayISO } from "@/lib/utils";

const qty = z.number({ error: "Enter a quantity" }).int("Whole units only").positive("Must be greater than 0");
const id = z.number({ error: "Required" }).int().positive("Required");

function useItemStock(consumableId: number | undefined) {
  return useQuery({
    queryKey: ["inventory", "detail", consumableId],
    queryFn: () => get<InventoryDetail>(`/inventory/${consumableId}`),
    enabled: !!consumableId,
  });
}

function ItemPicker({ register, error, fixed }: { register: ReturnType<typeof useForm>["register"]; error?: string; fixed?: boolean }) {
  const items = useConsumables();
  return (
    <Field label="Item" htmlFor="consumable_id" error={error}>
      <Select id="consumable_id" aria-readonly={fixed} tabIndex={fixed ? -1 : undefined} className={fixed ? "pointer-events-none bg-muted" : ""} {...register("consumable_id", { setValueAs: asInt })}>
        <option value="">Select an item…</option>
        {items.data?.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name} ({c.sku})
          </option>
        ))}
      </Select>
    </Field>
  );
}

/* ---------------- Receive ---------------- */

const receiveSchema = z.object({
  consumable_id: id,
  quantity: qty,
  lot_number: z.string().trim().min(1, "Lot / batch number is required").max(64),
  expiry_date: z.string().optional(),
  supplier_id: z.number().int().nullable(),
  unit_cost: z.number().nonnegative().optional(),
  reference: z.string().max(100).optional(),
  notes: z.string().optional(),
  supplier_order_id: z.number().int().nullable().optional(), // V4: delivery against a supplier order
});
type ReceiveValues = z.infer<typeof receiveSchema>;

export function ReceiveDialog({ consumableId, orderId, trigger }: { consumableId?: number; orderId?: number; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const suppliers = useSuppliers();
  const form = useForm<ReceiveValues>({ resolver: zodResolver(receiveSchema) });
  const { register, handleSubmit, reset, setValue, formState, control } = form;
  const itemId = useWatch({ control, name: "consumable_id" });
  const supplierId = useWatch({ control, name: "supplier_id" });
  const orderSel = useWatch({ control, name: "supplier_order_id" });
  const detail = useItemStock(open ? itemId : undefined);
  const orders = useQuery({
    queryKey: ["supplier-orders", "receivable", itemId],
    queryFn: () => get<Page<SupplierOrderRow>>("/supplier-orders", { status: "active", consumable_id: itemId, page_size: 50 }),
    enabled: open && !!itemId,
  });

  useEffect(() => {
    if (open) reset({ consumable_id: consumableId, supplier_id: null, lot_number: "", reference: "", notes: "", expiry_date: "",
      supplier_order_id: orderId ?? null });
  }, [open, consumableId, orderId, reset]);

  // V4: receiving against an order fixes the supplier, price and outstanding quantity
  useEffect(() => {
    const o = orders.data?.items.find((x) => x.id === orderSel);
    if (o) {
      setValue("supplier_id", o.supplier_id);
      setValue("unit_cost", o.unit_price);
      setValue("quantity", o.quantity_ordered - o.quantity_received);
    }
  }, [orderSel, orders.data, setValue]);

  // Pre-fill unit cost from the supplier's catalogue price
  useEffect(() => {
    if (orderSel) return;
    const offer = detail.data?.suppliers.find((s: SupplierProduct) => s.supplier.id === supplierId);
    if (offer) setValue("unit_cost", offer.unit_price);
  }, [supplierId, detail.data, setValue, orderSel]);

  const action = useAction(
    (v: ReceiveValues) =>
      post<MovementResult>("/inventory/receive", {
        ...v,
        expiry_date: v.expiry_date || null,
        reference: v.reference || null,
        notes: v.notes || null,
        supplier_order_id: v.supplier_order_id ?? null,
      }),
    {
      invalidate: [...STOCK_KEYS, ["supplier-orders"], ["supplier-intel"], ["stockout-risks"]],
      success: (r) => `Received ${formatNumber(r.movements[0].quantity)} ${r.movements[0].consumable.unit}. Usable stock: ${formatNumber(r.usable_stock)}`,
      onSuccess: () => setOpen(false),
    },
  );

  const offeredBy = new Set(detail.data?.suppliers.map((s) => s.supplier.id));
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title="Receive stock" description="Record a delivery (GRN). A new batch is created with its lot and expiry.">
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <ItemPicker register={register as never} error={formState.errors.consumable_id?.message} fixed={!!consumableId} />
          </div>
          {(orders.data?.items.length ?? 0) > 0 && (
            <Field label="Against supplier order" htmlFor="supplier_order_id" className="sm:col-span-2" hint="Records the delivery for supplier reliability (V4)">
              <Select id="supplier_order_id" {...register("supplier_order_id", { setValueAs: asOptionalId })}>
                <option value="">— Not against an order —</option>
                {orders.data!.items.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.reference} · {o.supplier} · {o.quantity_ordered - o.quantity_received} outstanding · expected {o.expected_date}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label="Quantity" htmlFor="quantity" error={formState.errors.quantity?.message} hint={detail.data ? `Unit: ${detail.data.item.unit}` : undefined}>
            <Input id="quantity" type="number" min={1} {...register("quantity", { setValueAs: asInt })} />
          </Field>
          <Field label="Lot / batch no." htmlFor="lot_number" error={formState.errors.lot_number?.message}>
            <Input id="lot_number" {...register("lot_number")} />
          </Field>
          <Field label="Expiry date" htmlFor="expiry_date" hint="Leave empty if the item does not expire">
            <Input id="expiry_date" type="date" min={todayISO()} {...register("expiry_date")} />
          </Field>
          <Field label="Supplier" htmlFor="supplier_id">
            <Select id="supplier_id" aria-readonly={!!orderSel} tabIndex={orderSel ? -1 : undefined} className={orderSel ? "pointer-events-none bg-muted" : ""} {...register("supplier_id", { setValueAs: asOptionalId })}>
              <option value="">—</option>
              {suppliers.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {offeredBy.has(s.id) ? " ★" : ""}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Unit cost (₹)" htmlFor="unit_cost" hint="Defaults to item cost" error={formState.errors.unit_cost?.message}>
            <Input id="unit_cost" type="number" step="0.01" min={0} {...register("unit_cost", { setValueAs: asInt })} />
          </Field>
          <Field label="Reference (GRN / invoice)" htmlFor="reference">
            <Input id="reference" {...register("reference")} />
          </Field>
          <Field label="Notes" htmlFor="notes" className="sm:col-span-2">
            <Textarea id="notes" rows={2} {...register("notes")} />
          </Field>
          <div className="sm:col-span-2">
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" type="button">Cancel</Button>
              </DialogClose>
              <Button type="submit" disabled={action.isPending}>
                {action.isPending && <Loader2 className="animate-spin" />} Receive
              </Button>
            </DialogFooter>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- Issue ---------------- */

const issueSchema = z.object({
  consumable_id: id,
  quantity: qty,
  department_id: id,
  reference: z.string().max(100).optional(),
  notes: z.string().optional(),
});
type IssueValues = z.infer<typeof issueSchema>;

export function IssueDialog({ consumableId, trigger }: { consumableId?: number; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const { me, can } = useMe();
  const depts = useDepartments();
  const ownOnly = !can(PERM.STOCK_ISSUE);
  const form = useForm<IssueValues>({ resolver: zodResolver(issueSchema) });
  const { register, handleSubmit, reset, formState, control } = form;
  const itemId = useWatch({ control, name: "consumable_id" });
  const detail = useItemStock(open ? itemId : undefined);

  useEffect(() => {
    if (open) reset({ consumable_id: consumableId, department_id: ownOnly ? (me?.department_id ?? undefined) : undefined, reference: "", notes: "" });
  }, [open, consumableId, reset, ownOnly, me]);

  const action = useAction(
    (v: IssueValues) => post<MovementResult>("/inventory/issue", { ...v, reference: v.reference || null, notes: v.notes || null }),
    {
      invalidate: STOCK_KEYS,
      success: (r) => {
        const n = r.movements.reduce((a, m) => a - m.quantity, 0);
        return `Issued ${formatNumber(n)} from ${r.movements.length} batch${r.movements.length > 1 ? "es" : ""} (FEFO). Usable stock: ${formatNumber(r.usable_stock)}`;
      },
      onSuccess: () => setOpen(false),
    },
  );

  const available = detail.data?.stock.usable_stock;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title="Issue stock" description="Issue to a department. Batches are picked first-expiry-first-out; expired stock is never issued.">
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <ItemPicker register={register as never} error={formState.errors.consumable_id?.message} fixed={!!consumableId} />
          </div>
          <Field
            label="Quantity"
            htmlFor="quantity"
            error={formState.errors.quantity?.message}
            hint={available !== undefined ? `Available: ${formatNumber(available)} ${detail.data?.item.unit}` : undefined}
          >
            <Input id="quantity" type="number" min={1} max={available} {...register("quantity", { setValueAs: asInt })} />
          </Field>
          <Field label="Department" htmlFor="department_id" error={formState.errors.department_id?.message}>
            <Select id="department_id" aria-readonly={ownOnly} tabIndex={ownOnly ? -1 : undefined} className={ownOnly ? "pointer-events-none bg-muted" : ""} {...register("department_id", { setValueAs: asInt })}>
              <option value="">Select…</option>
              {depts.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Indent / reference" htmlFor="reference" className="sm:col-span-2">
            <Input id="reference" {...register("reference")} />
          </Field>
          <Field label="Notes" htmlFor="notes" className="sm:col-span-2">
            <Textarea id="notes" rows={2} {...register("notes")} />
          </Field>
          <div className="sm:col-span-2">
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" type="button">Cancel</Button>
              </DialogClose>
              <Button type="submit" disabled={action.isPending}>
                {action.isPending && <Loader2 className="animate-spin" />} Issue
              </Button>
            </DialogFooter>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- Batch actions: return / wastage / adjust ---------------- */

type BatchMode = "return" | "wastage" | "adjust";

const COPY: Record<BatchMode, { title: string; desc: string; submit: string }> = {
  return: { title: "Return to store", desc: "A department returns unused stock to this batch.", submit: "Record return" },
  wastage: { title: "Record wastage", desc: "Remove damaged, contaminated or expired units from this batch.", submit: "Record wastage" },
  adjust: { title: "Stock count adjustment", desc: "Set the batch to the physically counted quantity.", submit: "Save count" },
};

const batchSchema = z.object({
  quantity: z.number({ error: "Enter a quantity" }).int().min(0),
  department_id: z.number().int().optional(),
  reason: z.string().optional(),
});
type BatchValues = z.infer<typeof batchSchema>;

export function BatchActionDialog({
  batch,
  unit,
  mode,
  trigger,
}: {
  batch: Batch;
  unit: string;
  mode: BatchMode;
  trigger: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const { me, can } = useMe();
  const depts = useDepartments();
  const ownOnly = !can(PERM.STOCK_ISSUE);
  const { register, handleSubmit, reset, setError, formState } = useForm<BatchValues>({ resolver: zodResolver(batchSchema) });

  useEffect(() => {
    if (open)
      reset({
        quantity: mode === "adjust" ? batch.quantity : undefined,
        department_id: ownOnly ? (me?.department_id ?? undefined) : undefined,
        reason: mode === "wastage" && batch.is_expired ? "Expired" : "",
      });
  }, [open, mode, batch, reset, ownOnly, me]);

  const action = useAction(
    (v: BatchValues) => {
      if (mode === "return") return post<MovementResult>("/inventory/return", { batch_id: batch.id, quantity: v.quantity, department_id: v.department_id, reason: v.reason || null });
      if (mode === "wastage") return post<MovementResult>("/inventory/wastage", { batch_id: batch.id, quantity: v.quantity, reason: v.reason });
      return post<MovementResult>("/inventory/adjust", { batch_id: batch.id, counted_quantity: v.quantity, reason: v.reason });
    },
    { invalidate: STOCK_KEYS, success: `${COPY[mode].submit}: done`, onSuccess: () => setOpen(false) },
  );

  const submit = (v: BatchValues) => {
    if (mode !== "return" && (!v.reason || v.reason.trim().length < 3)) return setError("reason", { message: "Give a reason (min 3 characters)" });
    if (mode === "return" && !v.department_id) return setError("department_id", { message: "Select the returning department" });
    if (mode !== "adjust" && v.quantity <= 0) return setError("quantity", { message: "Must be greater than 0" });
    action.mutate(v);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={COPY[mode].title} description={<>{COPY[mode].desc} Lot <b>{batch.lot_number}</b> · {formatNumber(batch.quantity)} {unit} on hand.</>}>
        <form onSubmit={handleSubmit(submit)} className="space-y-4">
          <Field
            label={mode === "adjust" ? "Counted quantity" : "Quantity"}
            htmlFor="quantity"
            error={formState.errors.quantity?.message}
          >
            <Input id="quantity" type="number" min={0} {...register("quantity", { setValueAs: asInt })} />
          </Field>
          {mode === "return" && (
            <Field label="Returning department" htmlFor="department_id" error={formState.errors.department_id?.message}>
              <Select id="department_id" aria-readonly={ownOnly} tabIndex={ownOnly ? -1 : undefined} className={ownOnly ? "pointer-events-none bg-muted" : ""} {...register("department_id", { setValueAs: asInt })}>
                <option value="">Select…</option>
                {depts.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label={mode === "return" ? "Reason (optional)" : "Reason"} htmlFor="reason" error={formState.errors.reason?.message}>
            <Input id="reason" placeholder={mode === "adjust" ? "e.g. Monthly cycle count" : mode === "wastage" ? "e.g. Packaging damaged" : ""} {...register("reason")} />
          </Field>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" type="button">Cancel</Button>
            </DialogClose>
            <Button type="submit" variant={mode === "wastage" ? "danger" : "default"} disabled={action.isPending}>
              {action.isPending && <Loader2 className="animate-spin" />} {COPY[mode].submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
