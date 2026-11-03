# UI audit — modelrouter-ai (2026-11-02)

Competitor research done BEFORE any redesign, per Alex's standing rule.
Sources are docs and public write-ups, not copied screenshots or code.

## What modelrouter's surfaces lacked

- `GET /` was a bare status page: system font, one plain table, an
  uptime line and a spent-today line. No sense of health, no budget
  story, no routing story, no reason for a new operator to trust it.
- Cost reports were Markdown/JSON only: accurate, but nothing you
  could glance at or show anyone.
- Nothing explained the product's core idea — Single / Cascade /
  Critique — anywhere a human looks.

## Competitor patterns worth borrowing

### LiteLLM Admin UI (`/ui`)
1. **Spend next to budget, per row.** Every list view (teams, keys,
   models) shows current spend beside the budget — the operator never
   has to open a detail view to answer "are we over?"
2. **Request logs as the audit surface.** Each entry shows model,
   key alias, tokens, cost — a scannable ledger, not a dump.
3. **Budget enforcement visible in the product.** Hitting a cap
   returns a clear error; the UI makes the cap a first-class object.
4. **Left-nav product frame.** Keys / Teams / Models / Logs reads as
   a real product, not a debug page.
5. *What NOT to borrow:* the UI itself is described even by friendly
   reviewers as "functional rather than pretty" — that is our opening.

### OpenRouter dashboard
1. **Primary meter: cap vs balance, labeled honestly.** OpenRouter
   distinguishes "spending cap, not balance" — a cap meter and a
   balance are different numbers and the UI says so.
2. **Spend notes at three horizons.** Daily, weekly, monthly spend
   shown together so a spike has context.
3. **Spend chart over time.** A day/week/month chart makes trends
   visible that a table hides.
4. **Per-model activity breakdown.** The last 30 days split by model,
   with requests, tokens, and cost per model.
5. *What NOT to borrow:* OpenRouter's current-day data lags (activity
   excludes the current UTC day). Our dashboard shows today live,
   from our own log.

### Portkey (hosted console; gateway OSS)
1. **Analytics as a first-class dashboard.** 40+ metrics, cost /
   latency / error views, metadata-scoped views (per user, per env).
2. **Failover trace.** The observability view shows every attempt —
   which providers were tried, why each failed, which one succeeded.
   This is the closest existing pattern to our cascade/critique story:
   the chain of attempts IS the product.
3. **Threshold alerts.** Slack on daily spend over $X, email on p95
   latency over Y — budgets as events, not just numbers.
4. **Logs with full detail per request.** Timestamp, model, tokens,
   cost, status — filterable, sortable.
5. *What NOT to borrow:* Portkey's console is proprietary and the
   hosted product is now inside Palo Alto's Prisma AIRS — the
   self-hosted story is ours to own.

### Langfuse
1. **KPI strip first.** Overview leads with total traces,
   generations, scores, errors — the health of the system in one row.
2. **Cost dashboard: cost over time, by model.** Charts first,
   tables second.
3. **Traces table.** Name, timestamp, latency, tokens, cost, tags —
   sortable, filterable, and each row drills into a full trace.
4. **Latency percentiles.** p50/p95 views, not just averages.
5. *What NOT to borrow:* Langfuse shows full prompts in traces. We
   deliberately do not — our log stores a hash, never prompt text —
   and the dashboard must preserve that privacy guarantee.

## What we borrowed (mapped to the redesign)

| Pattern | Where it lands in modelrouter |
|---|---|
| KPI strip (Langfuse, LiteLLM) | Dashboard header row: requests / tokens / spent today / budget status |
| Cap meter, honestly labeled (OpenRouter) | Budget bar: spent vs daily cap, warn/stop markers, "no cap configured" state |
| Spend chart over time (OpenRouter, Langfuse) | HTML cost report: daily spend bars (plain divs, no JS libs) |
| Per-model + per-project breakdown (all four) | Dashboard models table; cost report sections |
| Request ledger (LiteLLM, Portkey, Langfuse) | Recent routing decisions table: time, tier, models tried, escalation reason, tokens, cost, outcome |
| Failover trace (Portkey) | Decisions show the full attempt chain (draft → strong) and the escalation reason in plain language |
| Budget as events (Portkey alerts) | Budget status pill (ok / warn / stop) on the dashboard and in the report |
| Setup snippet (gateway onboarding) | Copy-paste base URL + curl on the dashboard |

## Visual identity: "The Switchboard Ledger"

Distinctive, not a copy of any one competitor. A control-room ledger:
paper-white background, ink text, one signal-green accent for healthy
states, amber for warn, red for stop. No gradients, no rounded
corners, no emoji. Serif display headings (Georgia stack — no webfont
downloads, the page must work offline and self-hosted), system sans
for body, monospace for model ids, costs, and code. Hairline rules,
generous spacing, and one recurring motif: the tier flow
(draft → judge → strong) drawn as a sharp-edged step diagram, which
is the product's core idea made visible.

## Privacy rules the redesign must not break

- NEVER display prompt text. The routing log stores a SHA-256 hash,
  never the text; the dashboard shows neither.
- NEVER display API keys. Model rows show "key set" / "local" only.
- The dashboard reads the JSONL log tail and renders only safe
  fields: timestamp, request id, tier, effort, models tried,
  escalation reason, tokens, cost, outcome, duration.
