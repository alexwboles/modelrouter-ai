# Agent setup: point your coding agent at the router

The router is an OpenAI-compatible endpoint at
`http://127.0.0.1:4000/v1`. Any agent that lets you set a custom base
URL and model name works the same way: set the base URL to the router,
set the model to a tier name (or `tier:effort`), and the router does
the rest. The agent's own API key setting is irrelevant for local
models and is not forwarded as a provider key — provider keys come
from the router's environment only.

## OpenCode

In `opencode.json` (or `~/.config/opencode/opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "modelrouter": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "modelrouter",
      "options": { "baseURL": "http://127.0.0.1:4000/v1" },
      "models": {
        "cascade": { "name": "cascade" },
        "cascade-high": { "name": "cascade:high" },
        "critique": { "name": "critique" }
      }
    }
  }
}
```

Then pick `modelrouter/cascade` (or `cascade-high`, `critique`) in the
model picker. If your OpenCode version configures providers
differently, the invariant is the same: base URL
`http://127.0.0.1:4000/v1`, model name = tier or `tier:effort`.

## Codex CLI

In `~/.codex/config.toml`:

```toml
model_provider = "modelrouter"
model = "cascade"

[model_providers.modelrouter]
name = "modelrouter"
base_url = "http://127.0.0.1:4000/v1"
env_key = "MODELROUTER_API_KEY"  # any placeholder value; the router ignores it
```

Start the router first (`modelrouter serve`), then run `codex` as
usual. Swap `model = "critique"` or `"cascade:high"` to change the
routing pattern without touching provider settings.

## Claude Code

Claude Code speaks the Anthropic API natively, so it needs an
OpenAI-compatible bridge in front of the router (for example
`claude-code-openai` wrappers or an Anthropic→OpenAI proxy). With such
a bridge in place, the same invariant holds: the bridge's upstream
base URL becomes `http://127.0.0.1:4000/v1` and its model name becomes
a router tier (`cascade`, `critique`, `single`). Native Anthropic
support in the router itself is a possible future addition; v0.1 is
OpenAI-compatible endpoints only, which covers OpenCode, Codex CLI,
and every other OpenAI-shaped client directly.

## Verifying the wiring

```bash
# 1. The router lists its catalog (tiers included)
curl http://127.0.0.1:4000/v1/models

# 2. A real request through the proxy
curl http://127.0.0.1:4000/v1/chat/completions -d '{
  "model": "cascade",
  "messages": [{"role": "user", "content": "say hi"}]
}'

# 3. The decision it made
tail -1 ~/.local/share/modelrouter/routing.jsonl | python3 -m json.tool
```

If step 2 returns a 502, a backend is down or a key is missing — run
`modelrouter serve` and check its startup warnings (it lists every
model whose `key_env` variable is unset).
