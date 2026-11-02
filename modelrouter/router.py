"""Routing engine: Single / Cascade / Critique.

How a request picks a tier (in priority order):

1. ``modelrouter_tier`` body field (stripped before forwarding).
2. The ``model`` field: a tier name (``cascade``), a ``tier:effort``
   pair (``cascade:high``), or a configured model id (means Single to
   that model).
3. ``policies.default_tier`` from the config.

Effort works the same way: ``modelrouter_effort`` field, the ``:effort``
suffix, or ``policies.default_effort``. Effort overrides tier roles
(see ``config.resolve``); it never changes which pattern runs.

Every request writes exactly one JSONL routing-log line: prompt hash
(never prompt text), tier, effort, models tried, escalation reason,
tokens, and cost. API keys are never logged (see ``redact``).
"""

import json
import os
import re
import threading
import time
import uuid

from . import backends, quality
from .backends import BackendError
from .costs import cost_usd
from .redact import prompt_hash

ROUTER_FIELDS = ("modelrouter_tier", "modelrouter_effort", "modelrouter_project")

CRITIC_SYSTEM = (
    "You are a strict reviewer of coding answers. Score the draft answer "
    "from 0 to 10: 10 is correct, complete, and directly answers the request; "
    "0 is empty, wrong, or unrelated. Reply with JSON only, no other text: "
    '{"score": <integer 0-10>, "notes": "<one or two sentences>"}'
)

_JSON_BLOB = re.compile(r"\{.*\}", re.S)


class BudgetBlocked(Exception):
    """Daily budget cap reached; no backend was called."""


class Router:
    def __init__(self, config, tracker, log_path: str):
        self.config = config
        self.tracker = tracker
        self.log_path = log_path
        self._log_lock = threading.Lock()

    # -- request interpretation ---------------------------------------
    def select(self, body: dict):
        """Resolve (tier, effort, project, model_override) from a request.

        ``model_override`` is set when the client names a configured model
        id directly: that means Single tier to exactly that model, not to
        the single tier's configured default.
        """
        tier = body.get("modelrouter_tier")
        effort = body.get("modelrouter_effort")
        project = body.get("modelrouter_project")
        requested = body.get("model") or ""
        model_override = None
        if isinstance(requested, str) and ":" in requested:
            maybe_tier, maybe_effort = requested.split(":", 1)
            if maybe_tier in self.config.tiers and maybe_effort in (
                "low",
                "medium",
                "high",
            ):
                tier = tier or maybe_tier
                effort = effort or maybe_effort
        if not tier:
            if requested in self.config.tiers:
                tier = requested
            elif requested in self.config.models:
                tier = "single"
                model_override = requested
            else:
                tier = self.config.policies.default_tier
        if not effort:
            effort = self.config.policies.default_effort
        return tier, effort, project, model_override

    def explain(self, tier: str, effort: str) -> dict:
        """Dry-run description of what a tier/effort selection would do."""
        roles = self.config.resolve(tier, effort)
        plan = {"tier": tier, "effort": effort, "roles": roles}
        if tier == "single":
            plan["flow"] = [roles["model"]]
            plan["notes"] = "One call, straight to the configured model."
        elif tier == "cascade":
            tier_def = self.config.tiers["cascade"]
            plan["flow"] = [roles["draft"], roles["strong"]]
            plan["notes"] = (
                f"Draft on {roles['draft']}; escalate to {roles['strong']} on "
                f"upstream error, empty/short output (min "
                f"{tier_def.get('min_length', 40)} chars), refusal, or a "
                f"missing code block when code was requested."
            )
        elif tier == "critique":
            tier_def = self.config.tiers["critique"]
            plan["flow"] = [
                roles["primary"],
                roles["critic"],
                f"revise on {roles['primary']} (up to "
                f"{tier_def.get('max_rounds', 2)} rounds)",
            ]
            plan["notes"] = (
                f"{roles['primary']} drafts, {roles['critic']} scores 0-10, "
                f"revise while below threshold {tier_def.get('threshold', 7)}."
            )
        else:
            plan["flow"] = list(roles.values())
            plan["notes"] = "Custom tier: roles resolve in config order."
        return plan

    # -- public entry points ------------------------------------------
    def handle(self, body: dict):
        """Run one non-streaming request. Returns an OpenAI-shaped dict."""
        started = time.monotonic()
        tier, effort, project, model_override = self.select(body)
        budget = self.config.policies.budget
        status = self.tracker.budget_status(
            budget.daily_usd, budget.warn_fraction, budget.stop_fraction
        )
        if status == "stop":
            entry = self._log_entry(
                body, tier, effort, project, [], "budget_blocked",
                0, 0, 0.0, "budget_blocked", started,
            )
            self._write_log(entry)
            raise BudgetBlocked(
                f"daily budget of ${budget.daily_usd:.2f} reached; "
                f"no backend was called"
            )
        try:
            if tier == "single":
                content, attempts, escalation = self._run_single(
                    body, tier, effort, model_override)
            elif tier == "cascade":
                content, attempts, escalation = self._run_cascade(body, tier, effort)
            elif tier == "critique":
                content, attempts, escalation = self._run_critique(body, tier, effort)
            else:
                raise ValueError(f"unknown tier {tier!r}")
        except BackendError as exc:
            # Bill and log whatever already succeeded, then report the failure.
            attempts = getattr(exc, "attempts", [])
            escalation = getattr(exc, "escalation", None)
            for attempt in attempts:
                self.tracker.record(
                    attempt["model"], attempt["usage"], attempt["cost_usd"], project
                )
            prompt_tokens = sum(a["usage"].get("prompt_tokens") or 0 for a in attempts)
            completion_tokens = sum(
                a["usage"].get("completion_tokens") or 0 for a in attempts)
            total_cost = round(sum(a["cost_usd"] for a in attempts), 6)
            entry = self._log_entry(
                body, tier, effort, project, attempts, escalation,
                prompt_tokens, completion_tokens, total_cost, "error", started,
            )
            self._write_log(entry)
            raise
        prompt_tokens = sum(a["usage"].get("prompt_tokens") or 0 for a in attempts)
        completion_tokens = sum(a["usage"].get("completion_tokens") or 0 for a in attempts)
        total_cost = round(sum(a["cost_usd"] for a in attempts), 6)
        for attempt in attempts:
            model = self.config.require_model(attempt["model"])
            self.tracker.record(
                attempt["model"], attempt["usage"], attempt["cost_usd"], project
            )
        entry = self._log_entry(
            body, tier, effort, project, attempts, escalation,
            prompt_tokens, completion_tokens, total_cost, "ok", started,
            budget_warning=(status == "warn"),
        )
        self._write_log(entry)
        return self._response(body, content, prompt_tokens, completion_tokens,
                              tier, effort, attempts, escalation, status)

    # -- tier implementations ------------------------------------------
    def _forward_body(self, body: dict, model_id: str) -> dict:
        out = {k: v for k, v in body.items() if k not in ROUTER_FIELDS}
        out["model"] = model_id
        out["stream"] = False
        return out

    def _attempt(self, body, model_id):
        model = self.config.require_model(model_id)
        content, usage, _raw = backends.chat(
            model, self._forward_body(body, model_id),
            self.config.policies.request_timeout,
        )
        amount = round(cost_usd(model, usage), 6)
        return {
            "model": model_id,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
            },
            "cost_usd": amount,
            "content": content,
        }

    def _run_single(self, body, tier, effort, model_override=None):
        if model_override:
            model_id = self.config.require_model(model_override).id
        else:
            model_id = self.config.model_for_role(tier, effort, "model").id
        try:
            attempt = self._attempt(body, model_id)
        except BackendError as exc:
            exc.attempts = []
            exc.escalation = None
            raise
        return attempt["content"], [attempt], None

    def _run_cascade(self, body, tier, effort):
        tier_def = self.config.tiers["cascade"]
        draft_id = self.config.model_for_role(tier, effort, "draft").id
        strong_id = self.config.model_for_role(tier, effort, "strong").id
        min_length = tier_def.get("min_length", 40)
        refusal_patterns = tier_def.get("refusal_patterns")
        attempts, escalation = [], None
        try:
            draft = self._attempt(body, draft_id)
            attempts.append(draft)
            failures = quality.check_output(
                draft["content"], body.get("messages"),
                min_length=min_length, refusal_patterns=refusal_patterns,
            )
            if not failures:
                return draft["content"], attempts, None
            escalation = "+".join(failures)
        except BackendError as exc:
            escalation = f"upstream_error:{exc.status or 'unreachable'}"
        # Escalation: the strong model answers, whatever it returns.
        try:
            strong = self._attempt(body, strong_id)
        except BackendError as exc:
            exc.attempts = attempts
            exc.escalation = escalation
            raise
        strong["gate_failures_on_strong"] = quality.check_output(
            strong["content"], body.get("messages"),
            min_length=min_length, refusal_patterns=refusal_patterns,
        )
        attempts.append(strong)
        return strong["content"], attempts, escalation

    def _critique_score(self, critic_id, messages, draft):
        critic = self.config.require_model(critic_id)
        last_user = ""
        for message in reversed(messages or []):
            if message.get("role") == "user":
                last_user = message.get("content") or ""
                break
        critic_body = {
            "model": critic_id,
            "stream": False,
            "messages": [
                {"role": "system", "content": CRITIC_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Original request:\n{last_user}\n\n"
                        f"Draft answer:\n{draft}\n\nReply with JSON only."
                    ),
                },
            ],
        }
        content, usage, _raw = backends.chat(
            critic, critic_body, self.config.policies.request_timeout
        )
        match = _JSON_BLOB.search(content or "")
        if not match:
            return None, content, usage
        try:
            parsed = json.loads(match.group(0))
            score = int(parsed.get("score"))
            notes = str(parsed.get("notes", ""))
        except (ValueError, TypeError, AttributeError):
            return None, content, usage
        if not 0 <= score <= 10:
            return None, content, usage
        return (score, notes), content, usage

    def _run_critique(self, body, tier, effort):
        tier_def = self.config.tiers["critique"]
        primary_id = self.config.model_for_role(tier, effort, "primary").id
        critic_id = self.config.model_for_role(tier, effort, "critic").id
        threshold = tier_def.get("threshold", 7)
        max_rounds = tier_def.get("max_rounds", 2)
        messages = body.get("messages") or []
        attempts, escalation = [], None

        draft = self._attempt(body, primary_id)
        attempts.append(draft)
        best_content, best_score = draft["content"], -1
        rounds = 0
        while True:
            try:
                scored, critic_text, critic_usage = self._critique_score(
                    critic_id, messages, draft["content"]
                )
            except BackendError:
                escalation = "critic_error"
                break
            critic_model = self.config.require_model(critic_id)
            attempts.append({
                "model": critic_id,
                "usage": {
                    "prompt_tokens": critic_usage.get("prompt_tokens") or 0,
                    "completion_tokens": critic_usage.get("completion_tokens") or 0,
                },
                "cost_usd": round(cost_usd(critic_model, critic_usage), 6),
                "content": critic_text,
            })
            if scored is None:
                escalation = "critic_unparseable"
                break
            score, notes = scored
            if score > best_score:
                best_score, best_content = score, draft["content"]
            if score >= threshold:
                escalation = None
                break
            if rounds >= max_rounds:
                escalation = f"below_threshold_after_{max_rounds}_rounds"
                break
            rounds += 1
            escalation = f"below_threshold:{score}<{threshold}"
            revision_body = dict(body)
            revision_body["messages"] = list(messages) + [
                {"role": "assistant", "content": draft["content"]},
                {
                    "role": "user",
                    "content": (
                        f"A reviewer scored your previous answer {score}/10 "
                        f"with this feedback: {notes}\n\nRevise your answer "
                        f"to address the feedback."
                    ),
                },
            ]
            try:
                draft = self._attempt(revision_body, primary_id)
            except BackendError:
                escalation = "primary_error_on_revision"
                break
            attempts.append(draft)
        return best_content, attempts, escalation

    # -- response + logging ---------------------------------------------
    def _response(self, body, content, prompt_tokens, completion_tokens,
                  tier, effort, attempts, escalation, budget_status):
        return {
            "id": "chatcmpl-mr-" + uuid.uuid4().hex[:24],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model") or tier,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "modelrouter": {
                "tier": tier,
                "effort": effort,
                "models_tried": [a["model"] for a in attempts],
                "escalation_reason": escalation,
                "budget_status": budget_status,
            },
        }

    def _log_entry(self, body, tier, effort, project, attempts, escalation,
                   prompt_tokens, completion_tokens, total_cost, outcome,
                   started, budget_warning=False):
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "request_id": uuid.uuid4().hex[:16],
            "prompt_hash": prompt_hash(body.get("messages") or []),
            "tier": tier,
            "effort": effort,
            "project": project,
            "models_tried": [a["model"] for a in attempts],
            "escalation_reason": escalation,
            "budget_warning": budget_warning,
            "attempts": [
                {
                    "model": a["model"],
                    "prompt_tokens": a["usage"].get("prompt_tokens") or 0,
                    "completion_tokens": a["usage"].get("completion_tokens") or 0,
                    "cost_usd": a["cost_usd"],
                    **({"gate_failures": a["gate_failures_on_strong"]}
                       if "gate_failures_on_strong" in a else {}),
                }
                for a in attempts
            ],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": round(total_cost, 6),
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "outcome": outcome,
        }

    def _write_log(self, entry):
        line = json.dumps(entry, sort_keys=True)
        directory = os.path.dirname(self.log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._log_lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
