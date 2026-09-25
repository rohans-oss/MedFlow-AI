"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle } from "lucide-react";
import { useParams } from "next/navigation";

import { ClassificationBanner, PilotNav, usePilot } from "@/components/pilots";
import { Badge, Card, CardBody, CardHeader, Skeleton } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, put } from "@/lib/api";
import type { PilotReadiness } from "@/lib/types";

export default function PilotReadinessPage() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const { can } = useMe();
  const pilot = usePilot(pid);
  const q = useQuery({ queryKey: ["pilots", pid, "readiness"], queryFn: () => get<PilotReadiness>(`/pilots/${pid}/readiness`) });
  const confirm = useAction((v: { key: string; confirmed: boolean }) => put(`/pilots/${pid}/readiness/${v.key}`, { confirmed: v.confirmed }), {
    success: "Checklist updated", invalidate: [["pilots"]],
  });
  if (!pilot.data || !q.data) return <Skeleton className="h-96" />;
  const manage = can(PERM.PILOTS_MANAGE);
  return (
    <>
      <PilotNav pilot={pilot.data} />
      <ClassificationBanner pilot={pilot.data} />
      <p className="mb-4 text-sm text-muted-foreground" data-testid="readiness-statement">
        <strong>{q.data.done} of {q.data.total}</strong> items done. {q.data.statement}
      </p>
      <div className="grid gap-6 lg:grid-cols-2" data-testid="readiness">
        {q.data.groups.map((g) => (
          <Card key={g.group}>
            <CardHeader title={g.group} />
            <CardBody className="space-y-3">
              {g.items.map((i) => (
                <div key={i.key} className="flex gap-3 text-sm" data-testid={`ready-${i.key}`}>
                  {i.done ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
                  <div className="flex-1">
                    <div className="font-medium">{i.label} <Badge className="ml-1">{i.mode === "auto" ? "automatic" : i.mode === "manual" ? "confirmation" : "automatic + confirmation"}</Badge></div>
                    <div className="text-xs text-muted-foreground">{i.hint}</div>
                    {i.evidence && <div className="text-xs">Evidence: <span className={i.auto_ok ? "text-emerald-700" : "text-amber-700"}>{i.evidence}</span></div>}
                  </div>
                  {i.mode !== "auto" && (
                    <label className="flex items-center gap-1.5 text-xs">
                      <input type="checkbox" aria-label={`Confirm ${i.label}`} checked={i.confirmed} disabled={!manage || confirm.isPending}
                        onChange={(e) => confirm.mutate({ key: i.key, confirmed: e.target.checked })} />
                      confirmed
                    </label>
                  )}
                </div>
              ))}
            </CardBody>
          </Card>
        ))}
      </div>
    </>
  );
}
