"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { get, post } from "@/lib/api";
import type { RecommendationDetail, RecommendationRow } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

export const PROCUREMENT_KEYS = [["procurement"], ["supplier-orders"], ["supplier-intel"], ["alerts"]];

const money = (v: number) => `₹${formatNumber(Math.round(v))}`;

function linesText(r: RecommendationRow) {
  return r.lines.length ? r.lines.map((l) => `${formatNumber(l.quantity)} ${r.item.unit} from ${l.supplier} at ₹${l.unit_price.toFixed(2)}`).join(" + ") : "No new order";
}

/** Approve as recommended: records the supplier order(s) in MedFlow's order log. Nothing is sent to a supplier. */
export function ApproveDialog({ rec, trigger }: { rec: RecommendationRow; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const action = useAction(() => post<RecommendationDetail>(`/procurement/recommendations/${rec.id}/approve`, { reason: reason || null }), {
    invalidate: PROCUREMENT_KEYS,
    success: (d) => (d.orders.length ? `Approved — recorded ${d.orders.map((o) => o.reference).join(", ")}` : "Approved — no order needed"),
    onSuccess: () => setOpen(false),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (o) setReason(""); }}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={`Approve · ${rec.item.name}`} description="Approving records the order(s) in MedFlow's supplier order log so deliveries can be tracked. MedFlow does not send anything to the supplier or to a purchasing system.">
        <form onSubmit={(e) => { e.preventDefault(); action.mutate(); }} className="space-y-4">
          <div className="rounded-lg border bg-muted/40 p-3 text-sm" data-testid="approve-summary">
            <div className="font-medium">{linesText(rec)}</div>
            {rec.lines.length > 0 && <div className="mt-1 text-xs text-muted-foreground">Purchase value {money(rec.purchase_value)} · expected total cost {money(rec.expected_cost)}</div>}
          </div>
          <Field label="Note (optional)" htmlFor="ap_reason">
            <Input id="ap_reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Budget confirmed with finance" />
          </Field>
          <DialogFooter>
            <DialogClose asChild><Button variant="outline" type="button">Cancel</Button></DialogClose>
            <Button type="submit" disabled={action.isPending}>{action.isPending && <Loader2 className="animate-spin" />} Approve</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

type EditLine = { supplier_id: number; quantity: number; unit_price: string };

/** Modify quantity and/or supplier (a reason is required), then approve. The changed plan is re-evaluated. */
export function ModifyDialog({ rec, trigger }: { rec: RecommendationRow; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [lines, setLines] = useState<EditLine[]>([]);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const detail = useQuery({
    queryKey: ["procurement", "recommendation", rec.id],
    queryFn: () => get<RecommendationDetail>(`/procurement/recommendations/${rec.id}`),
    enabled: open,
  });
  // suppliers that list this item (every supplier that appeared in a scenario) with their price and MOQ
  const suppliers = useMemo(() => {
    const m = new Map<number, { id: number; name: string; code: string; price: number; moq: number }>();
    for (const s of detail.data?.scenarios ?? []) for (const l of s.lines) m.set(l.supplier_id, { id: l.supplier_id, name: l.supplier, code: l.code, price: l.unit_price, moq: l.moq });
    return [...m.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [detail.data]);
  const onOpenChange = (o: boolean) => {
    if (o) {
      setLines(rec.lines.map((l) => ({ supplier_id: l.supplier_id, quantity: l.quantity, unit_price: "" })));
      setReason("");
      setError(null);
    }
    setOpen(o);
  };
  const action = useAction(
    () => post<RecommendationDetail>(`/procurement/recommendations/${rec.id}/approve`, {
      lines: lines.map((l) => ({ supplier_id: l.supplier_id, quantity: l.quantity, unit_price: l.unit_price === "" ? null : Number(l.unit_price) })),
      reason,
    }),
    { invalidate: PROCUREMENT_KEYS, success: (d) => `Approved with changes — recorded ${d.orders.map((o) => o.reference).join(", ") || "no order"}`, onSuccess: () => setOpen(false) },
  );
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (reason.trim().length < 3) return setError("Give a reason for changing the recommendation");
    for (const l of lines) {
      const s = suppliers.find((x) => x.id === l.supplier_id);
      if (!s) return setError("Select a supplier for every line");
      if (!Number.isInteger(l.quantity) || l.quantity < s.moq) return setError(`${s.name}: quantity must be at least its minimum order (${s.moq})`);
    }
    if (new Set(lines.map((l) => l.supplier_id)).size !== lines.length) return setError("Each supplier can appear only once");
    setError(null);
    action.mutate();
  };
  const set = (i: number, patch: Partial<EditLine>) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={`Modify & approve · ${rec.item.name}`} description={`Recommended: ${linesText(rec)}. Change supplier or quantity; the changed plan is re-evaluated with the same cost model and stored with your reason.`}>
        <form onSubmit={submit} className="space-y-4">
          {detail.isLoading ? <Loader2 className="animate-spin" /> : (
            <div className="space-y-2">
              {lines.map((l, i) => {
                const s = suppliers.find((x) => x.id === l.supplier_id);
                return (
                  <div key={i} className="grid grid-cols-[1fr_7rem_7rem_auto] items-end gap-2">
                    <Field label={i === 0 ? "Supplier" : ""} htmlFor={`md_sup_${i}`}>
                      <Select id={`md_sup_${i}`} aria-label={`Supplier ${i + 1}`} value={l.supplier_id || ""} onChange={(e) => set(i, { supplier_id: Number(e.target.value) })}>
                        <option value="">Select…</option>
                        {suppliers.map((x) => <option key={x.id} value={x.id}>{x.name} (₹{x.price.toFixed(2)}, MOQ {x.moq})</option>)}
                      </Select>
                    </Field>
                    <Field label={i === 0 ? "Quantity" : ""} htmlFor={`md_qty_${i}`}>
                      <Input id={`md_qty_${i}`} aria-label={`Quantity ${i + 1}`} type="number" min={s?.moq ?? 1} value={l.quantity} onChange={(e) => set(i, { quantity: Number(e.target.value) })} />
                    </Field>
                    <Field label={i === 0 ? "Unit price ₹" : ""} htmlFor={`md_price_${i}`}>
                      <Input id={`md_price_${i}`} type="number" step="0.01" min={0} placeholder={s ? s.price.toFixed(2) : ""} value={l.unit_price} onChange={(e) => set(i, { unit_price: e.target.value })} />
                    </Field>
                    <Button type="button" variant="ghost" size="sm" aria-label="Remove line" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}><Trash2 /></Button>
                  </div>
                );
              })}
              {lines.length < 4 && (
                <Button type="button" variant="outline" size="sm" onClick={() => setLines((ls) => [...ls, { supplier_id: 0, quantity: 0, unit_price: "" }])}><Plus /> Add supplier line</Button>
              )}
              {!lines.length && <p className="text-xs text-muted-foreground">No lines: approving records no order (&quot;no new order&quot; decision).</p>}
            </div>
          )}
          <Field label="Reason for the change" htmlFor="md_reason" error={error ?? undefined}>
            <Textarea id="md_reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Rate contract with this supplier; storage limited" />
          </Field>
          <DialogFooter>
            <DialogClose asChild><Button variant="outline" type="button">Cancel</Button></DialogClose>
            <Button type="submit" disabled={action.isPending}>{action.isPending && <Loader2 className="animate-spin" />} Approve with changes</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function RejectDialog({ rec, trigger }: { rec: RecommendationRow; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const action = useAction(() => post<RecommendationDetail>(`/procurement/recommendations/${rec.id}/reject`, { reason }), {
    invalidate: PROCUREMENT_KEYS, success: "Recommendation rejected", onSuccess: () => setOpen(false),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (o) { setReason(""); setError(null); } }}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={`Reject · ${rec.item.name}`} description={`Recommended: ${linesText(rec)}. Rejecting records nothing; the reason is kept for audit.`}>
        <form onSubmit={(e) => { e.preventDefault(); if (reason.trim().length < 3) return setError("A reason is required"); action.mutate(); }} className="space-y-4">
          <Field label="Reason" htmlFor="rj_reason" error={error ?? undefined}>
            <Textarea id="rj_reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Stock borrowed from sister hospital" />
          </Field>
          <DialogFooter>
            <DialogClose asChild><Button variant="outline" type="button">Cancel</Button></DialogClose>
            <Button type="submit" variant="danger" disabled={action.isPending}>Reject</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
