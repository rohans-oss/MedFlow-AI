import { AlertCircle, AlertTriangle, CheckCircle2, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/primitives";
import type { AlertStatus, MovementType, RiskLevel, Severity, StockStatus } from "@/lib/types";

const STOCK: Record<StockStatus, { label: string; tone: "red" | "amber" | "green" | "blue" }> = {
  OUT_OF_STOCK: { label: "Out of stock", tone: "red" },
  LOW: { label: "Low", tone: "amber" },
  OK: { label: "Healthy", tone: "green" },
  OVERSTOCK: { label: "Overstock", tone: "blue" },
};

export function StockStatusBadge({ status }: { status: StockStatus }) {
  const s = STOCK[status];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

const SEV: Record<Severity, "red" | "orange" | "amber" | "neutral"> = {
  CRITICAL: "red",
  HIGH: "orange",
  MEDIUM: "amber",
  LOW: "neutral",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={SEV[severity]}>{severity.charAt(0) + severity.slice(1).toLowerCase()}</Badge>;
}

const MOVE: Record<MovementType, { label: string; tone: "green" | "blue" | "violet" | "red" | "amber" }> = {
  RECEIPT: { label: "Receipt", tone: "green" },
  ISSUE: { label: "Issue", tone: "blue" },
  RETURN: { label: "Return", tone: "violet" },
  WASTAGE: { label: "Wastage", tone: "red" },
  ADJUSTMENT: { label: "Adjustment", tone: "amber" },
};

export function MovementBadge({ type }: { type: MovementType }) {
  const m = MOVE[type];
  return <Badge tone={m.tone}>{m.label}</Badge>;
}

export function AlertStatusBadge({ status }: { status: AlertStatus }) {
  const tone = status === "OPEN" ? "red" : status === "ACKNOWLEDGED" ? "amber" : "green";
  return <Badge tone={tone}>{status.charAt(0) + status.slice(1).toLowerCase()}</Badge>;
}

export const ALERT_TYPE_LABEL: Record<string, string> = {
  OUT_OF_STOCK: "Out of stock",
  LOW_STOCK: "Low stock",
  EXPIRED: "Expired batch",
  EXPIRING_SOON: "Expiring soon",
  STOCKOUT_RISK: "Stockout risk",
  SUPPLIER_DELAY: "Supplier delay",
};

function daysUntil(date: string) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((new Date(`${date}T00:00:00`).getTime() - today.getTime()) / 86_400_000);
}

export function ExpiryText({ date, days }: { date: string | null; days?: number | null }) {
  if (!date) return <span className="text-muted-foreground">No expiry</span>;
  const d = days ?? daysUntil(date);
  const cls = d < 0 ? "text-red-600 font-medium" : d <= 30 ? "text-orange-600 font-medium" : d <= 60 ? "text-amber-700" : "";
  const txt = new Date(`${date}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  return (
    <span className={cls}>
      {txt}
      <span className="ml-1 text-xs text-muted-foreground">({d < 0 ? `expired ${-d}d ago` : `${d}d`})</span>
    </span>
  );
}

/** V3 risk level: status colour + icon + text (never colour alone). */
export function RiskBadge({ level, out }: { level: RiskLevel; out?: boolean }) {
  if (out) return <Badge tone="red"><XCircle className="size-3" /> Out of stock</Badge>;
  if (level === "HIGH") return <Badge tone="red"><AlertTriangle className="size-3" /> High</Badge>;
  if (level === "MEDIUM") return <Badge tone="amber"><AlertCircle className="size-3" /> Medium</Badge>;
  return <Badge tone="green"><CheckCircle2 className="size-3" /> Low</Badge>;
}

/** V4 reliability grade (A–D) with the score; "limited evidence" when fewer than 5 decided orders. */
export function GradeBadge({ score, grade, limited }: { score: number | null; grade: string | null; limited?: boolean }) {
  if (score == null || grade == null) return <Badge>No orders</Badge>;
  const tone = grade === "A" ? "green" : grade === "B" ? "blue" : grade === "C" ? "amber" : "red";
  return (
    <Badge tone={tone} title={limited ? "Limited evidence: fewer than 5 decided orders" : undefined}>
      {grade} · {score.toFixed(0)}{limited ? " *" : ""}
    </Badge>
  );
}

/** V4: can this supplier deliver before the projected stockout, based on its own history? */
export function VerdictBadge({ verdict }: { verdict: "likely" | "uncertain" | "unlikely" | "no_evidence" | "no_deadline" }) {
  if (verdict === "likely") return <Badge tone="green"><CheckCircle2 className="size-3" /> Likely in time</Badge>;
  if (verdict === "uncertain") return <Badge tone="amber"><AlertCircle className="size-3" /> Uncertain</Badge>;
  if (verdict === "unlikely") return <Badge tone="red"><XCircle className="size-3" /> Unlikely in time</Badge>;
  if (verdict === "no_evidence") return <Badge title="No comparable past orders">No history</Badge>;
  return <Badge>No deadline</Badge>;
}
