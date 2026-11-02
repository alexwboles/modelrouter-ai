# Research notes

Brief pre-build research (Nov 2, 2026). Everything below is a pattern
description, not copied code — modelrouter-ai shares no code with any
of these projects.

## GitHub Copilot Fusion (GA Oct 28, 2026)

Sources: GitHub's Copilot Fusion docs and changelog, plus independent
write-ups of the GA announcement.

What Fusion is: an "intelligent runtime model router and orchestrator."
The user picks Fusion plus an effort level (Standard / High / Max);
per prompt, Fusion picks the models, the reasoning effort, and one of
three execution patterns. Billed per model at each model's standard
rate; no separate Fusion fee.

**Borrowed:**

- The three patterns, exactly as named: **Single** (one model completes
  the task), **Cascade** (an efficient model drafts, a judge accepts or
  escalates to a stronger model), **Critique** (one model drafts, other
  model(s) review, the original revises).
- The **effort dial**: low / medium / high in modelrouter maps to
  Fusion's Standard / High / Max idea — the user chooses effort, not
  models, and the router resolves effort to concrete models.
- "Only add model calls when a task warrants them": a straightforward
  prompt costs one model call; review calls exist only on the
  Cascade/Critique paths.

**Deliberately different:**

- Fusion's Critique runs several critics in parallel and aggregates
  feedback. modelrouter v0.1 uses a single critic (simpler, cheaper,
  easier to reason about); parallel critics are a possible later step.
- Fusion considers real-time system health and prompt-cache boundaries
  when routing. modelrouter does not (yet): routing is config + gates,
  not live health checks.
- Fusion is paid and its local-model routing is a limited preview.
  modelrouter's whole point is free + local-first: Ollama models are
  first-class backends, not a preview.

## LiteLLM / OpenRouter routing patterns

Sources: LiteLLM proxy docs and community gateway write-ups.

**Borrowed (patterns only, no code, no dependency):**

- One YAML file with a model list: each entry names the model clients
  see, the real backend (`api_base`), and the key via an environment
  variable (`os.environ/KEY` style) so the file is committable.
- Fallback chains: a failed primary automatically tries the next model.
- Per-model cost tracking (`input_cost_per_token` /
  `output_cost_per_token`) and budget enforcement (daily caps, warn vs.
  stop behavior).

**Deliberately different / refused:**

- LiteLLM is a large dependency with a database-backed control plane.
  modelrouter is stdlib-only with a JSON state file — a router, not a
  platform.
- **LiteLLM is never a dependency and none of its code is vendored.**
  Version 1.107.0 was compromised and harvested credentials; depending
  on any LiteLLM release would import that supply-chain risk. The
  patterns above are reimplemented from scratch in ~1,500 lines.
- LiteLLM routes by strategy (lowest-cost, latency, shuffle) across a
  model group. modelrouter routes by explicit tier + effort + quality
  gates instead — the operator can say exactly why a stronger model
  was called, from the routing log.

## Ollama `/v1` API

Source: Ollama's OpenAI-compatibility docs.

**Borrowed:**

- Ollama serves an OpenAI-compatible surface at
  `http://localhost:11434/v1` with `POST /v1/chat/completions`,
  `GET /v1/models`, streaming SSE, and JSON mode. The API key is
  required by SDKs but ignored for local models.
- Consequence for modelrouter: **one backend client covers everything.**
  Local Ollama, OpenAI, and any OpenAI-compatible endpoint (vLLM,
  llama.cpp server, LocalAI, OpenRouter's OpenAI surface) are all just
  "POST `{endpoint}/chat/completions` with an OpenAI-shaped body." No
  per-provider adapters, no native Ollama `/api/*` calls.

**Noted, not used:**

- Ollama's native `/api/*` surface (model management, `num_ctx`) is out
  of scope: modelrouter routes chat completions; pull and manage
  models with the `ollama` CLI.
