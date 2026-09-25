# V7 — AI operations assistant

> **Principle:** the assistant does not make operational calculations. It queries the results V1–V6 already produce and
> explains them. It never invents a number, and it never changes anything.

```text
question ─► write request?  ── yes ─► refused (read-only), nothing runs
              │ no
              ├─ LLM configured? ── yes ─► model chooses MedFlow tools (≤ ASSISTANT_MAX_STEPS rounds)
              │                               └─► structured JSON answer ─► grounding check ─┬─ pass ─► LLM answer
              │                                                                             └─ fail ─► planner answer (+ reason)
              └─ no (default) ─► deterministic planner: intent + entity resolution ─► tools ─► answer composed from tool facts
                                                                     │
      every answer ─► assistant_messages (tool calls, provider, model, grounding, fallback reason) + audit_logs row
```

Code: `backend/app/assistant/` (`tools.py`, `planner.py`, `llm.py`, `engine.py`), API `app/api/assistant.py`, page
`frontend/app/(app)/assistant/`. Tests: `backend/tests/test_assistant.py`, `frontend/e2e/v7-assistant.spec.ts`.

## Tools — the only way the assistant gets data

Every tool calls an **existing MedFlow endpoint function** (`risk.item_risk`, `supplier_intel.item_options`,
`procurement.item_plan`, `graph.explain`, …) with the caller's user and database session, so it sees exactly what the
corresponding page shows, with the same hospital scoping and the same 404 for another hospital's ids. Each tool returns
`data` (structured) and `facts` (sentences built from that data, mostly the explanation sentences the page already shows).

| Tool | Source (version) | Answers |
|---|---|---|
| `find_item`, `find_supplier` | PostgreSQL (V1) | name / SKU / code → id (this hospital only) |
| `item_status` | stockout risk (V3) + forecast (V2) | stock, probability, stockout date, shortage, reasons |
| `top_risks` | V3 overview | items most likely to run out (optionally within N days) |
| `item_forecast` | V2 forecast | demand, range, procedure-driven demand and SHAP drivers |
| `supplier_options` | V4 | which suppliers delivered within the window before (k of n), orders in transit |
| `supplier_performance`, `supplier_delays` | V4 scorecards | OTIF score, lateness, lead time, overdue orders |
| `procurement_plan`, `compare_with_cheapest` | V5 plan | recommended option, scenarios, cheapest-price and no-order comparison |
| `procurement_what_if` | V5 what-if | same scenarios re-simulated with a supplier delay / demand change |
| `pending_recommendations`, `needs_attention` | V5 | approval queue, items needing a decision |
| `graph_explain`, `supplier_impact`, `item_impact`, `graph_query` | V6 graph | explanation chain, impact analysis, 5 predefined queries |
| `active_alerts` | V1–V4 alerts | active alerts, optionally one type |
| `daily_review` | composition of the above | high risks, decisions due, approvals, supplier delays, expiring stock |
| `glossary` | curated from `docs/` | definitions (OTIF, safety stock, cost model, WAPE…) |

**There is no write tool.** Approving, modifying or rejecting a recommendation, recording or cancelling an order and
receiving or issuing stock stay on their pages, behind their permissions (`procurement:approve`, …). A question that
starts with an imperative write verb ("approve…", "place an order…", "cancel…") is refused before any tool or model runs.

## Deterministic planner (default — no LLM, no API keys)

`planner.plan()` resolves entities (item name/SKU by token match weighted by rarity; supplier code exact match, e.g.
"CPS"; "it / this item" from the conversation's memory) and picks an intent with ordered patterns (most specific first).
Ambiguous names return a "Which item?" answer listing the candidates; unknown names say the item wasn't found (another
hospital's items are never candidates). `planner.compose()` builds the answer only from the tools' `facts`.

Example questions (all answered on the demo; numbers come from the pages, e.g. suture 2-0: MEDIUM, 17 %, runs out 27 Sep):

| Question | Intent → tools |
|---|---|
| Why is Suture 2-0 at risk? | `why_risk` → `item_status`, `graph_explain` |
| Which suppliers can cover it within 4 days? | `supplier_options` (item from memory) |
| What happens if CPS becomes unavailable? | `supplier_impact` |
| Which procedures are affected if this item runs out? | `item_impact` |
| Why did the optimizer choose CPS instead of OPI? | `why_recommendation` → `compare_with_cheapest` |
| Compare the current recommendation with the cheapest option | `compare` → `compare_with_cheapest` |
| Show me all high-risk single-source items | `graph_query(risky_single_source)` |
| What should I review today? | `daily_review` |
| Which supplier has the most delays? | `supplier_delays` |
| Which supplies are most likely to run out next week? | `top_risks(within_days=7)` |
| Which procedures are driving demand for suture 2-0? | `item_forecast`, `item_impact` |
| What happens if CPS takes 2 additional days? | `what_if_supplier` → V5 what-if on each pending recommendation using CPS |

Limits of the planner: it understands the phrasings above and close variants, not arbitrary language. Unsupported
questions get a list of what it can answer instead of a guess.

## Optional LLM mode (configured only through the environment)

| Variable | Meaning |
|---|---|
| `ASSISTANT_PROVIDER` | `none` (default) · `openai_compatible` · `anthropic` |
| `ASSISTANT_BASE_URL` | for `openai_compatible`, e.g. `http://localhost:11434/v1` (Ollama), a llama.cpp or vLLM server |
| `ASSISTANT_MODEL` | model name at that provider |
| `ASSISTANT_API_KEY` | only if the provider needs one — **environment / secret store only, never committed** |
| `ASSISTANT_TIMEOUT_SECONDS`, `ASSISTANT_MAX_STEPS`, `ASSISTANT_RATE_LIMIT_PER_MINUTE` | 30 s, 6 tool rounds, 20 questions per user per minute |

A local model (`openai_compatible` against Ollama / llama.cpp / vLLM) keeps all data on your machine and needs no key.
With a hosted provider, tool results (item names, stock, supplier names, prices — **no patient data**, MedFlow holds none)
are sent to that provider; decide that per your hospital's policy.

Guardrails around any model (`llm.py`):

1. **Only registered read-only tools.** Unknown tool names, unexpected / missing / wrongly-typed / out-of-range arguments
   are rejected with an error message back to the model; nothing executes.
2. **Step limit.** At most `ASSISTANT_MAX_STEPS` tool rounds.
3. **Structured output.** The final answer must be JSON `{summary, points, follow_ups}`.
4. **At least one tool call.** An answer without MedFlow data is rejected.
5. **Grounding check.** Every number in the answer must appear in this turn's tool results or the question (a
   probability may be written as a percentage, and values may be rounded). Otherwise the answer is discarded.
6. **Tool results are data.** The system prompt tells the model to ignore instructions inside tool results.
7. **Write requests never reach the model.**

When any check fails (or the provider errors or times out), the deterministic planner answers instead, and the answer
carries a note ("The language model's answer was not used (numbers not found in tool results: 777) …"). The reason is
stored and audited. The grounding check is a strong filter against invented numbers, not a proof of correct wording: a
model could still misattribute a real number. That is why the evidence (every tool call with its arguments and facts)
is shown under each answer.

**Honest status:** no real LLM was executed while building V7. The development environment could not download a
model or reach a model server (Hugging Face, the Ollama installer and GitHub release downloads were blocked). The LLM loop
is tested with a scripted provider (tool execution, rejected tools/arguments, step limit, malformed output, grounding
failure, provider error), and the OpenAI-compatible and Anthropic adapters are tested against mocked HTTP responses.
Answer quality with a real model has not been evaluated.

## Conversations, memory and audit

- `assistant_conversations` (hospital, user, title, `context` = the item / supplier being discussed) and
  `assistant_messages` (question; answer JSON; tool calls with arguments, facts, errors and timings; provider, model,
  intent, grounded, fallback reason, latency). Migration `181a85264e64`.
- Conversations are **private to their user** — another user, even in the same hospital, gets 404.
- Every question also writes an `audit_logs` row `assistant.ask` (question, intent, mode, provider, tools, grounding,
  fallback reason, refused).
- Reading data can refresh derived snapshots exactly as opening the pages does (today's V3 risk, V6 graph re-sync); it
  never creates orders, recommendations or stock movements (tested).

## API

| Method | Path | |
|---|---|---|
| GET | `/api/assistant/status` | provider / mode, model, tools, example questions |
| POST | `/api/assistant/ask` | `{question (1–500 chars), conversation_id?}` → answer + evidence (429 above the rate limit) |
| GET | `/api/assistant/conversations` | the caller's conversations |
| GET | `/api/assistant/conversations/{id}` | one of them with all messages (404 otherwise) |

All need the `read` permission (every role).

## V8 — hospital context

The assistant always works in the **active hospital** of the signed-in account (server-verified). Its tools have no
hospital parameter; a model that adds one is rejected by argument validation. Questions that refer to another hospital
("Show me Hospital B's inventory", another hospital's name or code, "across hospitals") are refused before any tool or
model runs, with the same reply whether or not the hospital exists. Conversations are user + hospital scoped. See
`docs/multi-hospital.md`.

## Known limitations

- Numbers are the pages' numbers, including their different windows. Example: the forecast page counts every
  non-cancelled procedure in its window, including today's completed ones (suture 2-0: 103 procedures, 231 foil); the
  graph counts only procedures still scheduled in the next 14 days (94, 211). The answer shows both with a note.
- The planner is pattern-based; paraphrases far from the examples fall back to "what I can answer".
- In-memory rate limit (per API process); use a shared store if you run several API workers.
- Demo data is synthetic; nothing here has been evaluated on a real hospital.
- Graph-backed answers need the graph store. When it is down they say so, and the other tools still answer.
