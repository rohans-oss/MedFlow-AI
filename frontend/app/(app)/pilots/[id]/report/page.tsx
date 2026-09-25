"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, Save } from "lucide-react";
import { useParams } from "next/navigation";

import { ClassificationBanner, fmtDiff, fmtValue, PilotNav, usePilot } from "@/components/pilots";
import { Badge, Button, Card, CardBody, CardHeader, Skeleton, Table, TD, TH, THead, TR } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, post } from "@/lib/api";
import type { PilotReport, ReportSnapshot } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

export default function PilotReportPage() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const { can } = useMe();
  const pilot = usePilot(pid);
  const q = useQuery({ queryKey: ["pilots", pid, "report"], queryFn: () => get<PilotReport>(`/pilots/${pid}/report`) });
  const snaps = useQuery({ queryKey: ["pilots", pid, "snapshots"], queryFn: () => get<ReportSnapshot[]>(`/pilots/${pid}/report/snapshots`) });
  const save = useAction(() => post(`/pilots/${pid}/report/snapshots`), { success: "Report snapshot saved", invalidate: [["pilots"]] });
  if (!pilot.data) return <Skeleton className="h-96" />;
  const r = q.data;
  return (
    <>
      <PilotNav pilot={pilot.data} />
      <ClassificationBanner pilot={pilot.data} />
      <div className="mb-4 flex flex-wrap gap-2">
        <a href={`/api/pilots/${pid}/report?format=markdown`} download>
          <Button variant="outline" size="sm"><Download /> Download Markdown</Button>
        </a>
        {can(PERM.PILOTS_MANAGE) && <Button size="sm" onClick={() => save.mutate(undefined)} disabled={save.isPending} data-testid="save-snapshot"><Save /> Save snapshot</Button>}
      </div>
      {!r ? <Skeleton className="h-96" /> : (
        <div className="space-y-6" data-testid="pilot-report">
          <Card>
            <CardHeader title={r.title} description={`Generated ${formatDateTime(r.generated_at)} · ${r.comparison_label}`}
              action={<Badge tone={r.classification === "synthetic" ? "violet" : "blue"} data-testid="result-label">{r.result_label}</Badge>} />
            {r.banner && <CardBody className="font-semibold text-violet-800" data-testid="report-banner">{r.banner}</CardBody>}
          </Card>
          {r.sections.map((s) => (
            <Card key={s.key}>
              <CardHeader title={s.title} />
              {s.rows.length > 0 && (
                <Table>
                  <THead><tr><TH>Metric</TH><TH className="text-right">Baseline</TH><TH className="text-right">Pilot</TH><TH className="text-right">Difference</TH></tr></THead>
                  <tbody>
                    {s.rows.map((row) => (
                      <TR key={row.key}>
                        <TD>{row.label}{row.normalized && <Badge className="ml-1">{row.normalized}</Badge>}</TD>
                        <TD className="text-right tabular-nums">{row.baseline === null ? "Insufficient data" : fmtValue(row.baseline, row.unit)}</TD>
                        <TD className="text-right tabular-nums">{row.pilot === null ? "Insufficient data" : fmtValue(row.pilot, row.unit)}</TD>
                        <TD className="text-right tabular-nums">{fmtDiff(row)}</TD>
                      </TR>
                    ))}
                  </tbody>
                </Table>
              )}
            </Card>
          ))}
          <Card>
            <CardHeader title="9. Issues encountered" />
            <CardBody>
              {r.issues.length === 0 ? <p className="text-sm text-muted-foreground">No issues were logged.</p> : (
                <ul className="space-y-1 text-sm">{r.issues.map((i) => <li key={i.id}><Badge>{i.status}</Badge> {i.title} — {i.category.replaceAll("_", " ")}, {i.severity}{i.resolution ? `. Resolution: ${i.resolution}` : ""}</li>)}</ul>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="10. Limitations" />
            <CardBody><ul className="list-disc space-y-1 pl-5 text-sm" data-testid="limitations">{r.limitations.map((x) => <li key={x}>{x}</li>)}</ul></CardBody>
          </Card>
          <Card>
            <CardHeader title="11. Conclusion" description="Only what the measured data show." />
            <CardBody><ul className="list-disc space-y-1 pl-5 text-sm" data-testid="conclusion">{r.conclusion.map((x) => <li key={x}>{x}</li>)}</ul></CardBody>
          </Card>
          <Card>
            <CardHeader title="Saved snapshots" description="Immutable copies of the report as generated." />
            <CardBody>
              {snaps.data?.length ? (
                <ul className="space-y-1 text-sm" data-testid="snapshots">
                  {snaps.data.map((s) => <li key={s.id}><a className="text-teal-800 hover:underline" href={`/api/pilot-reports/${s.id}`} target="_blank" rel="noreferrer">
                    Report #{s.id}</a> — {formatDateTime(s.generated_at)} by {s.generated_by?.name ?? "—"} ({s.data_classification})</li>)}
                </ul>
              ) : <p className="text-sm text-muted-foreground">None yet.</p>}
            </CardBody>
          </Card>
        </div>
      )}
    </>
  );
}
