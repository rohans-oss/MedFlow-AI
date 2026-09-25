"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { Code2, Loader2, Play, RefreshCw } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { RiskBadge } from "@/components/badges";
import { Chain, GraphView } from "@/components/graph-view";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Field,
  PageHeader,
  Select,
  Skeleton,
  Stat,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/primitives";
import { useAction, useConsumables, useSuppliers } from "@/hooks/use-lookups";
import { PERM, useMe } from "@/hooks/use-me";
import { ApiError, get, post } from "@/lib/api";
import type {
  GraphExplain,
  GraphQueryDef,
  GraphQueryResult,
  GraphResult,
  GraphSchema,
  GraphStatus,
  GraphSyncRun,
  ItemImpact,
  SupplierImpact,
} from "@/lib/types";
import { formatDate, formatNumber } from "@/lib/utils";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);
const when = (s: string | null | undefined) => (s ? new Date(s).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—");

function Unavailable({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? error.message : String(error);
  return (
    <Card data-testid="graph-unavailable">
      <EmptyState title={error instanceof ApiError && error.status === 503 ? "Knowledge graph unavailable" : "Could not load"} description={msg} />
    </Card>
  );
}

function Synced({ r }: { r: GraphResult<unknown> }) {
  return <p className="text-xs text-muted-foreground">Read from the graph projection synced {when(r.synced_at)} (run #{r.sync_run_id}) · PostgreSQL is the source of truth.</p>;
}

function CypherBox({ cypher }: { cypher: Record<string, string> | string }) {
  const [open, setOpen] = useState(false);
  const entries = typeof cypher === "string" ? [["query", cypher]] : Object.entries(cypher);
  return (
    <div>
      <button className="inline-flex items-center gap-1 text-xs font-medium text-teal-800 hover:underline" onClick={() => setOpen((o) => !o)}>
        <Code2 className="size-3.5" /> {open ? "Hide" : "Show"} Cypher
      </button>
      {open && (
        <div className="mt-2 space-y-2" data-testid="cypher">
          {entries.map(([k, v]) => (
            <pre key={k} className="overflow-x-auto rounded-lg bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100"><span className="text-slate-400">{`// ${k}\n`}</span>{v}</pre>
          ))}
        </div>
      )}
    </div>
  );
}

function ItemSelect({ value, onChange, id = "kg_item" }: { value: number | null; onChange: (v: number) => void; id?: string }) {
  const items = useConsumables();
  return (
    <Select id={id} className="w-80" value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))}>
      <option value="">Select an item…</option>
      {items.data?.filter((c) => c.is_active).map((c) => <option key={c.id} value={c.id}>{c.name} ({c.sku})</option>)}
    </Select>
  );
}

function SupplierSelect({ value, onChange, id = "kg_sup" }: { value: number | null; onChange: (v: number) => void; id?: string }) {
  const sups = useSuppliers();
  return (
    <Select id={id} className="w-72" value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))}>
      <option value="">Select a supplier…</option>
      {sups.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
    </Select>
  );
}

/* ------------------------------------------------------------------ V6.3 explain */

function ExplainTab({ item, setItem }: { item: number | null; setItem: (id: number) => void }) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["graph", "explain", item],
    queryFn: () => get<GraphResult<GraphExplain>>(`/graph/items/${item}/explain`),
    enabled: item != null,
    retry: false,
  });
  return (
    <div className="space-y-6">
      <Field label="Why is this item at risk?" htmlFor="kg_item"><ItemSelect value={item} onChange={setItem} /></Field>
      {item == null ? <Card><EmptyState title="Pick an item to see its explanation chain" description="Item → stockout risk → forecast → scheduled procedures → departments → suppliers and their delivery history → orders in transit → procurement option." /></Card>
        : isLoading ? <Skeleton className="h-96" />
        : error ? <Unavailable error={error} />
        : data ? (
          <>
            <Card>
              <CardHeader title={data.data.item.name} description={data.data.summary} />
              <CardBody className="overflow-x-auto"><GraphView nodes={data.data.graph.nodes} edges={data.data.graph.edges} /></CardBody>
            </Card>
            <Card data-testid="explain-chain">
              <CardHeader title="Explanation chain" description="Each step is one hop in the graph; every sentence is built from the numbers on the nodes and relationships." />
              <CardBody>
                <ol className="relative space-y-4 border-l-2 border-slate-200 pl-6">
                  {data.data.steps.map((s) => (
                    <li key={s.kind} className="relative">
                      <span className="absolute -left-[31px] top-1 size-3 rounded-full border-2 border-white bg-teal-700" aria-hidden />
                      <div className="flex flex-wrap items-center gap-2">
                        <h4 className="text-sm font-semibold">{s.title}</h4>
                        {s.via && <Badge className="font-mono text-[10px]">{s.via}</Badge>}
                      </div>
                      <ul className="mt-1 space-y-0.5 text-sm text-slate-700">{s.lines.map((l) => <li key={l}>{l}</li>)}</ul>
                    </li>
                  ))}
                </ol>
              </CardBody>
            </Card>
            <div className="flex flex-wrap items-center justify-between gap-3"><Synced r={data} /><CypherBox cypher={data.data.cypher} /></div>
          </>
        ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ V6.4 impact */

const SEVERITY: Record<string, "red" | "orange" | "amber" | "neutral"> = { critical: "red", high: "orange", watch: "amber", low: "neutral" };

function SupplierImpactView({ id }: { id: number }) {
  const { data, error, isLoading } = useQuery({ queryKey: ["graph", "impact", "supplier", id], queryFn: () => get<GraphResult<SupplierImpact>>(`/graph/impact/suppliers/${id}`), retry: false });
  if (isLoading) return <Skeleton className="h-96" />;
  if (error) return <Unavailable error={error} />;
  if (!data) return null;
  const d = data.data;
  const sole = d.items.filter((i) => i.sole_source).length;
  return (
    <div className="space-y-6" data-testid="supplier-impact">
      <div className="grid gap-4 sm:grid-cols-4">
        <Stat label="Items losing a supplier" value={d.items.length} />
        <Stat label="No alternative supplier" value={sole} tone={sole ? "red" : "green"} />
        <Stat label="Procedures exposed (sole-source items)" value={d.procedures.filter((p) => p.sole_source_items > 0).length} />
        <Stat label="Open orders that would not arrive" value={d.open_orders.length} tone={d.open_orders.length ? "amber" : "default"} />
      </div>
      <Card>
        <CardHeader title={`If ${d.supplier.name} becomes unavailable`} description="Traversed: Supplier → SUPPLIES → Item ← USES_ITEM ← Procedure ← PERFORMS ← Department; Item → HAS_RISK" />
        <CardBody className="space-y-3">
          <ul className="list-disc space-y-1 pl-5 text-sm" data-testid="impact-summary">{d.summary.map((s) => <li key={s}>{s}</li>)}</ul>
          <div className="space-y-2">{d.chains.map((c, i) => <Chain key={i} links={c} />)}</div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Affected items" description="Severity: critical = no alternative and already at risk · high = no alternative · watch = at risk, alternative exists" />
        <Table data-testid="impact-items">
          <THead><tr><TH>Item</TH><TH>Severity</TH><TH>Alternatives</TH><TH>Risk</TH><TH className="text-right">Usable</TH><TH className="text-right">Days left</TH></tr></THead>
          <tbody>
            {d.items.map((i) => (
              <TR key={i.id}>
                <TD><div className="font-medium">{i.item}</div><div className="text-xs text-muted-foreground">{i.sku}{i.preferred ? " · preferred supplier" : ""}</div></TD>
                <TD><Badge tone={SEVERITY[i.severity]}>{i.severity}</Badge></TD>
                <TD className="text-sm">{i.sole_source ? <span className="font-medium text-red-700">none — sole source</span> : i.alternatives.join(", ")}</TD>
                <TD>{i.risk_level ? <RiskBadge level={i.risk_level} out={i.usable_stock <= 0} /> : "—"} <span className="text-xs text-muted-foreground">{pct(i.risk_probability)}</span></TD>
                <TD className="text-right tabular-nums">{formatNumber(i.usable_stock)}</TD>
                <TD className="text-right tabular-nums">{i.days_left ?? "—"}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Procedures that use these items" />
          {!d.procedures.length ? <EmptyState title="No mapped procedure uses these items" /> : (
            <Table>
              <THead><tr><TH>Procedure</TH><TH className="text-right">Scheduled 14 d</TH><TH>Items from this supplier</TH></tr></THead>
              <tbody>
                {d.procedures.map((p) => (
                  <TR key={p.id}>
                    <TD><div className="font-medium">{p.procedure}</div><div className="text-xs text-muted-foreground">{p.department}</div></TD>
                    <TD className="text-right tabular-nums">{p.scheduled_next_14}</TD>
                    <TD className="text-xs">{p.items.map((x) => <span key={x.sku} className={x.sole_source ? "font-semibold text-red-700" : ""}>{x.item}{x.sole_source ? " (sole source)" : ""}; </span>)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <Card>
          <CardHeader title="Departments using these items" description="Units issued in the last 90 days" />
          {!d.departments.length ? <EmptyState title="No recorded use" /> : (
            <Table>
              <THead><tr><TH>Department</TH><TH className="text-right">Items</TH><TH className="text-right">Units (90 d)</TH></tr></THead>
              <tbody>{d.departments.map((x) => <TR key={x.id}><TD>{x.department}</TD><TD className="text-right tabular-nums">{x.items}</TD><TD className="text-right tabular-nums">{formatNumber(x.units_90)}</TD></TR>)}</tbody>
            </Table>
          )}
        </Card>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3"><Synced r={data} /><CypherBox cypher={d.cypher} /></div>
    </div>
  );
}

function ItemImpactView({ id }: { id: number }) {
  const { data, error, isLoading } = useQuery({ queryKey: ["graph", "impact", "item", id], queryFn: () => get<GraphResult<ItemImpact>>(`/graph/impact/items/${id}`), retry: false });
  if (isLoading) return <Skeleton className="h-96" />;
  if (error) return <Unavailable error={error} />;
  if (!data) return null;
  const d = data.data;
  return (
    <div className="space-y-6" data-testid="item-impact">
      <Card>
        <CardHeader title={`If ${d.item.name} runs short`} description="Traversed: Item ← USES_ITEM ← Procedure ← PERFORMS ← Department; Item ← USES ← Department; Item ← SUPPLIES ← Supplier" />
        <CardBody><ul className="list-disc space-y-1 pl-5 text-sm" data-testid="impact-summary">{d.summary.map((s) => <li key={s}>{s}</li>)}</ul></CardBody>
      </Card>
      <Card>
        <CardHeader title="Procedures that depend on it" description="Kit quantity per procedure, scheduled count and how many of them current usable stock alone could cover" />
        {!d.procedures.length ? <EmptyState title="No mapped procedure uses this item" /> : (
          <Table data-testid="impact-procedures">
            <THead><tr><TH>Procedure</TH><TH className="text-right">Per procedure</TH><TH className="text-right">Scheduled 14 d</TH><TH className="text-right">Units 14 d</TH><TH className="text-right">Covered by stock</TH><TH className="text-right">Kit items</TH></tr></THead>
            <tbody>
              {d.procedures.map((p) => (
                <TR key={p.id}>
                  <TD><div className="font-medium">{p.procedure}</div><div className="text-xs text-muted-foreground">{p.department}</div></TD>
                  <TD className="text-right tabular-nums">{formatNumber(p.per_procedure)} {d.item.unit}</TD>
                  <TD className="text-right tabular-nums">{p.scheduled_next_14}</TD>
                  <TD className="text-right tabular-nums">{formatNumber(p.units_next_14)}</TD>
                  <TD className="text-right tabular-nums">{p.procedures_covered_by_stock ?? "—"}</TD>
                  <TD className="text-right tabular-nums">{p.kit_items}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Departments that depend on it" />
          <Table>
            <THead><tr><TH>Department</TH><TH className="text-right">Units (90 d)</TH><TH className="text-right">Share</TH><TH>Via procedures</TH></tr></THead>
            <tbody>{d.departments.map((x) => <TR key={x.id}><TD>{x.department}</TD><TD className="text-right tabular-nums">{x.units_90 == null ? "—" : formatNumber(x.units_90)}</TD><TD className="text-right tabular-nums">{pct(x.share)}</TD><TD className="text-xs">{x.procedures.join(", ") || "—"}</TD></TR>)}</tbody>
          </Table>
        </Card>
        <Card>
          <CardHeader title="Suppliers" />
          <Table>
            <THead><tr><TH>Supplier</TH><TH className="text-right">Price</TH><TH className="text-right">In window</TH></tr></THead>
            <tbody>{d.suppliers.map((x) => <TR key={x.code}><TD>{x.supplier}{!x.active && <Badge className="ml-1">inactive</Badge>}</TD><TD className="text-right tabular-nums">₹{x.unit_price.toFixed(2)}</TD><TD className="text-right tabular-nums">{x.window_n ? `${x.window_k}/${x.window_n}` : "—"}</TD></TR>)}</tbody>
          </Table>
        </Card>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3"><Synced r={data} /><CypherBox cypher={d.cypher} /></div>
    </div>
  );
}

function ImpactTab({ params, set }: { params: URLSearchParams; set: (n: Record<string, string | null>) => void }) {
  const mode = params.get("mode") ?? "supplier";
  const supplier = Number(params.get("supplier")) || null;
  const item = Number(params.get("item")) || null;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Impact of" htmlFor="kg_mode">
          <Select id="kg_mode" className="w-56" value={mode} onChange={(e) => set({ mode: e.target.value })}>
            <option value="supplier">A supplier becoming unavailable</option>
            <option value="item">An item running short</option>
          </Select>
        </Field>
        {mode === "supplier"
          ? <Field label="Supplier" htmlFor="kg_sup"><SupplierSelect value={supplier} onChange={(v) => set({ supplier: String(v) })} /></Field>
          : <Field label="Item" htmlFor="kg_item_impact"><ItemSelect id="kg_item_impact" value={item} onChange={(v) => set({ item: String(v) })} /></Field>}
      </div>
      {mode === "supplier" ? (supplier ? <SupplierImpactView id={supplier} /> : <Card><EmptyState title="Pick a supplier" description="See which items, procedures, departments, risks, orders and recommendations depend on it." /></Card>)
        : (item ? <ItemImpactView id={item} /> : <Card><EmptyState title="Pick an item" description="See which procedures and departments depend on it." /></Card>)}
    </div>
  );
}

/* ------------------------------------------------------------------ V6.5 search */

function SearchTab() {
  const { data: catalog } = useQuery({ queryKey: ["graph", "queries"], queryFn: () => get<GraphQueryDef[]>("/graph/queries") });
  const [name, setName] = useState("");
  const [item, setItem] = useState<number | null>(null);
  const [supplier, setSupplier] = useState<number | null>(null);
  const [result, setResult] = useState<GraphResult<GraphQueryResult> | null>(null);
  const q = catalog?.find((x) => x.name === name);
  const run = useAction(() => post<GraphResult<GraphQueryResult>>(`/graph/queries/${name}`, { item_id: item, supplier_id: supplier }), { onSuccess: setResult });
  const cell = (v: unknown) => (v == null ? "—" : Array.isArray(v) ? v.join(", ") : typeof v === "number" ? (Number.isInteger(v) ? formatNumber(v) : v.toFixed(3)) : typeof v === "boolean" ? (v ? "yes" : "no") : String(v));
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Ask the graph" description="Predefined, parameterised questions — each runs one Cypher query against the projection (no free text, no AI)." />
        <CardBody>
          <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
            <Field label="Question" htmlFor="kg_q">
              <Select id="kg_q" className="w-[26rem]" value={name} onChange={(e) => { setName(e.target.value); setResult(null); }}>
                <option value="">Select a question…</option>
                {catalog?.map((x) => <option key={x.name} value={x.name}>{x.title}</option>)}
              </Select>
            </Field>
            {q?.params.includes("item") && <Field label="Item" htmlFor="kg_q_item"><ItemSelect id="kg_q_item" value={item} onChange={setItem} /></Field>}
            {q?.params.includes("supplier") && <Field label="Supplier" htmlFor="kg_q_sup"><SupplierSelect id="kg_q_sup" value={supplier} onChange={setSupplier} /></Field>}
            <Button type="submit" disabled={!q || run.isPending}>{run.isPending ? <Loader2 className="animate-spin" /> : <Play />} Run</Button>
          </form>
        </CardBody>
      </Card>
      {result && (
        <Card data-testid="query-result">
          <CardHeader title={result.data.title} description={`${result.data.rows.length} row(s)`} action={<CypherBox cypher={result.data.cypher} />} />
          {!result.data.rows.length ? <EmptyState title="No results" /> : (
            <Table>
              <THead><tr>{result.data.columns.map((c) => <TH key={c}>{c.replaceAll("_", " ")}</TH>)}</tr></THead>
              <tbody>{result.data.rows.map((r, i) => <TR key={i}>{result.data.columns.map((c) => <TD key={c} className="text-sm">{cell(r[c])}</TD>)}</TR>)}</tbody>
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ V6.2 sync & schema */

function RunCounts({ run }: { run: GraphSyncRun }) {
  const labels = Object.keys(run.node_counts ?? {});
  return (
    <Table data-testid="sync-counts">
      <THead><tr><TH>Node label</TH><TH className="text-right">PostgreSQL projection</TH><TH className="text-right">Found in graph</TH><TH /></tr></THead>
      <tbody>
        {labels.map((l) => {
          const a = run.node_counts?.[l] ?? 0;
          const b = run.graph_node_counts?.[l];
          return <TR key={l}><TD>{l}</TD><TD className="text-right tabular-nums">{formatNumber(a)}</TD><TD className="text-right tabular-nums">{b == null ? "—" : formatNumber(b)}</TD><TD>{b === a ? <Badge tone="green">match</Badge> : <Badge tone="red">differs</Badge>}</TD></TR>;
        })}
      </tbody>
    </Table>
  );
}

function SyncTab() {
  const { can } = useMe();
  const status = useQuery({ queryKey: ["graph", "status"], queryFn: () => get<GraphStatus>("/graph/status") });
  const schema = useQuery({ queryKey: ["graph", "schema"], queryFn: () => get<GraphResult<GraphSchema>>("/graph/schema"), retry: false, enabled: !!status.data?.available });
  const sync = useAction(() => post<GraphSyncRun>("/graph/sync"), { invalidate: [["graph"]], success: (r) => `Graph synced — ${formatNumber(Object.values(r.graph_node_counts ?? {}).reduce((a, b) => a + b, 0))} nodes, verified ${r.verified ? "✓" : "✗"}` });
  const s = status.data;
  if (!s) return <Skeleton className="h-96" />;
  const last = s.last_success;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-4">
        <Stat label="Graph store" value={<span data-testid="graph-backend">{s.backend}</span>} sub={s.url} />
        <Stat label="Status" value={s.available ? "Available" : "Unavailable"} tone={s.available ? "green" : "red"} sub={s.error ?? "reachable"} />
        <Stat label="In sync with PostgreSQL" value={s.current == null ? "—" : s.current ? "Yes" : "Behind"} tone={s.current ? "green" : s.current === false ? "amber" : "default"}
          sub={s.current === false ? `changed: ${s.changed.join(", ")} (re-synced on the next graph read)` : last ? `last sync ${when(last.finished_at)}` : "never synced"} />
        <Stat label="Nodes · relationships" value={last ? `${formatNumber(Object.values(last.graph_node_counts ?? {}).reduce((a, b) => a + b, 0))} · ${formatNumber(Object.values(last.graph_edge_counts ?? {}).reduce((a, b) => a + b, 0))}` : "—"} sub={last ? `${last.duration_ms} ms · ${last.verified ? "counts verified" : "not verified"}` : undefined} />
      </div>
      <Card>
        <CardHeader title="PostgreSQL → graph synchronisation" description={s.note + " Changes are detected with a data fingerprint; graph reads re-project automatically. If the graph store is down, only this page is affected."}
          action={can(PERM.GRAPH_SYNC) && <Button onClick={() => sync.mutate()} disabled={sync.isPending || !s.available}>{sync.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Sync now</Button>} />
        {last ? <RunCounts run={last} /> : <EmptyState title="No successful sync yet" />}
      </Card>
      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <Card>
          <CardHeader title="Graph schema" description="Relationship types and what they mean (counts from the graph)" />
          {schema.error ? <Unavailable error={schema.error} /> : !schema.data ? <Skeleton className="m-5 h-48" /> : (
            <Table data-testid="graph-schema">
              <THead><tr><TH>Relationship</TH><TH>Meaning</TH><TH className="text-right">Count</TH></tr></THead>
              <tbody>{schema.data.data.relationships.map((r) => <TR key={`${r.from}-${r.type}-${r.to}`}><TD className="whitespace-nowrap font-mono text-xs">({r.from})-[:{r.type}]→({r.to})</TD><TD className="text-xs text-muted-foreground">{r.meaning}</TD><TD className="text-right tabular-nums">{formatNumber(r.count)}</TD></TR>)}</tbody>
            </Table>
          )}
        </Card>
        <Card>
          <CardHeader title="Recent sync runs" />
          <Table>
            <THead><tr><TH>Run</TH><TH>When</TH><TH>Trigger</TH><TH>Result</TH></tr></THead>
            <tbody>{s.runs.map((r) => <TR key={r.id}><TD>#{r.id}</TD><TD className="whitespace-nowrap text-xs">{when(r.started_at)}</TD><TD className="text-xs">{r.trigger}</TD><TD><Badge tone={r.status === "SUCCESS" ? "green" : r.status === "FAILED" ? "red" : "amber"}>{r.status.toLowerCase()}</Badge>{r.error && <div className="mt-1 max-w-56 truncate text-xs text-muted-foreground" title={r.error}>{r.error}</div>}</TD></TR>)}</tbody>
          </Table>
        </Card>
      </div>
      <p className="text-xs text-muted-foreground">Data as of {formatDate(new Date().toISOString())}. Synthetic demo data; counts only — no patient data exists in MedFlow or its graph.</p>
    </div>
  );
}

/* ------------------------------------------------------------------ page */

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = params.get("tab") ?? "explain";
  const item = Number(params.get("item")) || null;
  const set = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params.toString());
    Object.entries(next).forEach(([k, v]) => (v == null ? p.delete(k) : p.set(k, v)));
    router.replace(`${pathname}?${p.toString()}`, { scroll: false });
  };
  return (
    <>
      <PageHeader
        title="Knowledge graph"
        description="How items, procedures, departments, suppliers, forecasts, risks, orders and procurement decisions connect — an operational graph projected from MedFlow's PostgreSQL data (not a biomedical graph; no AI)."
      />
      <Tabs.Root value={tab} onValueChange={(v) => set({ tab: v })}>
        <Tabs.List className="mb-6 flex gap-1 overflow-x-auto border-b">
          {[["explain", "Explain a risk"], ["impact", "Impact analysis"], ["search", "Graph search"], ["sync", "Sync & schema"]].map(([k, label]) => (
            <Tabs.Trigger key={k} value={k}
              className="-mb-px whitespace-nowrap border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground data-[state=active]:border-teal-700 data-[state=active]:text-teal-800">
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="explain"><ExplainTab item={item} setItem={(id) => set({ item: String(id) })} /></Tabs.Content>
        <Tabs.Content value="impact"><ImpactTab params={params} set={set} /></Tabs.Content>
        <Tabs.Content value="search"><SearchTab /></Tabs.Content>
        <Tabs.Content value="sync"><SyncTab /></Tabs.Content>
      </Tabs.Root>
    </>
  );
}

export default function KnowledgeGraphPage() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
