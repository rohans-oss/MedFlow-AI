"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Button, Field, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { asInt, asOptionalId, useAction, useCategories, useConsumables, useDepartments } from "@/hooks/use-lookups";
import { patch, post } from "@/lib/api";
import type { Category, Consumable, Department, Supplier, SupplierProduct, User } from "@/lib/types";
import { ROLE_LABELS } from "@/lib/utils";

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

function Check({ id, label, ...rest }: { id: string; label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className="flex items-center gap-2">
      <input id={id} type="checkbox" className="size-4 accent-teal-700" {...rest} />
      <Label htmlFor={id} className="font-normal">{label}</Label>
    </div>
  );
}

const optStr = z.string().optional().nullable();
const nn = (v?: string | null) => (v && v.trim() ? v.trim() : null);

/* ---------------- Consumable ---------------- */

const consumableSchema = z
  .object({
    sku: z.string().trim().min(2, "At least 2 characters").max(64).regex(/^[A-Za-z0-9._-]+$/, "Letters, digits, . _ - only"),
    name: z.string().trim().min(2, "Required").max(200),
    category_id: z.number().int().nullable(),
    unit: z.string().trim().min(1, "Required").max(32),
    unit_cost: z.number({ error: "Required" }).nonnegative(),
    reorder_level: z.number({ error: "Required" }).int().nonnegative(),
    max_level: z.number().int().nonnegative().optional(),
    description: optStr,
    is_active: z.boolean(),
  })
  .refine((v) => v.max_level === undefined || v.max_level === 0 || v.max_level >= v.reorder_level, {
    path: ["max_level"],
    message: "Must be ≥ reorder level",
  });
type ConsumableValues = z.infer<typeof consumableSchema>;

export function ConsumableDialog({ item, trigger }: { item?: Consumable; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const cats = useCategories();
  const { register, handleSubmit, reset, formState } = useForm<ConsumableValues>({ resolver: zodResolver(consumableSchema) });
  useEffect(() => {
    if (open)
      reset(
        item
          ? { ...item, category_id: item.category?.id ?? null, max_level: item.max_level ?? undefined }
          : { sku: "", name: "", unit: "piece", unit_cost: 0, reorder_level: 0, category_id: null, is_active: true, description: "" },
      );
  }, [open, item, reset]);

  const action = useAction(
    (v: ConsumableValues) => {
      const body = { ...v, description: nn(v.description), max_level: v.max_level || null };
      if (item) {
        const { sku: _sku, ...rest } = body;
        void _sku;
        return patch<Consumable>(`/consumables/${item.id}`, rest);
      }
      return post<Consumable>("/consumables", body);
    },
    {
      invalidate: [["inventory"], ["consumables"], ["alerts"], ["dashboard"]],
      success: item ? "Item updated" : "Item created",
      onSuccess: () => setOpen(false),
    },
  );
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={item ? "Edit item" : "New consumable"} description="Reorder level drives low-stock alerts.">
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="grid gap-4 sm:grid-cols-2">
          <Field label="SKU" htmlFor="sku" error={e.sku?.message}>
            <Input id="sku" readOnly={!!item} className={!!item ? "bg-muted" : ""} {...register("sku")} />
          </Field>
          <Field label="Unit" htmlFor="unit" error={e.unit?.message} hint="piece, pair, box, bottle…">
            <Input id="unit" {...register("unit")} />
          </Field>
          <Field label="Name" htmlFor="name" error={e.name?.message} className="sm:col-span-2">
            <Input id="name" {...register("name")} />
          </Field>
          <Field label="Category" htmlFor="category_id">
            <Select id="category_id" {...register("category_id", { setValueAs: asOptionalId })}>
              <option value="">—</option>
              {cats.data?.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Unit cost (₹)" htmlFor="unit_cost" error={e.unit_cost?.message}>
            <Input id="unit_cost" type="number" step="0.01" min={0} {...register("unit_cost", { setValueAs: asInt })} />
          </Field>
          <Field label="Reorder level" htmlFor="reorder_level" error={e.reorder_level?.message} hint="Alert when usable stock ≤ this">
            <Input id="reorder_level" type="number" min={0} {...register("reorder_level", { setValueAs: asInt })} />
          </Field>
          <Field label="Max level" htmlFor="max_level" error={e.max_level?.message} hint="Optional; flags overstock">
            <Input id="max_level" type="number" min={0} {...register("max_level", { setValueAs: asInt })} />
          </Field>
          <Field label="Description" htmlFor="description" className="sm:col-span-2">
            <Textarea id="description" rows={2} {...register("description")} />
          </Field>
          <div className="sm:col-span-2">
            <Check id="is_active" label="Active (monitored and issuable)" {...register("is_active")} />
          </div>
          <div className="sm:col-span-2">
            <Footer pending={action.isPending} label={item ? "Save" : "Create item"} />
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- Supplier ---------------- */

const supplierSchema = z.object({
  code: z.string().trim().min(2, "At least 2 characters").max(32).regex(/^[A-Za-z0-9_-]+$/, "Letters, digits, _ - only"),
  name: z.string().trim().min(2, "Required").max(200),
  contact_person: optStr,
  email: z.union([z.literal(""), z.email("Invalid email")]).optional().nullable(),
  phone: optStr,
  city: optStr,
  address: optStr,
  gstin: z.union([z.literal(""), z.string().regex(/^[0-9A-Z]{15}$/, "15 characters, uppercase")]).optional().nullable(),
  default_lead_time_days: z.number({ error: "Required" }).int().min(0).max(365),
  notes: optStr,
  is_active: z.boolean(),
});
type SupplierValues = z.infer<typeof supplierSchema>;

export function SupplierDialog({ supplier, trigger }: { supplier?: Supplier; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const { register, handleSubmit, reset, formState } = useForm<SupplierValues>({ resolver: zodResolver(supplierSchema) });
  useEffect(() => {
    if (open) reset(supplier ?? { code: "", name: "", default_lead_time_days: 7, is_active: true });
  }, [open, supplier, reset]);
  const action = useAction(
    (v: SupplierValues) => {
      const body = {
        ...v,
        contact_person: nn(v.contact_person), email: nn(v.email), phone: nn(v.phone), city: nn(v.city),
        address: nn(v.address), gstin: nn(v.gstin), notes: nn(v.notes),
      };
      if (supplier) {
        const { code: _c, ...rest } = body;
        void _c;
        return patch<Supplier>(`/suppliers/${supplier.id}`, rest);
      }
      return post<Supplier>("/suppliers", body);
    },
    { invalidate: [["suppliers"]], success: supplier ? "Supplier updated" : "Supplier created", onSuccess: () => setOpen(false) },
  );
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={supplier ? "Edit supplier" : "New supplier"}>
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="grid gap-4 sm:grid-cols-2">
          <Field label="Code" htmlFor="code" error={e.code?.message}>
            <Input id="code" readOnly={!!supplier} className={!!supplier ? "bg-muted" : ""} {...register("code")} />
          </Field>
          <Field label="Default lead time (days)" htmlFor="lead" error={e.default_lead_time_days?.message}>
            <Input id="lead" type="number" min={0} {...register("default_lead_time_days", { setValueAs: asInt })} />
          </Field>
          <Field label="Name" htmlFor="sname" error={e.name?.message} className="sm:col-span-2">
            <Input id="sname" {...register("name")} />
          </Field>
          <Field label="Contact person" htmlFor="contact">
            <Input id="contact" {...register("contact_person")} />
          </Field>
          <Field label="Phone" htmlFor="phone">
            <Input id="phone" {...register("phone")} />
          </Field>
          <Field label="Email" htmlFor="semail" error={e.email?.message}>
            <Input id="semail" type="email" {...register("email")} />
          </Field>
          <Field label="GSTIN" htmlFor="gstin" error={e.gstin?.message}>
            <Input id="gstin" {...register("gstin")} />
          </Field>
          <Field label="City" htmlFor="city">
            <Input id="city" {...register("city")} />
          </Field>
          <Field label="Address" htmlFor="address">
            <Input id="address" {...register("address")} />
          </Field>
          <Field label="Notes" htmlFor="snotes" className="sm:col-span-2">
            <Textarea id="snotes" rows={2} {...register("notes")} />
          </Field>
          <div className="sm:col-span-2">
            <Check id="s_active" label="Active" {...register("is_active")} />
          </div>
          <div className="sm:col-span-2">
            <Footer pending={action.isPending} label={supplier ? "Save" : "Create supplier"} />
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- Supplier product ---------------- */

const spSchema = z.object({
  consumable_id: z.number({ error: "Select an item" }).int().positive("Select an item"),
  supplier_sku: optStr,
  unit_price: z.number({ error: "Required" }).positive("Must be > 0"),
  lead_time_days: z.number({ error: "Required" }).int().min(0).max(365),
  moq: z.number({ error: "Required" }).int().min(1),
  is_preferred: z.boolean(),
});
type SpValues = z.infer<typeof spSchema>;

export function SupplierProductDialog({
  supplierId,
  product,
  defaultLead,
  trigger,
}: {
  supplierId: number;
  product?: SupplierProduct;
  defaultLead: number;
  trigger: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const items = useConsumables();
  const { register, handleSubmit, reset, formState } = useForm<SpValues>({ resolver: zodResolver(spSchema) });
  useEffect(() => {
    if (open)
      reset(
        product
          ? { ...product, consumable_id: product.consumable.id }
          : { lead_time_days: defaultLead, moq: 1, is_preferred: false, supplier_sku: "" },
      );
  }, [open, product, defaultLead, reset]);
  const action = useAction(
    (v: SpValues) => {
      const body = { ...v, supplier_sku: nn(v.supplier_sku) };
      if (product) {
        const { consumable_id: _c, ...rest } = body;
        void _c;
        return patch(`/suppliers/${supplierId}/products/${product.id}`, rest);
      }
      return post(`/suppliers/${supplierId}/products`, body);
    },
    {
      invalidate: [["suppliers"], ["supplier-products", String(supplierId)], ["inventory"]],
      success: product ? "Catalogue entry updated" : "Item added to supplier catalogue",
      onSuccess: () => setOpen(false),
    },
  );
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={product ? "Edit catalogue entry" : "Add item to catalogue"}>
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="grid gap-4 sm:grid-cols-2">
          <Field label="Item" htmlFor="sp_item" error={e.consumable_id?.message} className="sm:col-span-2">
            <Select id="sp_item" aria-readonly={!!product} tabIndex={!!product ? -1 : undefined} className={!!product ? "pointer-events-none bg-muted" : ""} {...register("consumable_id", { setValueAs: asInt })}>
              <option value="">Select an item…</option>
              {items.data?.map((c) => (
                <option key={c.id} value={c.id}>{c.name} ({c.sku})</option>
              ))}
            </Select>
          </Field>
          <Field label="Unit price (₹)" htmlFor="sp_price" error={e.unit_price?.message}>
            <Input id="sp_price" type="number" step="0.01" {...register("unit_price", { setValueAs: asInt })} />
          </Field>
          <Field label="Supplier SKU" htmlFor="sp_sku">
            <Input id="sp_sku" {...register("supplier_sku")} />
          </Field>
          <Field label="Lead time (days)" htmlFor="sp_lead" error={e.lead_time_days?.message}>
            <Input id="sp_lead" type="number" {...register("lead_time_days", { setValueAs: asInt })} />
          </Field>
          <Field label="Minimum order qty" htmlFor="sp_moq" error={e.moq?.message}>
            <Input id="sp_moq" type="number" {...register("moq", { setValueAs: asInt })} />
          </Field>
          <div className="sm:col-span-2">
            <Check id="sp_pref" label="Preferred supplier for this item" {...register("is_preferred")} />
          </div>
          <div className="sm:col-span-2">
            <Footer pending={action.isPending} label="Save" />
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- Department ---------------- */

const deptSchema = z.object({
  code: z.string().trim().min(1, "Required").max(32).regex(/^[A-Za-z0-9_-]+$/, "Letters, digits, _ - only"),
  name: z.string().trim().min(2, "Required").max(120),
  description: optStr,
  is_active: z.boolean(),
});
type DeptValues = z.infer<typeof deptSchema>;

export function DepartmentDialog({ dept, trigger }: { dept?: Department; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const { register, handleSubmit, reset, formState } = useForm<DeptValues>({ resolver: zodResolver(deptSchema) });
  useEffect(() => {
    if (open) reset(dept ?? { code: "", name: "", description: "", is_active: true });
  }, [open, dept, reset]);
  const action = useAction(
    (v: DeptValues) =>
      dept
        ? patch(`/departments/${dept.id}`, { name: v.name, description: nn(v.description), is_active: v.is_active })
        : post("/departments", { ...v, description: nn(v.description) }),
    { invalidate: [["departments"]], success: dept ? "Department updated" : "Department created", onSuccess: () => setOpen(false) },
  );
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={dept ? "Edit department" : "New department"}>
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="space-y-4">
          <Field label="Code" htmlFor="d_code" error={e.code?.message}>
            <Input id="d_code" readOnly={!!dept} className={!!dept ? "bg-muted" : ""} {...register("code")} />
          </Field>
          <Field label="Name" htmlFor="d_name" error={e.name?.message}>
            <Input id="d_name" {...register("name")} />
          </Field>
          <Field label="Description" htmlFor="d_desc">
            <Textarea id="d_desc" rows={2} {...register("description")} />
          </Field>
          <Check id="d_active" label="Active" {...register("is_active")} />
          <Footer pending={action.isPending} label="Save" />
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- Category ---------------- */

const catSchema = z.object({ name: z.string().trim().min(2, "Required").max(120), description: optStr });
type CatValues = z.infer<typeof catSchema>;

export function CategoryDialog({ category, trigger }: { category?: Category; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const { register, handleSubmit, reset, formState } = useForm<CatValues>({ resolver: zodResolver(catSchema) });
  useEffect(() => {
    if (open) reset(category ?? { name: "", description: "" });
  }, [open, category, reset]);
  const action = useAction(
    (v: CatValues) =>
      category
        ? patch(`/categories/${category.id}`, { ...v, description: nn(v.description) })
        : post("/categories", { ...v, description: nn(v.description) }),
    { invalidate: [["categories"], ["inventory"]], success: "Category saved", onSuccess: () => setOpen(false) },
  );
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={category ? "Edit category" : "New category"}>
        <form onSubmit={handleSubmit((v) => action.mutate(v))} className="space-y-4">
          <Field label="Name" htmlFor="c_name" error={formState.errors.name?.message}>
            <Input id="c_name" {...register("name")} />
          </Field>
          <Field label="Description" htmlFor="c_desc">
            <Textarea id="c_desc" rows={2} {...register("description")} />
          </Field>
          <Footer pending={action.isPending} label="Save" />
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- User ---------------- */

const pw = z.string().min(8, "At least 8 characters").max(72).regex(/[A-Za-z]/, "Include a letter").regex(/\d/, "Include a digit");
const userSchema = z
  .object({
    email: z.email("Invalid email"),
    full_name: z.string().trim().min(2, "Required").max(200),
    role: z.enum(["admin", "procurement_manager", "inventory_manager", "department_manager", "viewer"]),
    department_id: z.number().int().nullable(),
    password: z.union([z.literal(""), pw]).optional(),
    is_active: z.boolean(),
  })
  .refine((v) => v.role !== "department_manager" || v.department_id, {
    path: ["department_id"],
    message: "Department managers need a department",
  });
type UserValues = z.infer<typeof userSchema>;

export function UserDialog({ user, trigger }: { user?: User; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const depts = useDepartments();
  const { register, handleSubmit, reset, setError, formState } = useForm<UserValues>({ resolver: zodResolver(userSchema) });
  useEffect(() => {
    if (open)
      reset(
        user
          ? { ...user, password: "" }
          : { email: "", full_name: "", role: "viewer", department_id: null, password: "", is_active: true },
      );
  }, [open, user, reset]);
  const action = useAction(
    (v: UserValues) =>
      user
        ? patch(`/users/${user.id}`, {
            full_name: v.full_name, role: v.role, department_id: v.department_id, is_active: v.is_active,
            ...(v.password ? { password: v.password } : {}),
          })
        : post("/users", { email: v.email, full_name: v.full_name, role: v.role, department_id: v.department_id, password: v.password }),
    { invalidate: [["users"]], success: user ? "User updated" : "User created", onSuccess: () => setOpen(false) },
  );
  const submit = (v: UserValues) => {
    if (!user && !v.password) return setError("password", { message: "Set an initial password" });
    action.mutate(v);
  };
  const e = formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={user ? "Edit user" : "Invite user"} description="Role controls what the user can see and change.">
        <form onSubmit={handleSubmit(submit)} className="grid gap-4 sm:grid-cols-2">
          <Field label="Full name" htmlFor="u_name" error={e.full_name?.message}>
            <Input id="u_name" {...register("full_name")} />
          </Field>
          <Field label="Email" htmlFor="u_email" error={e.email?.message}>
            <Input id="u_email" type="email" readOnly={!!user} className={!!user ? "bg-muted" : ""} {...register("email")} />
          </Field>
          <Field label="Role" htmlFor="u_role">
            <Select id="u_role" {...register("role")}>
              {Object.entries(ROLE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </Select>
          </Field>
          <Field label="Department" htmlFor="u_dept" error={e.department_id?.message}>
            <Select id="u_dept" {...register("department_id", { setValueAs: asOptionalId })}>
              <option value="">—</option>
              {depts.data?.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </Select>
          </Field>
          <Field
            label={user ? "Reset password" : "Initial password"}
            htmlFor="u_pw"
            error={e.password?.message}
            hint={user ? "Leave empty to keep the current password" : "Min 8 chars, letters and digits"}
            className="sm:col-span-2"
          >
            <Input id="u_pw" type="password" autoComplete="new-password" {...register("password")} />
          </Field>
          {user && (
            <div className="sm:col-span-2">
              <Check id="u_active" label="Active (can sign in)" {...register("is_active")} />
            </div>
          )}
          <div className="sm:col-span-2">
            <Footer pending={action.isPending} label={user ? "Save" : "Create user"} />
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
