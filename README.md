# modelrouter-ai

**A free, self-hosted, Fusion-style model router for open coding agents.**

Point a coding agent at modelrouter instead of at one model, and the
router picks which model actually answers — across local models
(Ollama-compatible `/v1` endpoints) and any OpenAI-compatible API. "Which
model for this task" becomes infrastructure instead of a paid IDE
feature.

GitHub Copilot Fusion (GA Oct 28, 2026) productized exactly this idea —
an effort dial plus Single / Cascade / Critique execution patterns —
but it is a paid feature, and its local-model routing is still a limited
preview. modelrouter-ai is the open version: MIT-licensed, zero
third-party dependencies (Python standard library only), no hosted
component, no paid services. It routes to models and keys you already
have; it never hosts, sells, or phones home anything.

## How it works

```
coding agent (OpenCode, Codex CLI, Claude Code, ...)
        │  base_url = http://127.0.0.1:4000/v1
        ▼
modelrouter ── Single:    one model answers directly
            ── Cascade:   cheap model drafts; a quality gate accepts it
            │              or escalates to a stronger model
            └── Critique:  primary drafts; a critic scores 0–10; revise
                           while below threshold (max 2 rounds)
```

Every decision is written to a JSONL routing log (prompt hash, never
prompt text), every token is priced from your own editable price table,
and a daily budget warns at 80% and stops at 100%.

## Quickstart

```bash
# 1. Copy the example config and edit the model list
cp config.example.yaml modelrouter.yaml

# 2. Put API keys in the environment — never in the config file
export OPENAI_API_KEY="..."

# 3. Start the proxy (Ollama, if you use local models, on :11434)
python -m modelrouter serve
# modelrouter listening on http://127.0.0.1:4000
# OpenAI-compatible base URL: http://127.0.0.1:4000/v1

# 4. Dry-run a routing decision (no backend called)
python -m modelrouter route --explain "write a function that adds two numbers" \
  --tier cascade --effort high

# 5. See what was spent
python -m modelrouter costs
python -m modelrouter costs --html > costs.html   # styled report, same data
```

Or install it: `pip install .` then use the `modelrouter` command.

## Dashboard

While the proxy runs, open http://127.0.0.1:4000/ for a live
dashboard: today's requests, tokens, and spend; the budget bar with
warn/stop markers; every configured model with its tier roles; the
last routing decisions (model, tier, escalation reason, cost — never
prompt text, never keys); and a copy-paste agent-setup snippet. See
`docs/UI-AUDIT.md` for the competitor research behind it.

## One full example

Config (`modelrouter.yaml`): a free local model drafts, a cheap API
model is the strong fallback, a strong model critiques.

```yaml
models:
  - id: qwen3-local
    endpoint: http://localhost:11434/v1
    key_env: ""
    input_price: 0.0
    output_price: 0.0
  - id: gpt-cheap
    endpoint: https://api.openai.com/v1
    key_env: OPENAI_API_KEY
    input_price: 0.15
    output_price: 0.60
  - id: gpt-strong
    endpoint: https://api.openai.com/v1
    key_env: OPENAI_API_KEY
    input_price: 2.00
    output_price: 8.00
tiers:
  cascade: {draft: qwen3-local, strong: gpt-cheap, min_length: 40}
  critique: {primary: gpt-cheap, critic: gpt-strong, threshold: 7, max_rounds: 2}
```

A client request:

```bash
curl http://127.0.0.1:4000/v1/chat/completions -d '{
  "model": "cascade:high",
  "messages": [{"role": "user", "content": "write a function that adds two numbers"}]
}'
```

The local model drafts; if the draft is empty, too short, a refusal,
or missing a code block when code was requested, the router escalates
to `gpt-cheap` and logs exactly why. The response carries a
`modelrouter` block with the tier, effort, models tried, and escalation
reason, plus summed token usage.

## Choosing a model, tier, and effort in requests

| `model` field | Meaning |
| --- | --- |
| `cascade` | Cascade tier at the default effort |
| `cascade:high` | Cascade tier at high effort |
| `qwen3-local` | A configured model id: Single tier to exactly that model |
| (absent) | `policies.default_tier` at `policies.default_effort` |

Body fields `modelrouter_tier`, `modelrouter_effort`, and
`modelrouter_project` override the same things (and are stripped before
forwarding). Effort levels `low` / `medium` / `high` override tier
roles in the config without changing the pattern.

## Streaming

`stream: true` on the Single tier passes the backend's SSE stream
through chunk-by-chunk. Cascade and Critique must see the complete
draft before they can judge it, so they buffer internally and then emit
the final answer as one SSE chunk followed by `[DONE]` — a valid
stream, just without incremental tokens. See `docs/ROUTING.md`.

## Security notes

- API keys come only from environment variables named by `key_env`.
  They are never written to config files, logs, or error output
  (redaction is tested).
- The routing log stores a SHA-256 hash of the messages, never the
  prompt text.
- The router binds `127.0.0.1` by default. Expose it beyond localhost
  only behind auth you control — anyone who can reach it can spend
  your configured API keys.

## Docs

- `docs/RESEARCH.md` — what was borrowed from Copilot Fusion, LiteLLM,
  and Ollama (and what was deliberately not copied).
- `docs/ROUTING.md` — tier semantics, gates, and examples.
- `docs/COSTS.md` — price table, budgets, and reports.
- `docs/AGENT-SETUP.md` — pointing OpenCode, Codex CLI, and Claude
  Code at the proxy.

## Future interop (not built)

HQ's Ollama → OpenAI fallback brain could one day point at this router
instead of at Ollama directly. There is no code coupling to HQ today;
this is documented as a possibility only.

## License

MIT. See `LICENSE`.
