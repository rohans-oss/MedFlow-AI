"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ClassificationBanner, fmtDiff, fmtValue, PilotNav, SECTION_TITLES, usePilot } from "@/components/pilots";
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, Select, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, post } from "@/lib/api";
import type { PilotComparison } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const FACTORS = ["seasonal_demand", "department_change", "supplier_change", "data_source_change", "inventory_policy_change", "staffing_change", "other"];

function FactorForm({ pid }: { pid: number }) {
  const [f, setF] = useState({ factor: "seasonal_demand", note: "", period: "pilot" });
  const add = useAction(() => post(`/pilots/${pid}/external-factors`, f), {
    success: "External factor recorded", invalidate: [["pilots"]], onSuccess: () => setF({ ...f, note: "" }),
  });
  return (
    <form className="grid items-end gap-3 sm:grid-cols-[200px_1fr_140px_auto]" onSubmit={(e) => { e.preventDefault(); add.mutate(undefined); }}>
      <Field label="Factor"><Select value={f.factor} onChange={(e) => setF({ ...f, factor: e.target.value })}>{FACTORS.map((x) => <option key={x} value={x}>{x.replaceAll("_", " ")}</option>)}</Select></Field>
      <Field label="Note"><Input aria-label="Factor note" required minLength={3} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
      <Field label="Period"><Select value={f.period} onChange={(e) => setF({ ...f, period: e.target.value })}>{["baseline", "pilot", "both"].map((x) => <option key={x}>{x}</option>)}</Select></Field>
      <Button type="submit" size="sm" disabled={add.isPending}><Plus /> Add</Button>
    </form>
  );
}

export default function PilotMetricsPage() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const { can } = useMe();
  const pilot = usePilot(pid);
  const q = useQuery({ queryKey: ["pilots", pid, "comparison"], queryFn: () => get<PilotComparison>(`/pilots/${pid}/comparison`) });
  if (!pilot.data) return <Skeleton className="h-96" />;
  const sections = Object.keys(SECTION_TITLES);
  return (
    <>
      <PilotNav pilot={pilot.data} />
      <ClassificationBanner pilot={pilot.data} />
      {!q.data ? <Skeleton className="h-96" /> : (
        <div className="space-y-6">
          <Card>
            <CardHeader title={q.data.label} description={q.data.classification_label} />
            <CardBody className="space-y-2 text-sm">
              <p data-testid="causality">{q.data.causality_statement}</p>
              <p className="text-muted-foreground">
                Baseline {formatDate(q.data.baseline.start)} → {formatDate(q.data.baseline.end)} ({q.data.baseline.days} days) ·
                Pilot {formatDate(q.data.pilot.start)} → {formatDate(q.data.pilot.end)} ({q.data.pilot.days} days).
                Percentages: difference in percentage points (pp) and relative change. “Insufficient data” = below the minimum sample.
              </p>
              {q.data.notes.map((n) => <p key={n} className="text-xs text-amber-800">{n}</p>)}
            </CardBody>
          </Card>
          <Card>
            <Table data-testid="comparison-table">
              <THead><tr><TH>Metric</TH><TH className="text-right">Baseline</TH><TH className="text-right">Pilot</TH><TH className="text-right">Difference</TH><TH>Samples</TH></tr></THead>
              <tbody>
                {sections.map((s) => {
                  const rows = q.data!.rows.filter((r) => r.section === s);
                  if (!rows.length) return null;
                  return [
                    <tr key={s} className="bg-muted/50"><td colSpan={5} className="px-4 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{SECTION_TITLES[s]}</td></tr>,
                    ...rows.map((r) => (
                      <TR key={r.key} data-testid={`row-${r.key}`}>
                        <TD>{r.label}{r.normalized && <Badge className="ml-1">{r.normalized}</Badge>}</TD>
                        <TD className="text-right tabular-nums">{r.baseline === null ? <span className="text-amber-700">Insufficient data</span> : fmtValue(r.baseline, r.unit)}</TD>
                        <TD className="text-right tabular-nums">{r.pilot === null ? <span className="text-amber-700">Insufficient data</span> : fmtValue(r.pilot, r.unit)}</TD>
                        <TD className="text-right tabular-nums">{r.status === "ok" ? fmtDiff(r) : <span className="text-amber-700">Insufficient data</span>}</TD>
                        <TD className="text-xs text-muted-foreground">{r.baseline_n ?? "—"} / {r.pilot_n ?? "—"}{r.min_n ? ` (min ${r.min_n})` : ""}</TD>
                      </TR>
                    )),
                  ];
                })}
              </tbody>
            </Table>
          </Card>
          <Card>
            <CardHeader title="External factors" description="Things that changed between the periods besides MedFlow — recorded so readers can judge the comparison." />
            <CardBody className="space-y-3">
              {q.data.external_factors.length === 0 ? <p className="text-sm text-muted-foreground">None recorded.</p> : (
                <ul className="space-y-1 text-sm" data-testid="factors">
                  {q.data.external_factors.map((f, i) => <li key={i}><Badge>{f.factor.replaceAll("_", " ")}</Badge> <span className="text-muted-foreground">({f.period})</span> {f.note} — {f.recorded_by}</li>)}
                </ul>
              )}
              {can(PERM.PILOTS_MANAGE) && <FactorForm pid={pid} />}
            </CardBody>
          </Card>
        </div>
      )}
    </>
  );
}
