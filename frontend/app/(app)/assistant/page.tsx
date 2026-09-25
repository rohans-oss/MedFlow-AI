"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, ChevronRight, Loader2, MessageSquarePlus, Send, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { Badge, Button, Card, CardBody, CardHeader, EmptyState, PageHeader, Skeleton, Textarea } from "@/components/ui/primitives";
import { ApiError, get, post } from "@/lib/api";
import type {
  AssistantAnswer,
  AssistantConversation,
  AssistantConversationDetail,
  AssistantReply,
  AssistantStatus,
  AssistantToolCall,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface Turn {
  key: string;
  question: string;
  answer?: AssistantAnswer | null;
  evidence?: AssistantToolCall[];
  mode?: string;
  provider?: string | null;
  model?: string | null;
  fallback_reason?: string | null;
  latency_ms?: number | null;
  pending?: boolean;
  error?: string;
}

const when = (s: string) => new Date(s).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });

function Evidence({ calls }: { calls: AssistantToolCall[] }) {
  const [open, setOpen] = useState(false);
  if (!calls.length) return <p className="text-xs text-muted-foreground">No MedFlow tool was called for this answer.</p>;
  return (
    <div data-testid="evidence">
      <button className="inline-flex items-center gap-1 text-xs font-medium text-teal-800 hover:underline" onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <Wrench className="size-3.5" /> Evidence: {calls.length} tool call{calls.length === 1 ? "" : "s"} ({calls.map((c) => c.tool).join(", ")})
      </button>
      {open && (
        <ol className="mt-2 space-y-2" data-testid="evidence-list">
          {calls.map((c, i) => (
            <li key={i} className="rounded-lg border bg-muted/40 p-3 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <code className="font-semibold">{c.tool}</code>
                <code className="text-muted-foreground">{JSON.stringify(Object.fromEntries(Object.entries(c.args).filter(([, v]) => v != null)))}</code>
                <Badge tone={c.ok ? "green" : "amber"}>{c.ok ? "ok" : "unavailable"}</Badge>
                <span className="text-muted-foreground">{c.ms} ms</span>
              </div>
              {c.error && <p className="mt-1 text-amber-800">{c.error}</p>}
              {c.facts.length > 0 && (
                <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-muted-foreground">
                  {c.facts.slice(0, 8).map((f, j) => <li key={j}>{f}</li>)}
                  {c.facts.length > 8 && <li>… {c.facts.length - 8} more</li>}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function AnswerCard({ t, onAsk }: { t: Turn; onAsk: (q: string) => void }) {
  if (t.pending) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="answer-pending">
        <Loader2 className="size-4 animate-spin" /> Querying MedFlow…
      </div>
    );
  }
  if (t.error) return <p className="text-sm text-red-700" data-testid="answer-error">{t.error}</p>;
  const a = t.answer;
  if (!a) return null;
  return (
    <Card data-testid="answer">
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h3 className="text-sm font-semibold" data-testid="answer-title">{a.title}</h3>
          <div className="flex flex-wrap gap-1.5">
            <Badge tone={t.mode === "llm" ? "violet" : "blue"} data-testid="answer-mode">
              {t.mode === "llm" ? `LLM · ${t.model ?? t.provider}` : "Deterministic planner"}
            </Badge>
            <Badge tone="green" title="Every statement comes from the tool results listed under Evidence">
              <ShieldCheck className="size-3" /> From {t.evidence?.filter((e) => e.ok).length ?? 0} tool result(s)
            </Badge>
            {t.latency_ms != null && <span className="text-xs text-muted-foreground">{t.latency_ms} ms</span>}
          </div>
        </div>
        <p className="text-sm leading-relaxed" data-testid="answer-summary">{a.summary}</p>
        {a.points.length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-sm" data-testid="answer-points">
            {a.points.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        )}
        {a.notes.length > 0 && (
          <div className="space-y-1 rounded-lg bg-amber-50 p-3 text-xs text-amber-900 ring-1 ring-amber-200" data-testid="answer-notes">
            {a.notes.map((n, i) => (
              <p key={i} className="flex gap-1.5"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{n}</p>
            ))}
          </div>
        )}
        {a.links.length > 0 && (
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="text-muted-foreground">Open:</span>
            {a.links.map((l) => (
              <Link key={l.href} href={l.href} className="font-medium text-teal-800 hover:underline">{l.label}</Link>
            ))}
          </div>
        )}
        <Evidence calls={t.evidence ?? []} />
        {a.follow_ups.length > 0 && (
          <div className="flex flex-wrap gap-2 border-t pt-3" data-testid="follow-ups">
            {a.follow_ups.map((q) => (
              <button key={q} onClick={() => onAsk(q)} className="rounded-full border bg-white px-3 py-1 text-xs hover:bg-muted">{q}</button>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function AssistantPage() {
  const qc = useQueryClient();
  const params = useSearchParams();
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const asked = useRef(false);

  const status = useQuery({ queryKey: ["assistant", "status"], queryFn: () => get<AssistantStatus>("/assistant/status") });
  const convs = useQuery({ queryKey: ["assistant", "conversations"], queryFn: () => get<AssistantConversation[]>("/assistant/conversations") });

  const ask = useMutation({
    mutationFn: (v: { question: string; conversation_id: number | null; key: string }) =>
      post<AssistantReply>("/assistant/ask", { question: v.question, conversation_id: v.conversation_id }),
    onSuccess: (r, v) => {
      setConversationId(r.conversation_id);
      setTurns((ts) => ts.map((t) => (t.key === v.key ? { ...t, pending: false, answer: r.answer, evidence: r.evidence, mode: r.mode,
        provider: r.provider, model: r.model, fallback_reason: r.fallback_reason, latency_ms: r.latency_ms } : t)));
      qc.invalidateQueries({ queryKey: ["assistant", "conversations"] });
    },
    onError: (e, v) => {
      setTurns((ts) => ts.map((t) => (t.key === v.key ? { ...t, pending: false, error: e instanceof ApiError ? e.message : String(e) } : t)));
    },
  });

  const submit = (q: string) => {
    const question = q.trim();
    if (!question || ask.isPending) return;
    const key = `${Date.now()}`;
    setTurns((ts) => [...ts, { key, question, pending: true }]);
    setText("");
    ask.mutate({ question, conversation_id: conversationId, key });
  };

  useEffect(() => {
    const q = params.get("q");
    if (q && !asked.current) {
      asked.current = true;
      submit(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const open = async (id: number) => {
    const d = await get<AssistantConversationDetail>(`/assistant/conversations/${id}`);
    const out: Turn[] = [];
    for (const m of d.messages) {
      if (m.role === "user") out.push({ key: `m${m.id}`, question: m.content });
      else if (out.length) {
        Object.assign(out[out.length - 1], { answer: m.answer, evidence: m.tool_calls ?? [], mode: m.provider === "deterministic" ? "deterministic" : "llm",
          provider: m.provider, model: m.model, fallback_reason: m.fallback_reason, latency_ms: m.latency_ms });
      }
    }
    // an LLM-provider message whose answer fell back to the planner is shown as deterministic
    for (const t of out) if (t.fallback_reason) t.mode = "deterministic";
    setConversationId(d.id);
    setTurns(out);
  };

  const reset = () => {
    setConversationId(null);
    setTurns([]);
  };

  const s = status.data;
  return (
    <div>
      <PageHeader
        title={<span className="inline-flex items-center gap-2"><Sparkles className="size-5 text-teal-700" /> AI operations assistant</span>}
        description="Ask about stock, risk, forecasts, suppliers, procurement and the knowledge graph. Every answer is looked up in MedFlow's own V1–V6 results — the assistant does not calculate, guess or change anything."
        actions={<Button variant="outline" onClick={reset} data-testid="new-conversation"><MessageSquarePlus className="size-4" /> New conversation</Button>}
      />
      <div className="grid gap-6 lg:grid-cols-[1fr_280px]">
        <div className="min-w-0 space-y-4">
          <Card>
            <CardBody className="flex flex-wrap items-center gap-2 py-3 text-xs" data-testid="assistant-status">
              {s ? (
                <>
                  <Badge tone={s.mode === "llm" ? "violet" : "blue"}>{s.mode === "llm" ? `LLM: ${s.provider} · ${s.model}` : "Deterministic planner (no LLM configured)"}</Badge>
                  <Badge tone="green"><ShieldCheck className="size-3" /> Read-only · {s.tools.length} MedFlow tools</Badge>
                  <span className="text-muted-foreground">
                    {s.mode === "llm"
                      ? `The model chooses tools (max ${s.max_steps} rounds); answers whose numbers are not in the tool results are replaced by the planner's answer.`
                      : "Questions are matched to MedFlow tools by rules; answers are composed only from what the tools return."}
                    {" "}Approvals stay with people on the Procurement page. Demo data is synthetic.
                  </span>
                </>
              ) : <Skeleton className="h-5 w-72" />}
            </CardBody>
          </Card>

          {turns.length === 0 && (
            <Card>
              <CardHeader title="Try a question" description="Click one, or type your own below." />
              <CardBody className="flex flex-wrap gap-2" data-testid="examples">
                {(s?.examples ?? []).map((q) => (
                  <button key={q} onClick={() => submit(q)} className="rounded-full border bg-white px-3 py-1.5 text-xs hover:bg-muted">{q}</button>
                ))}
              </CardBody>
            </Card>
          )}

          <div className="space-y-5" data-testid="conversation">
            {turns.map((t) => (
              <div key={t.key} className="space-y-2">
                <div className="flex justify-end">
                  <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-teal-700 px-4 py-2 text-sm text-white" data-testid="question">{t.question}</p>
                </div>
                <AnswerCard t={t} onAsk={submit} />
              </div>
            ))}
            <div ref={bottom} />
          </div>

          <form
            className="sticky bottom-0 flex gap-2 rounded-xl border bg-card p-3 shadow-sm"
            onSubmit={(e) => {
              e.preventDefault();
              submit(text);
            }}
          >
            <Textarea
              aria-label="Question"
              data-testid="question-input"
              rows={2}
              maxLength={500}
              value={text}
              placeholder={conversationId ? "Ask a follow-up — “it” refers to the item discussed above" : "e.g. Why is Suture 2-0 at risk?"}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit(text);
                }
              }}
              className="min-h-0 flex-1 resize-none"
            />
            <Button type="submit" disabled={!text.trim() || ask.isPending} data-testid="ask">
              {ask.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />} Ask
            </Button>
          </form>
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader title="Your conversations" description="Private to you; every question is also in the audit log." />
            <CardBody className="p-2" data-testid="conversation-list">
              {convs.isLoading ? <Skeleton className="h-16" /> : !convs.data?.length ? (
                <EmptyState title="No conversations yet" />
              ) : (
                <ul className="space-y-0.5">
                  {convs.data.map((c) => (
                    <li key={c.id}>
                      <button
                        onClick={() => open(c.id)}
                        className={cn("w-full rounded-md px-3 py-2 text-left text-xs hover:bg-muted", c.id === conversationId && "bg-muted font-medium")}
                      >
                        <span className="line-clamp-2">{c.title}</span>
                        <span className="text-muted-foreground">{when(c.updated_at)} · {Math.floor(c.message_count / 2)} question(s)</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
          {s && (
            <Card>
              <CardHeader title="What it can answer" />
              <CardBody>
                <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                  {s.capabilities.map((c) => <li key={c}>{c}</li>)}
                </ul>
              </CardBody>
            </Card>
          )}
        </aside>
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-40" />}>
      <AssistantPage />
    </Suspense>
  );
}
