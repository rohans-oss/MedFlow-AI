"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Info, MessageSquarePlus } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { Badge, Button, Field, Select, Textarea } from "@/components/ui/primitives";
import { useAction } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { get, post } from "@/lib/api";
import type { ComparisonRow, MetricUnit, Pilot, PilotMetric, PilotStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

export const SECTION_TITLES: Record<string, string> = {
  data_quality: "Data quality", inventory: "Inventory", stockouts: "Stockouts", forecasting: "Forecasting",
  stockout_prediction: "Stockout prediction", supplier: "Suppliers", procurement: "Procurement", adoption: "User adoption",
};

export function fmtValue(v: number | null | undefined, unit: MetricUnit): string {
  if (v === null || v === undefined) return "—";
  if (unit === "pct") return `${(v * 100).toFixed(1)}%`;
  if (unit === "money") return `₹${Math.round(v).toLocaleString("en-IN")}`;
  if (unit === "days" || unit === "hours") return `${v.toFixed(1)} ${unit}`;
  return Number.isInteger(v) ? v.toLocaleString("en-IN") : v.toLocaleString("en-IN", { maximumFractionDigits: 1 });
}

export function fmtDiff(r: ComparisonRow): string {
  if (r.status !== "ok") return "Insufficient data";
  const parts: string[] = [];
  if (r.difference_pp !== null) parts.push(`${r.difference_pp >= 0 ? "+" : ""}${r.difference_pp.toFixed(1)} pp`);
  else if (r.difference !== null) parts.push(`${r.difference >= 0 ? "+" : ""}${Number.isInteger(r.difference) ? r.difference : r.difference.toFixed(1)}`);
  if (r.relative_change !== null) parts.push(`${r.relative_change >= 0 ? "+" : ""}${(r.relative_change * 100).toFixed(1)}% relative`);
  return parts.join(" · ") || "0";
}

export const STATUS_TONE: Record<PilotStatus, "green" | "amber" | "neutral" | "blue" | "red"> = {
  planned: "blue", active: "green", paused: "amber", completed: "neutral", cancelled: "red",
};

export function ClassificationBanner({ pilot }: { pilot: Pilot }) {
  if (pilot.data_classification === "synthetic") {
    return (
      <div className="mb-6 flex gap-3 rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm text-violet-900" data-testid="synthetic-banner">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        <p><strong>DEMO / SYNTHETIC DATA — NOT REAL HOSPITAL DATA.</strong> Every number for this pilot is a synthetic/demo result
          from the demo hospital&apos;s simulated data. No real-world outcome claim can be made from it.</p>
      </div>
    );
  }
  return (
    <div className="mb-6 flex gap-3 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm text-sky-900">
      <Info className="mt-0.5 size-4 shrink-0" />
      <p>Observed pilot results from this hospital&apos;s own data. Comparisons are descriptive: a before/after difference does not show
        that MedFlow caused it.</p>
    </div>
  );
}

export function usePilot(id: number) {
  return useQuery({ queryKey: ["pilots", id], queryFn: () => get<Pilot>(`/pilots/${id}`) });
}

export function PilotNav({ pilot }: { pilot: Pilot }) {
  const pathname = usePathname();
  const base = `/pilots/${pilot.id}`;
  const tabs = [["", "Dashboard"], ["/metrics", "Baseline vs pilot"], ["/issues", "Issues"], ["/readiness", "Readiness"], ["/report", "Report"]];
  return (
    <div className="mb-6">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Link href="/pilots" className="text-sm text-muted-foreground hover:underline">Pilots</Link>
        <span className="text-muted-foreground">/</span>
        <h1 className="text-xl font-semibold tracking-tight" data-testid="pilot-name">{pilot.name}</h1>
        <Badge tone={STATUS_TONE[pilot.status]} data-testid="pilot-status">{pilot.status}</Badge>
        {pilot.data_classification === "synthetic" ? <Badge tone="violet">synthetic data</Badge> : <Badge tone="blue">observed data</Badge>}
      </div>
      <nav className="flex gap-1 overflow-x-auto border-b">
        {tabs.map(([suffix, label]) => {
          const href = `${base}${suffix}`;
          const active = pathname === href;
          return (
            <Link key={suffix} href={href}
              className={cn("-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium",
                active ? "border-teal-700 text-teal-800" : "border-transparent text-muted-foreground hover:text-foreground")}>
              {label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

export function MetricTile({ m }: { m: PilotMetric }) {
  return (
    <div className="rounded-lg border p-3" data-testid={`metric-${m.key}`} title={`${m.formula}\nSource: ${m.source}`}>
      <div className="text-xs text-muted-foreground">{m.label}</div>
      {m.sufficient ? (
        <div className="mt-1 text-lg font-semibold tabular-nums">{fmtValue(m.value, m.unit)}</div>
      ) : (
        <div className="mt-1 text-sm font-medium text-amber-700">Insufficient data</div>
      )}
      <div className="mt-1 text-[11px] leading-snug text-muted-foreground">
        {!m.sufficient && m.n !== null && m.min_n > 0 ? `n = ${m.n} (needs ${m.min_n}). ` : ""}{m.note ?? ""}
      </div>
    </div>
  );
}

const RATINGS = [["useful", "Useful"], ["somewhat_useful", "Somewhat useful"], ["not_useful", "Not useful"]];
const REASONS = [["correct_recommendation", "Correct recommendation"], ["wrong_quantity", "Wrong quantity"], ["wrong_supplier", "Wrong supplier"],
  ["data_problem", "Data problem"], ["timing_problem", "Timing problem"], ["missing_context", "Missing context"], ["other", "Other"]];

/** Operational-learning feedback for the active pilot (not a review system). */
export function FeedbackForm({ pilotId, target = "workflow", recommendationId, onDone }: {
  pilotId: number; target?: string; recommendationId?: number; onDone?: () => void;
}) {
  const [rating, setRating] = useState("useful");
  const [targetType, setTargetType] = useState(target);
  const [reasons, setReasons] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const send = useAction(() => post(`/pilots/${pilotId}/feedback`, {
    target_type: targetType, rating, reasons, comment: comment || null, recommendation_id: recommendationId ?? null,
  }), { success: "Thanks — feedback recorded for the pilot", invalidate: [["pilots"]], onSuccess: () => { setReasons([]); setComment(""); onDone?.(); } });
  return (
    <form className="space-y-3" data-testid="feedback-form" onSubmit={(e) => { e.preventDefault(); send.mutate(undefined); }}>
      <div className="flex flex-wrap gap-2">
        {RATINGS.map(([k, l]) => (
          <button type="button" key={k} onClick={() => setRating(k)}
            className={cn("rounded-full border px-3 py-1 text-xs", rating === k ? "border-teal-700 bg-teal-50 text-teal-800" : "text-muted-foreground")}>
            {l}
          </button>
        ))}
      </div>
      {!recommendationId && (
        <Field label="About">
          <Select aria-label="Feedback about" value={targetType} onChange={(e) => setTargetType(e.target.value)}>
            {["workflow", "forecast", "stockout_risk", "supplier", "integration"].map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
          </Select>
        </Field>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {REASONS.map(([k, l]) => (
          <label key={k} className="flex items-center gap-1.5">
            <input type="checkbox" checked={reasons.includes(k)} onChange={(e) => setReasons(e.target.checked ? [...reasons, k] : reasons.filter((r) => r !== k))} />
            {l}
          </label>
        ))}
      </div>
      <Textarea aria-label="Feedback comment" className="min-h-16 text-sm" placeholder="Optional comment" value={comment} maxLength={1000}
        onChange={(e) => setComment(e.target.value)} />
      <Button size="sm" type="submit" disabled={send.isPending}><MessageSquarePlus /> Send feedback</Button>
    </form>
  );
}

/** Shown under a V5 recommendation: records that it was viewed and, during an active pilot, asks whether it was useful. */
export function RecommendationPilotHooks({ recommendationId }: { recommendationId: number }) {
  const { can } = useMe();
  useQuery({
    queryKey: ["pilot-view", recommendationId],
    queryFn: () => post(`/pilot-tracking/recommendations/${recommendationId}/view`).then(() => true),
    staleTime: Infinity, retry: false,
  });
  const active = useQuery({ queryKey: ["pilots", "active"], queryFn: () => get<Pilot | null>("/pilots/active"), staleTime: 60_000 });
  const [done, setDone] = useState(false);
  if (!active.data || !can(PERM.PILOTS_CONTRIBUTE) || active.data.status !== "active") return null;
  return (
    <div className="mt-3 rounded-md border border-dashed p-3" data-testid="rec-feedback">
      <p className="mb-2 text-xs font-medium">Pilot “{active.data.name}”: was this recommendation useful?</p>
      {done ? <p className="text-xs text-emerald-700">Feedback recorded.</p>
        : <FeedbackForm pilotId={active.data.id} target="recommendation" recommendationId={recommendationId} onDone={() => setDone(true)} />}
    </div>
  );
}
