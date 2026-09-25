"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CalendarPlus, FlaskConical, Link2, Pencil, Plus, XCircle } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import {
  CancelScheduleDialog,
  MappingDialog,
  ProcedureTypeDialog,
  ScheduleDialog,
  useProcedureScope,
  useProcedureTypes,
} from "@/components/forms/procedure-dialogs";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  PageHeader,
  Pagination,
  Select,
  Skeleton,
  Stat,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/primitives";
import { useDepartments } from "@/hooks/use-lookups";
import { get } from "@/lib/api";
import type { MappingRow, Page, ProcedureStatus, ProcedureSummary, ScheduleRow } from "@/lib/types";
import { formatDate, formatNumber, todayISO } from "@/lib/utils";

const PAGE_SIZE = 50;
const STATUS_TONE: Record<ProcedureStatus, "blue" | "green" | "neutral"> = { SCHEDULED: "blue", COMPLETED: "green", CANCELLED: "neutral" };

function Synthetic({ on }: { on: boolean }) {
  return on ? <Badge tone="violet" title="Generated demo data — not real hospital data">synthetic</Badge> : null;
}

/* ------------------------------------------------------------------ schedule */

function ScheduleTab() {
  const scope = useProcedureScope();
  const depts = useDepartments();
  const types = useProcedureTypes(true);
  const [f, setF] = useState({ status: "active", department_id: "", procedure_type_id: "", date_from: todayISO(), date_to: "" });
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({
    queryKey: ["procedures", "schedule", f, page],
    queryFn: () => get<Page<ScheduleRow>>("/procedures/schedule", { ...f, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setF({ ...f, [k]: e.target.value });
    setPage(1);
  };

  return (
    <Card>
      <div className="grid gap-3 border-b p-4 sm:grid-cols-2 lg:grid-cols-5">
        <Select value={f.status} onChange={set("status")} aria-label="Status">
          <option value="active">Not cancelled</option>
          <option value="SCHEDULED">Scheduled only</option>
          <option value="COMPLETED">Completed only</option>
          <option value="CANCELLED">Cancelled</option>
          <option value="all">All statuses</option>
        </Select>
        <Select value={f.department_id} onChange={set("department_id")} aria-label="Department">
          <option value="">All departments</option>
          {depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </Select>
        <Select value={f.procedure_type_id} onChange={set("procedure_type_id")} aria-label="Procedure">
          <option value="">All procedures</option>
          {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
        <input type="date" aria-label="From" className="h-9 rounded-md border bg-card px-3 text-sm" value={f.date_from} onChange={set("date_from")} />
        <input type="date" aria-label="To" className="h-9 rounded-md border bg-card px-3 text-sm" value={f.date_to} onChange={set("date_to")} />
      </div>
      {isLoading ? (
        <div className="space-y-2 p-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
      ) : !data?.items.length ? (
        <EmptyState title="No procedures in this window" description="Schedule procedures so forecasts can account for planned activity." />
      ) : (
        <>
          <Table data-testid="schedule-table">
            <THead>
              <tr>
                <TH>Date</TH>
                <TH>Procedure</TH>
                <TH>Department</TH>
                <TH className="text-right">Count</TH>
                <TH>Status</TH>
                <TH>Notes</TH>
                <TH />
              </tr>
            </THead>
            <tbody>
              {data.items.map((r) => {
                const editable = r.status !== "CANCELLED" && scope.canManageDept(r.department.id);
                return (
                  <TR key={r.id}>
                    <TD className="whitespace-nowrap">{formatDate(r.scheduled_date)}</TD>
                    <TD>
                      <span className="font-medium">{r.procedure_type.name}</span>
                      <div className="text-xs text-muted-foreground">{r.procedure_type.code}</div>
                    </TD>
                    <TD>{r.department.name}</TD>
                    <TD className="text-right tabular-nums font-medium">{r.count}</TD>
                    <TD><span className="flex gap-1"><Badge tone={STATUS_TONE[r.status]}>{r.status.toLowerCase()}</Badge><Synthetic on={r.is_synthetic} /></span></TD>
                    <TD className="max-w-64 truncate text-xs text-muted-foreground" title={r.notes ?? ""}>{r.notes ?? "—"}</TD>
                    <TD className="whitespace-nowrap text-right">
                      {editable && (
                        <>
                          <ScheduleDialog row={r} trigger={<Button size="sm" variant="ghost" aria-label="Edit"><Pencil /></Button>} />
                          {r.status === "SCHEDULED" && (
                            <CancelScheduleDialog row={r} trigger={<Button size="sm" variant="ghost" aria-label="Cancel"><XCircle /></Button>} />
                          )}
                        </>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </tbody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ types */

function TypesTab() {
  const scope = useProcedureScope();
  const [showInactive, setShowInactive] = useState(false);
  const { data, isLoading } = useProcedureTypes(showInactive);
  return (
    <Card>
      <CardHeader
        title="Procedure types"
        description="Procedure kinds per department. Inactive types can't be scheduled and are ignored for future forecasts."
        action={
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" className="size-4 accent-teal-700" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
            Show inactive
          </label>
        }
      />
      {isLoading ? (
        <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
      ) : !data?.length ? (
        <EmptyState title="No procedure types" description="Without procedure data, forecasts use consumption history only (V2A)." />
      ) : (
        <Table data-testid="types-table">
          <THead>
            <tr>
              <TH>Procedure</TH>
              <TH>Department</TH>
              <TH className="text-right">Avg duration</TH>
              <TH className="text-right">Mapped items</TH>
              <TH className="text-right">Next 14 days</TH>
              <TH>Status</TH>
              <TH />
            </tr>
          </THead>
          <tbody>
            {data.map((t) => (
              <TR key={t.id}>
                <TD>
                  <span className="font-medium">{t.name}</span>
                  <div className="text-xs text-muted-foreground">{t.code}{t.description ? ` · ${t.description}` : ""}</div>
                </TD>
                <TD>{t.department.name}</TD>
                <TD className="text-right tabular-nums">{t.avg_duration_minutes ? `${t.avg_duration_minutes} min` : "—"}</TD>
                <TD className="text-right tabular-nums">{t.mapping_count}</TD>
                <TD className="text-right tabular-nums">{t.upcoming_count}</TD>
                <TD>
                  <span className="flex gap-1">
                    {t.is_active ? <Badge tone="green">active</Badge> : <Badge>inactive</Badge>}
                    <Synthetic on={t.is_synthetic} />
                  </span>
                </TD>
                <TD className="text-right">
                  {scope.canManageDept(t.department.id) && (
                    <ProcedureTypeDialog type={t} trigger={<Button size="sm" variant="ghost" aria-label={`Edit ${t.code}`}><Pencil /></Button>} />
                  )}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ mappings */

function MappingsTab() {
  const scope = useProcedureScope();
  const types = useProcedureTypes(true);
  const [typeId, setTypeId] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["procedures", "mappings", typeId, showInactive],
    queryFn: () => get<MappingRow[]>("/procedures/mappings", { procedure_type_id: typeId, include_inactive: showInactive }),
  });
  const deptOf = (id: number) => types.data?.find((t) => t.id === id)?.department.id;
  return (
    <Card>
      <CardHeader
        title="Procedure → item mapping"
        description="Expected units of each item per procedure. Forecasts multiply these by scheduled counts. Demo quantities are synthetic, not clinical standards."
        action={
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" className="size-4 accent-teal-700" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
            Show inactive
          </label>
        }
      />
      <div className="border-b p-4">
        <Select className="max-w-sm" value={typeId} onChange={(e) => setTypeId(e.target.value)} aria-label="Procedure filter">
          <option value="">All procedures</option>
          {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
      </div>
      {isLoading ? (
        <div className="space-y-2 p-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
      ) : !data?.length ? (
        <EmptyState title="No mappings" description="Map items to a procedure to let forecasts use the procedure schedule." />
      ) : (
        <Table data-testid="mappings-table">
          <THead>
            <tr>
              <TH>Procedure</TH>
              <TH>Item</TH>
              <TH className="text-right">Qty / procedure</TH>
              <TH>Notes</TH>
              <TH>Status</TH>
              <TH />
            </tr>
          </THead>
          <tbody>
            {data.map((m) => {
              const dept = deptOf(m.procedure_type.id);
              return (
                <TR key={m.id}>
                  <TD><span className="font-medium">{m.procedure_type.name}</span><div className="text-xs text-muted-foreground">{m.procedure_type.code}</div></TD>
                  <TD>{m.consumable.name}<div className="text-xs text-muted-foreground">{m.consumable.sku}</div></TD>
                  <TD className="text-right tabular-nums font-medium">{formatNumber(m.quantity_per_procedure)} <span className="text-xs font-normal text-muted-foreground">{m.consumable.unit}</span></TD>
                  <TD className="max-w-64 truncate text-xs text-muted-foreground" title={m.notes ?? ""}>{m.notes ?? "—"}</TD>
                  <TD>
                    <span className="flex gap-1">
                      {m.is_active ? <Badge tone="green">active</Badge> : <Badge>inactive</Badge>}
                      <Synthetic on={m.is_synthetic} />
                    </span>
                  </TD>
                  <TD className="text-right">
                    {dept != null && scope.canManageDept(dept) && (
                      <MappingDialog mapping={m} trigger={<Button size="sm" variant="ghost" aria-label="Edit mapping"><Pencil /></Button>} />
                    )}
                  </TD>
                </TR>
              );
            })}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ page */

function ProceduresInner() {
  const scope = useProcedureScope();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = params.get("tab") ?? "schedule";
  const summary = useQuery({ queryKey: ["procedures", "summary", 14], queryFn: () => get<ProcedureSummary>("/procedures/summary", { days: 14 }) });
  const types = useProcedureTypes();
  const s = summary.data;

  return (
    <>
      <PageHeader
        title="Procedures"
        description="Procedure schedule and expected item usage per procedure — the inputs to procedure-aware forecasting. Counts only, no patient data."
        actions={
          scope.canManage && (
            <>
              <ProcedureTypeDialog trigger={<Button variant="outline"><Plus /> Procedure type</Button>} />
              <MappingDialog trigger={<Button variant="outline"><Link2 /> Map item</Button>} />
              <ScheduleDialog trigger={<Button><CalendarPlus /> Schedule</Button>} />
            </>
          )
        }
      />

      {!!s?.synthetic_rows && (
        <div className="mb-6 flex items-start gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm text-violet-900" data-testid="synthetic-banner">
          <FlaskConical className="mt-0.5 size-4 shrink-0" />
          <span>
            Rows marked <b>synthetic</b> are generated demo data. Procedure volumes and kit quantities are illustrative, not real hospital
            data and not medically validated — replace them with your own schedule and kits.
          </span>
        </div>
      )}

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Scheduled, next 14 days" value={s ? formatNumber(s.total_scheduled) : "—"} sub="procedures (status SCHEDULED)" />
        <Stat label="Schedule known through" value={s?.schedule_through ? formatDate(s.schedule_through) : "—"} sub="after this, forecasts use consumption only" />
        <Stat label="Active procedure types" value={types.data ? formatNumber(types.data.length) : "—"} />
        <Stat
          label="Busiest procedure (14 d)"
          value={s?.by_type[0] ? s.by_type[0].code : "—"}
          sub={s?.by_type[0] ? `${s.by_type[0].count} × ${s.by_type[0].name}` : undefined}
        />
      </div>

      <Tabs.Root value={tab} onValueChange={(v) => router.replace(`${pathname}?tab=${v}`, { scroll: false })}>
        <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b">
          {[["schedule", "Schedule"], ["types", "Procedure types"], ["mappings", "Item mapping"]].map(([k, label]) => (
            <Tabs.Trigger
              key={k}
              value={k}
              className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800"
            >
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="schedule"><ScheduleTab /></Tabs.Content>
        <Tabs.Content value="types"><TypesTab /></Tabs.Content>
        <Tabs.Content value="mappings"><MappingsTab /></Tabs.Content>
      </Tabs.Root>
    </>
  );
}

export default function ProceduresPage() {
  return (
    <Suspense>
      <ProceduresInner />
    </Suspense>
  );
}
