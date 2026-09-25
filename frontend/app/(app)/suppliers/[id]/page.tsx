"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Pencil, Plus, Star, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { MovementBadge } from "@/components/badges";
import { SupplierDialog, SupplierProductDialog } from "@/components/forms/entity-dialogs";
import { SupplierPerformance } from "@/components/supplier-performance";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { del, get } from "@/lib/api";
import type { Movement, Page, Supplier, SupplierProduct } from "@/lib/types";
import { formatDateTime, formatINR, formatNumber } from "@/lib/utils";

export default function SupplierDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useMe();
  const manage = can(PERM.MANAGE_SUPPLIERS);
  const supplier = useQuery({ queryKey: ["suppliers", "detail", id], queryFn: () => get<Supplier>(`/suppliers/${id}`) });
  const products = useQuery({ queryKey: ["supplier-products", id], queryFn: () => get<SupplierProduct[]>(`/suppliers/${id}/products`) });
  const receipts = useQuery({
    queryKey: ["movements", "supplier", id],
    queryFn: () => get<Page<Movement>>("/inventory/movements", { supplier_id: id, movement_type: "RECEIPT", page_size: 15 }),
  });
  const remove = useAction((pid: number) => del(`/suppliers/${id}/products/${pid}`), {
    invalidate: [["supplier-products", id], ["suppliers"], ["inventory"]],
    success: "Removed from catalogue",
  });

  if (supplier.error) return <EmptyState title="Supplier not found" />;
  if (!supplier.data) return <Skeleton className="h-96" />;
  const s = supplier.data;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/suppliers" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Suppliers
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold tracking-tight">{s.name}</h1>
              {!s.is_active && <Badge>Inactive</Badge>}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {s.code} · {s.city ?? "—"} · default lead time {s.default_lead_time_days} days
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link href={`/knowledge-graph?tab=impact&mode=supplier&supplier=${s.id}`} className="text-sm font-medium text-teal-700 hover:underline">Impact if unavailable</Link>
            {manage && <SupplierDialog supplier={s} trigger={<Button variant="outline"><Pencil /> Edit</Button>} />}
          </div>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader title="Contact" />
          <CardBody className="space-y-2 text-sm">
            {[
              ["Contact person", s.contact_person],
              ["Phone", s.phone],
              ["Email", s.email],
              ["Address", s.address],
              ["GSTIN", s.gstin],
              ["Notes", s.notes],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4">
                <span className="text-muted-foreground">{k}</span>
                <span className="text-right">{v || "—"}</span>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader
            title="Catalogue"
            description="Items this supplier can deliver"
            action={
              manage && (
                <SupplierProductDialog supplierId={s.id} defaultLead={s.default_lead_time_days} trigger={<Button size="sm"><Plus /> Add item</Button>} />
              )
            }
          />
          {!products.data?.length ? (
            <EmptyState title="No items listed yet" />
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Item</TH>
                  <TH className="text-right">Unit price</TH>
                  <TH className="text-right">Lead time</TH>
                  <TH className="text-right">MOQ</TH>
                  {manage && <TH />}
                </tr>
              </THead>
              <tbody>
                {products.data.map((p) => (
                  <TR key={p.id}>
                    <TD>
                      <Link href={`/inventory/${p.consumable.id}`} className="inline-flex items-center gap-1 hover:underline">
                        {p.is_preferred && <Star className="size-3.5 fill-amber-400 text-amber-400" aria-label="Preferred" />}
                        {p.consumable.name}
                      </Link>
                      <div className="text-xs text-muted-foreground">{p.supplier_sku ?? p.consumable.sku}</div>
                    </TD>
                    <TD className="text-right tabular-nums">{formatINR(p.unit_price, true)}</TD>
                    <TD className="text-right tabular-nums">{p.lead_time_days} d</TD>
                    <TD className="text-right tabular-nums">{formatNumber(p.moq)}</TD>
                    {manage && (
                      <TD className="text-right">
                        <div className="flex justify-end gap-1">
                          <SupplierProductDialog
                            supplierId={s.id}
                            product={p}
                            defaultLead={s.default_lead_time_days}
                            trigger={<Button size="icon" variant="ghost" aria-label="Edit"><Pencil /></Button>}
                          />
                          <Button
                            size="icon"
                            variant="ghost"
                            aria-label="Remove"
                            onClick={() => window.confirm(`Remove ${p.consumable.name} from this catalogue?`) && remove.mutate(p.id)}
                          >
                            <Trash2 className="text-red-600" />
                          </Button>
                        </div>
                      </TD>
                    )}
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>

      <div>
        <h2 className="mb-3 text-base font-semibold">Performance</h2>
        <SupplierPerformance supplierId={s.id} />
      </div>

      <Card>
        <CardHeader title="Recent deliveries" description="Receipts recorded against this supplier" />
        {!receipts.data?.items.length ? (
          <EmptyState title="No deliveries recorded" />
        ) : (
          <Table>
            <tbody>
              {receipts.data.items.map((m) => (
                <TR key={m.id}>
                  <TD className="w-40 whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(m.created_at)}</TD>
                  <TD className="w-24"><MovementBadge type={m.movement_type} /></TD>
                  <TD><Link href={`/inventory/${m.consumable.id}`} className="hover:underline">{m.consumable.name}</Link></TD>
                  <TD className="font-mono text-xs">{m.batch?.lot_number}</TD>
                  <TD className="text-right tabular-nums">+{formatNumber(m.quantity)} {m.consumable.unit}</TD>
                  <TD className="text-xs text-muted-foreground">{m.reference ?? ""}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
