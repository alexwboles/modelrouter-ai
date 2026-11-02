"""Cost accounting: per-request usage, running totals, budget caps.

Prices come from the config file (USD per 1M tokens, editable by the
user). Totals persist in ``<data_dir>/costs.json`` so ``modelrouter
costs`` works across restarts; the routing log (JSONL) is the audit
trail behind the totals.

Budget semantics (configurable, defaults in parentheses):

- warn at 80% of the daily cap: the request still runs, and the warning
  is recorded in the log and surfaced on the response.
- stop at 100% of the daily cap: the request is refused with HTTP 429
  before any backend is called. A request that would land exactly on
  the cap is allowed; only spending *beyond* the cap is blocked.
- no ``daily_usd`` configured means no cap at all.
"""

import json
import os
import threading
from datetime import datetime, timezone


def cost_usd(model, usage: dict) -> float:
    """Price one call from its token usage. Missing usage -> 0.0."""
    prompt = usage.get("prompt_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    return (prompt / 1_000_000 * model.input_price) + (
        completion / 1_000_000 * model.output_price
    )


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _empty_bucket():
    return {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}


class CostTracker:
    def __init__(self, state_path: str):
        self.state_path = state_path
        self._lock = threading.Lock()
        self.state = {"daily": {}, "by_model": {}, "by_project": {}}
        self._load()

    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                for key in self.state:
                    if isinstance(loaded.get(key), dict):
                        self.state[key] = loaded[key]
        except (OSError, ValueError):
            pass  # missing/corrupt state starts empty; the log is the backup

    def _save(self):
        directory = os.path.dirname(self.state_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.state_path)

    def record(self, model_id: str, usage: dict, amount: float, project: str | None):
        """Add one backend call to every breakdown bucket."""
        with self._lock:
            prompt = usage.get("prompt_tokens") or 0
            completion = usage.get("completion_tokens") or 0
            for bucket in (
                self.state["daily"].setdefault(_today(), _empty_bucket()),
                self.state["by_model"].setdefault(model_id, _empty_bucket()),
            ):
                bucket["requests"] += 1
                bucket["prompt_tokens"] += prompt
                bucket["completion_tokens"] += completion
                bucket["cost_usd"] = round(bucket["cost_usd"] + amount, 6)
            if project:
                bucket = self.state["by_project"].setdefault(project, _empty_bucket())
                bucket["requests"] += 1
                bucket["prompt_tokens"] += prompt
                bucket["completion_tokens"] += completion
                bucket["cost_usd"] = round(bucket["cost_usd"] + amount, 6)
            self._save()

    def spent_today(self) -> float:
        return self.state["daily"].get(_today(), {}).get("cost_usd", 0.0)

    def budget_status(self, daily_cap: float | None, warn_fraction: float,
                      stop_fraction: float) -> str:
        """'ok' | 'warn' | 'stop' for today's spend against the cap."""
        if daily_cap is None or daily_cap <= 0:
            return "ok"
        spent = self.spent_today()
        if spent >= daily_cap * stop_fraction:
            return "stop"
        if spent >= daily_cap * warn_fraction:
            return "warn"
        return "ok"

    # -- reports -------------------------------------------------------
    def markdown_report(self, days: int | None = None) -> str:
        lines = ["# modelrouter costs", ""]
        daily = self.state["daily"]
        keys = sorted(daily, reverse=True)
        if days is not None:
            keys = keys[:days]
        lines.append("## Spend by day")
        lines.append("")
        lines.append("| Day | Requests | Prompt tokens | Completion tokens | Cost (USD) |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        total = 0.0
        for day in keys:
            bucket = daily[day]
            total += bucket["cost_usd"]
            lines.append(
                f"| {day} | {bucket['requests']} | {bucket['prompt_tokens']} | "
                f"{bucket['completion_tokens']} | ${bucket['cost_usd']:.4f} |"
            )
        if not keys:
            lines.append("| _no usage recorded yet_ | | | | |")
        lines.append("")
        lines.append(f"**Total (shown days): ${total:.4f}**")
        lines.append("")
        lines.append("## Spend by model")
        lines.append("")
        lines.append("| Model | Requests | Cost (USD) |")
        lines.append("| --- | ---: | ---: |")
        for model_id in sorted(self.state["by_model"]):
            bucket = self.state["by_model"][model_id]
            lines.append(f"| {model_id} | {bucket['requests']} | ${bucket['cost_usd']:.4f} |")
        if not self.state["by_model"]:
            lines.append("| _no usage recorded yet_ | | |")
        lines.append("")
        lines.append("## Spend by project")
        lines.append("")
        lines.append("| Project | Requests | Cost (USD) |")
        lines.append("| --- | ---: | ---: |")
        for project in sorted(self.state["by_project"]):
            bucket = self.state["by_project"][project]
            lines.append(f"| {project} | {bucket['requests']} | ${bucket['cost_usd']:.4f} |")
        if not self.state["by_project"]:
            lines.append("| _no project-tagged requests yet_ | | |")
        lines.append("")
        return "\n".join(lines)

    def json_report(self) -> dict:
        return {
            "today": _today(),
            "spent_today_usd": round(self.spent_today(), 6),
            "daily": self.state["daily"],
            "by_model": self.state["by_model"],
            "by_project": self.state["by_project"],
        }
