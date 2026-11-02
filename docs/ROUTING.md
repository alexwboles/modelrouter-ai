# Routing: tier semantics

## Selecting a tier and effort

Priority order for each request:

1. `modelrouter_tier` / `modelrouter_effort` body fields.
2. The `model` field: a tier name (`cascade`), a `tier:effort` pair
   (`cascade:high`), or a configured model id (Single to that model).
3. `policies.default_tier` / `policies.default_effort` from the config.

Effort (`low` / `medium` / `high`) overrides individual tier roles in
the config. Example: at `low` effort the cascade's strong model can be
the same local model as the draft (escalation becomes a retry, not a
spend); at `high` effort the draft itself is a capable API model. The
pattern never changes with effort — only which models fill the roles.

## Single

One call, straight to the configured model (or to the model id the
client named). If the backend fails, the error is returned; Single does
not fall back. Streaming passes through chunk-by-chunk.

Example: `{"model": "single", "messages": [...]}`.

## Cascade

```
draft ── gates pass ──► answer
  │
  └─ gates fail, or draft errors ──► strong model ──► answer
```

**Gates** (all configurable via the tier's `min_length` and
`refusal_patterns`; the list itself is in `modelrouter/quality.py`):

| Gate | Trigger |
| --- | --- |
| `empty_output` | Draft is empty or whitespace-only |
| `too_short` | Fewer than `min_length` characters (default 40) |
| `refusal` | Matches a refusal pattern (`i can't`, `i cannot`, `as an ai …`, …) |
| `missing_code_block` | User asked for code, draft has no fenced code block |

Failing gates are joined with `+` (e.g. `refusal+missing_code_block`)
and logged as the escalation reason, so the routing log answers "why
did the expensive model get called?" An upstream error escalates as
`upstream_error:<status>` (or `upstream_error:unreachable`).

The strong model's answer is returned whatever its gates say — it is
the last resort, not another draft. If the strong model also fails, the
client gets HTTP 502 and the billed draft attempt is still logged and
costed.

**Streaming:** Cascade buffers internally and emits the final answer
as one SSE chunk + `[DONE]`. Incremental tokens are impossible here:
the router cannot know whether to escalate until the draft is complete.

Example: `{"model": "cascade:medium", "messages": [...]}`.

## Critique

```
primary drafts ──► critic scores 0–10 ──► >= threshold: answer
                         │
                         └─ below threshold: primary revises
                            (max_rounds, default 2) ──► best attempt
```

- The critic is prompted to reply with JSON only:
  `{"score": <0-10>, "notes": "..."}`. If its output is not parseable
  JSON, the draft is accepted and the log records
  `critic_unparseable` — a broken critic must not loop forever.
- If the critic backend errors, the current draft is returned and the
  log records `critic_error`.
- If a revision call errors, the best attempt so far is returned.
- "Best" means highest critic score; ties keep the earlier attempt.
- After `max_rounds` revisions without reaching the threshold, the
  best attempt is returned and the log records
  `below_threshold_after_<n>_rounds`.

Critique costs one primary + one critic call per round, plus one
primary call per revision. At threshold 7 and max 2 rounds, the worst
case is 3 primary + 3 critic calls — visible in the routing log and
the cost report before you ever wonder where the money went.

**Streaming:** same buffered behavior as Cascade.

Example: `{"model": "critique", "messages": [...]}`.

## The routing log

One JSONL line per request in `<data_dir>/routing.jsonl`:

```json
{"ts": "2026-11-02T20:00:00Z", "request_id": "…",
 "prompt_hash": "sha256-of-messages", "tier": "cascade",
 "effort": "medium", "project": "demo",
 "models_tried": ["qwen3-local", "gpt-cheap"],
 "escalation_reason": "missing_code_block",
 "attempts": [{"model": "qwen3-local", "prompt_tokens": 120,
               "completion_tokens": 80, "cost_usd": 0.0}],
 "cost_usd": 0.0002, "duration_ms": 812, "outcome": "ok"}
```

The prompt hash lets you correlate a log line with a request you
already hold. The prompt text itself is never in the log.
