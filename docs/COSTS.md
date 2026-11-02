# Costs: price table, budgets, reports

## The price table is your config

Each model entry carries `input_price` and `output_price` in USD per
1M tokens. Local models are `0.0`. These numbers are the entire pricing
model: the router multiplies reported token usage by them, nothing
else. If a provider changes prices, edit the config — no code changes,
no redeploy of anything else.

```yaml
- id: gpt-cheap
  endpoint: https://api.openai.com/v1
  key_env: OPENAI_API_KEY
  input_price: 0.15
  output_price: 0.60
```

Per-call cost:

```
cost = prompt_tokens / 1,000,000 * input_price
     + completion_tokens / 1,000,000 * output_price
```

Every attempt in a Cascade escalation or Critique round is a real
billed call and is costed individually; the response's `usage` sums
all attempts, so the number your agent sees matches the number in the
cost report. If a backend omits `usage`, that call costs $0.00 in the
report (and the routing log shows the zero token counts — visible,
not silently estimated).

## Budgets

Under `policies.budget`:

```yaml
budget:
  daily_usd: 5.0       # omit or set null for no cap
  warn_fraction: 0.8   # warn at $4.00
  stop_fraction: 1.0   # refuse at $5.00
```

- **Warn (80%):** the request still runs. The routing log records
  `budget_warning: true` and the response's `modelrouter` block reports
  `budget_status: "warn"`.
- **Stop (100%):** the request is refused with HTTP 429 *before any
  backend is called*, and the refusal is logged with outcome
  `budget_blocked`. A request landing exactly on the cap is allowed;
  only spending beyond it is blocked.
- The day boundary is local time; totals reset automatically each day.

## Reports

```bash
modelrouter costs            # Markdown: by day, by model, by project
modelrouter costs --json     # machine-readable totals
modelrouter costs --days 7   # limit the by-day table
```

State lives in `<data_dir>/costs.json` (default
`~/.local/share/modelrouter/`), so reports survive restarts. The
routing log (`routing.jsonl` in the same directory) is the audit trail
behind the totals.

## Per-project breakdown

Tag requests with a project to split spend across codebases or
clients — either the `modelrouter_project` body field or the
`X-ModelRouter-Project` HTTP header. Untagged requests appear in the
day and model tables only.
