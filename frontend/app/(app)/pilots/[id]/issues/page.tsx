"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ClassificationBanner, PilotNav, usePilot } from "@/components/pilots";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, Select, Skeleton, Table, TD, TH, THead, TR, Textarea } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, patch, post } from "@/lib/api";
import type { PilotIssue } from "@/lib/types";
import { timeAgo } from "@/lib/utils";

const CATEGORIES = ["inventory_mismatch", "unknown_item", "unknown_supplier", "incorrect_mapping", "duplicate_record", "missing_data",
  "incorrect_quantity", "integration_failure", "prediction_problem", "recommendation_problem", "user_workflow_problem", "other"];
const SEV_TONE = { low: "neutral", medium: "blue", high: "amber", critical: "red" } as const;
const ST_TONE = { open: "red", investigating: "amber", resolved: "green", ignored: "neutral" } as const;

function NewIssue({ pid }: { pid: number }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ category: "inventory_mismatch", severity: "medium", title: "", description: "", impact: "", source: "manual", source_ref: "" });
  const create = useAction(() => post(`/pilots/${pid}/issues`, { ...f, description: f.description || null, impact: f.impact || null, source_ref: f.source_ref || null }), {
    success: "Issue logged", invalidate: [["pilots"]], onSuccess: () => { setOpen(false); setF({ ...f, title: "", description: "", impact: "", source_ref: "" }); },
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button size="sm"><Plus /> Log issue</Button></DialogTrigger>
      <DialogContent title="Log a pilot issue" description="Issues never change inventory or orders — people investigate and resolve them.">
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
          <Field label="Title" className="sm:col-span-2"><Input aria-label="Issue title" required minLength={3} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label="Category"><Select aria-label="Issue category" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATEGORIES.map((c) => <option key={c} value={c}>{c.replaceAll("_", " ")}</option>)}</Select></Field>
          <Field label="Severity"><Select aria-label="Severity" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>{["low", "medium", "high", "critical"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Source"><Select value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}>{["manual", "sync_run", "reconciliation", "feedback"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Reference" hint="e.g. sync_run:12 or reconciliation:3"><Input value={f.source_ref} onChange={(e) => setF({ ...f, source_ref: e.target.value })} /></Field>
          <Field label="Description" className="sm:col-span-2"><Textarea className="min-h-16" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
          <Field label="Impact" className="sm:col-span-2"><Input value={f.impact} onChange={(e) => setF({ ...f, impact: e.target.value })} /></Field>
          <DialogFooter><Button type="submit" disabled={create.isPending}>Log issue</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function IssueActions({ issue }: { issue: PilotIssue }) {
  const [resolution, setResolution] = useState("");
  const [open, setOpen] = useState(false);
  const upd = useAction((body: Record<string, unknown>) => patch(`/pilot-issues/${issue.id}`, body), {
    success: "Issue updated", invalidate: [["pilots"]], onSuccess: () => setOpen(false),
  });
  if (issue.status === "resolved" || issue.status === "ignored") {
    return <Button size="sm" variant="ghost" onClick={() => upd.mutate({ status: "open" })}>Reopen</Button>;
  }
  return (
    <div className="flex gap-1">
      {issue.status === "open" && <Button size="sm" variant="outline" onClick={() => upd.mutate({ status: "investigating" })}>Investigate</Button>}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild><Button size="sm" variant="outline">Resolve</Button></DialogTrigger>
        <DialogContent title={`Resolve: ${issue.title}`} description="A resolution note is required. Nothing in inventory changes automatically.">
          <Textarea aria-label="Resolution" className="min-h-20" value={resolution} onChange={(e) => setResolution(e.target.value)} />
          <DialogFooter>
            <Button variant="ghost" onClick={() => upd.mutate({ status: "ignored", resolution })} disabled={resolution.trim().length < 3}>Ignore</Button>
            <Button onClick={() => upd.mutate({ status: "resolved", resolution })} disabled={resolution.trim().length < 3}>Mark resolved</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function PilotIssuesPage() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const { can } = useMe();
  const pilot = usePilot(pid);
  const q = useQuery({ queryKey: ["pilots", pid, "issues"], queryFn: () => get<PilotIssue[]>(`/pilots/${pid}/issues`) });
  if (!pilot.data) return <Skeleton className="h-96" />;
  const contribute = can(PERM.PILOTS_CONTRIBUTE);
  return (
    <>
      <PilotNav pilot={pilot.data} />
      <ClassificationBanner pilot={pilot.data} />
      <Card>
        <CardHeader title="Pilot issues & discrepancies" description="Data, integration, prediction, recommendation and workflow problems found during the pilot."
          action={contribute ? <NewIssue pid={pid} /> : undefined} />
        {!q.data ? <Skeleton className="m-4 h-24" /> : q.data.length === 0 ? <EmptyState title="No issues logged" /> : (
          <Table data-testid="issues-table">
            <THead><tr><TH>Issue</TH><TH>Category</TH><TH>Severity</TH><TH>Status</TH><TH>Assigned</TH><TH>Logged</TH><TH>Resolution</TH><TH /></tr></THead>
            <tbody>
              {q.data.map((i) => (
                <TR key={i.id}>
                  <TD className="max-w-64"><div className="font-medium">{i.title}</div>{i.impact && <div className="text-xs text-muted-foreground">Impact: {i.impact}</div>}
                    {i.source_ref && <div className="font-mono text-[11px] text-muted-foreground">{i.source_ref}</div>}</TD>
                  <TD className="text-xs">{i.category.replaceAll("_", " ")}</TD>
                  <TD><Badge tone={SEV_TONE[i.severity]}>{i.severity}</Badge></TD>
                  <TD><Badge tone={ST_TONE[i.status]} data-testid="issue-status">{i.status}</Badge></TD>
                  <TD className="text-xs">{i.assigned_to?.name ?? "—"}</TD>
                  <TD className="text-xs">{timeAgo(i.created_at)} · {i.created_by?.name ?? "—"}</TD>
                  <TD className="max-w-56 text-xs">{i.resolution ?? "—"}{i.resolved_by && ` — ${i.resolved_by.name}`}</TD>
                  <TD>{contribute && <IssueActions issue={i} />}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
