"""Shared HTML surfaces for modelrouter: status dashboard + cost report.

One design language ("The Switchboard Ledger", see docs/UI-AUDIT.md):
paper-white background, ink text, one signal-green accent, amber for
warn, red for stop. No gradients, no rounded corners, no emoji, no
webfonts, no JavaScript libraries — the pages must work offline and
self-hosted, rendered from stdlib only.

Privacy rules, enforced by construction here:

- Prompt text is NEVER rendered. The routing log stores a SHA-256 hash,
  never the text; ``recent_decisions`` whitelists safe fields and drops
  everything else, including the hash itself.
- API keys are NEVER rendered. Model rows show "key set" / "local"
  only; key values are never passed into this module.
"""

import html
import json
import os
from datetime import datetime, timezone

# -- palette -----------------------------------------------------------
INK = "#16150f"
PAPER = "#faf9f6"
PANEL = "#ffffff"
LINE = "#d8d5cc"
MUTED = "#6b675c"
GREEN = "#14663f"
GREEN_SOFT = "#e2efe7"
AMBER = "#9a6a00"
AMBER_SOFT = "#f7ecd2"
RED = "#b3261e"
RED_SOFT = "#fbe7e5"
BLUEGREY = "#33505e"

CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: %(paper)s; color: %(ink)s;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  line-height: 1.5; }
a { color: %(green)s; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 32px 24px 64px; }
header { display: flex; flex-wrap: wrap; align-items: baseline;
  justify-content: space-between; gap: 12px;
  border-bottom: 3px solid %(ink)s; padding-bottom: 16px; }
h1 { font-family: Georgia, "Times New Roman", serif; font-size: 34px;
  line-height: 1.1; margin: 0; letter-spacing: -0.5px; }
h1 span { color: %(green)s; }
h2 { font-family: Georgia, "Times New Roman", serif; font-size: 22px;
  margin: 40px 0 12px; }
h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
  color: %(muted)s; margin: 0 0 8px; }
.lede { color: %(muted)s; margin: 8px 0 0; max-width: 640px; }
.pill { display: inline-block; padding: 3px 10px; font-size: 12px;
  font-weight: 700; letter-spacing: 1px; border-radius: 0; }
.pill.live { background: %(green_soft)s; color: %(green)s; }
.pill.ok { background: %(green_soft)s; color: %(green)s; }
.pill.warn { background: %(amber_soft)s; color: %(amber)s; }
.pill.stop { background: %(red_soft)s; color: %(red)s; }
.pill.neutral { background: #efede6; color: %(muted)s; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
  margin-top: 24px; }
.kpi { background: %(panel)s; border: 1px solid %(line)s; padding: 16px; }
.kpi .v { font-family: Georgia, "Times New Roman", serif; font-size: 30px;
  line-height: 1.1; }
.kpi .l { font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
  color: %(muted)s; margin-top: 4px; }
.kpi .s { font-size: 13px; color: %(muted)s; }
section.panel { background: %(panel)s; border: 1px solid %(line)s;
  padding: 20px; margin-top: 24px; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%%; font-size: 14px; }
th { text-align: left; font-size: 12px; text-transform: uppercase;
  letter-spacing: 1px; color: %(muted)s; padding: 8px 12px;
  border-bottom: 2px solid %(ink)s; white-space: nowrap; }
td { padding: 9px 12px; border-bottom: 1px solid %(line)s;
  vertical-align: top; }
tr:last-child td { border-bottom: 0; }
.mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 13px; }
.num { text-align: right; font-variant-numeric: tabular-nums;
  white-space: nowrap; }
.bar-track { position: relative; height: 22px; background: #efede6;
  border: 1px solid %(line)s; }
.bar-fill { height: 100%%; background: %(green)s; }
.bar-fill.warn { background: %(amber)s; }
.bar-fill.stop { background: %(red)s; }
.bar-marker { position: absolute; top: -3px; bottom: -3px; width: 2px;
  background: %(ink)s; }
.bar-labels { display: flex; justify-content: space-between; font-size: 12px;
  color: %(muted)s; margin-top: 6px; }
.chart { display: flex; align-items: flex-end; gap: 8px; height: 160px;
  border-bottom: 2px solid %(ink)s; padding-top: 8px; }
.chart .col { flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: flex-end; height: 100%%; }
.chart .col .bar { width: 100%%; background: %(bluegrey)s; }
.chart .col .bar.hot { background: %(green)s; }
.chart .col .amt { font-size: 11px; color: %(muted)s; margin-bottom: 4px; }
.chart .col .day { font-size: 11px; color: %(muted)s; margin-top: 6px; }
.steps { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr;
  gap: 12px; align-items: stretch; }
.step { border: 1px solid %(line)s; padding: 16px; background: %(panel)s; }
.step .t { font-family: Georgia, "Times New Roman", serif; font-size: 19px; }
.step .d { font-size: 13px; color: %(muted)s; margin-top: 4px; }
.arrow { align-self: center; font-size: 20px; color: %(muted)s; }
pre { background: %(ink)s; color: #f2f0e8; padding: 16px; overflow-x: auto;
  font-size: 13px; line-height: 1.6; border-radius: 0; }
.foot { margin-top: 40px; font-size: 12px; color: %(muted)s;
  border-top: 1px solid %(line)s; padding-top: 12px; }
@media (max-width: 760px) {
  .wrap { padding: 20px 14px 48px; }
  h1 { font-size: 27px; }
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .steps { grid-template-columns: 1fr; }
  .arrow { display: none; }
  .bar-labels { flex-direction: column; gap: 2px; }
}
""" % {
    "paper": PAPER, "ink": INK, "panel": PANEL, "line": LINE,
    "muted": MUTED, "green": GREEN, "green_soft": GREEN_SOFT,
    "amber": AMBER, "amber_soft": AMBER_SOFT, "red": RED,
    "red_soft": RED_SOFT, "bluegrey": BLUEGREY,
}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def money(value: float) -> str:
    return f"${value:,.4f}"


def fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_uptime(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def today_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _today_bucket(state: dict) -> dict:
    return state.get("daily", {}).get(today_str(), {})


def budget_bar_html(spent: float, cap, warn_fraction: float,
                    stop_fraction: float) -> str:
    """Budget progress bar with warn/stop markers. No cap -> honest note."""
    if cap is None or cap <= 0:
        return ("<p class=\"s\">No daily budget cap configured — set "
                "<span class=\"mono\">policies.budget.daily_usd</span> in "
                "your config to enable the cap meter.</p>")
    pct = min(100.0, spent / cap * 100) if cap else 0.0
    status = "ok"
    if spent >= cap * stop_fraction:
        status = "stop"
    elif spent >= cap * warn_fraction:
        status = "warn"
    warn_left = min(100.0, warn_fraction * 100)
    stop_left = min(100.0, stop_fraction * 100)
    return f"""
<div class="bar-track"><div class="bar-fill {status}" style="width: {pct:.1f}%"></div>
<div class="bar-marker" style="left: {warn_left:.1f}%" title="warn at {warn_fraction:.0%}"></div>
<div class="bar-marker" style="left: {stop_left:.1f}%" title="stop at {stop_fraction:.0%}"></div></div>
<div class="bar-labels"><span>{money(spent)} spent today</span>
<span>warn {money(cap * warn_fraction)} &middot; stop {money(cap * stop_fraction)} &middot; cap {money(cap)}</span></div>"""


def daily_bars_html(state: dict, limit: int = 14) -> str:
    """Daily spend bar chart (plain divs, no JS). Latest day highlighted."""
    daily = state.get("daily", {})
    days = sorted(daily)[-limit:] if daily else []
    if not days:
        return "<p>No usage recorded yet.</p>"
    top = max(daily[d]["cost_usd"] for d in days) or 1.0
    cols = []
    for day in days:
        bucket = daily[day]
        height = max(2.0, bucket["cost_usd"] / top * 100)
        hot = " hot" if day == days[-1] else ""
        label = day[5:]
        cols.append(
            f"<div class=\"col\"><div class=\"amt\">{money(bucket['cost_usd'])}</div>"
            f"<div class=\"bar{hot}\" style=\"height: {height:.0f}%\"></div>"
            f"<div class=\"day\">{esc(label)}</div></div>"
        )
    return "<div class=\"chart\">" + "".join(cols) + "</div>"


def tier_roles(config, model_id: str) -> str:
    """Distinct 'tier:role' pairs naming this model, defaults + overrides."""
    pairs = []
    for tier_name, definition in config.tiers.items():
        for role, mid in definition.items():
            if role in ("model", "draft", "strong", "primary", "critic") \
                    and str(mid) == model_id and f"{tier_name}:{role}" not in pairs:
                pairs.append(f"{tier_name}:{role}")
    for per_tier in (config.effort or {}).values():
        for tier_name, overrides in (per_tier or {}).items():
            for role, mid in (overrides or {}).items():
                if str(mid) == model_id and f"{tier_name}:{role}" not in pairs:
                    pairs.append(f"{tier_name}:{role}")
    return " · ".join(pairs) if pairs else "—"


def _prettify_reason(reason) -> str:
    if not reason:
        return "—"
    text = str(reason).replace("_", " ")
    return esc(" → ".join(part for part in text.split("+")))


def recent_decisions(log_path: str, limit: int = 10) -> list:
    """Last N routing log entries, whitelisted to safe fields only.

    The log never contains prompt text (only a SHA-256 hash) and we do
    not render even the hash here. Unexpected keys are dropped.
    """
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            lines = [line for line in fh if line.strip()]
    except OSError:
        return []
    entries = []
    for line in lines[-limit:]:
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        entries.append({
            "ts": raw.get("ts", ""),
            "request_id": raw.get("request_id", "")[:8],
            "tier": raw.get("tier", ""),
            "effort": raw.get("effort", ""),
            "models_tried": raw.get("models_tried", []) or [],
            "escalation_reason": raw.get("escalation_reason"),
            "prompt_tokens": raw.get("prompt_tokens", 0),
            "completion_tokens": raw.get("completion_tokens", 0),
            "cost_usd": raw.get("cost_usd", 0.0),
            "duration_ms": raw.get("duration_ms", 0),
            "outcome": raw.get("outcome", ""),
        })
    entries.reverse()
    return entries


def _page(title: str, headline: str, lede: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head><body>
<div class="wrap"><header><div><h1>{headline}</h1>
<p class="lede">{lede}</p></div></header>{body}
<p class="foot">modelrouter — a free, self-hosted, MIT-licensed model router.
Prompts and API keys never appear on this page by design.</p></div>
</body></html>"""


def render_status_page(config, tracker, uptime_seconds: float,
                        log_path: str, base_url: str) -> str:
    """The GET / dashboard."""
    bucket = _today_bucket(tracker.state)
    requests = bucket.get("requests", 0)
    prompt_tokens = bucket.get("prompt_tokens", 0)
    completion_tokens = bucket.get("completion_tokens", 0)
    spent = tracker.spent_today()
    cap = config.policies.budget.daily_usd
    status = tracker.budget_status(
        cap, config.policies.budget.warn_fraction,
        config.policies.budget.stop_fraction)
    status_label = {"ok": "WITHIN BUDGET", "warn": "BUDGET WARN",
                    "stop": "BUDGET STOPPED"}[status]
    rows = "".join(
        f"<tr><td class=\"mono\">{esc(m.id)}</td>"
        f"<td class=\"mono\">{esc(m.endpoint)}</td>"
        f"<td>{'key set' if m.api_key() else 'local'}</td>"
        f"<td class=\"num\">${m.input_price:.2f} / ${m.output_price:.2f}</td>"
        f"<td>{esc(tier_roles(config, m.id))}</td></tr>"
        for m in config.models.values()
    )
    decisions = recent_decisions(log_path)
    if decisions:
        drows = "".join(
            f"<tr><td class=\"mono\">{esc(d['ts'][11:19]) if d['ts'] else '—'}</td>"
            f"<td>{esc(d['tier'])} <span class=\"mono\">({esc(d['effort'])})</span></td>"
            f"<td class=\"mono\">{esc(' → '.join(d['models_tried']))}</td>"
            f"<td>{_prettify_reason(d['escalation_reason'])}</td>"
            f"<td class=\"num\">{fmt_int(d['prompt_tokens'] + d['completion_tokens'])}</td>"
            f"<td class=\"num\">{money(d['cost_usd'])}</td>"
            f"<td><span class=\"pill {'ok' if d['outcome'] == 'ok' else 'stop'}\">"
            f"{esc(d['outcome'] or '—').upper()}</span></td></tr>"
            for d in decisions
        )
        decisions_html = f"""<div class="tablewrap"><table>
<tr><th>Time</th><th>Tier</th><th>Models tried</th><th>Why it escalated</th>
<th class="num">Tokens</th><th class="num">Cost</th><th>Outcome</th></tr>
{drows}</table></div>"""
    else:
        decisions_html = ("<p>No routing decisions recorded yet. Send a "
                          "request to <span class=\"mono\">POST "
                          "/v1/chat/completions</span> and it will appear here.</p>")
    snippet = (
        "export OPENAI_BASE_URL=\"" + base_url + "/v1\"\n"
        "curl $OPENAI_BASE_URL/chat/completions \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  -d '{\"model\": \"cascade:medium\", \"messages\": "
        "[{\"role\": \"user\", \"content\": \"Hello\"}]}'"
    )
    body = f"""
<div class="kpis">
<div class="kpi"><div class="v">{fmt_int(requests)}</div>
<div class="l">Requests today</div></div>
<div class="kpi"><div class="v">{fmt_int(prompt_tokens + completion_tokens)}</div>
<div class="l">Tokens today</div>
<div class="s">{fmt_int(prompt_tokens)} in · {fmt_int(completion_tokens)} out</div></div>
<div class="kpi"><div class="v">{money(spent)}</div>
<div class="l">Spent today</div></div>
<div class="kpi"><div class="v"><span class="pill {status}">{status_label}</span></div>
<div class="l">Budget status</div>
<div class="s">Uptime {esc(fmt_uptime(uptime_seconds))}</div></div>
</div>

<section class="panel"><h3>Today's budget</h3>
{budget_bar_html(spent, cap, config.policies.budget.warn_fraction,
                 config.policies.budget.stop_fraction)}
</section>

<section class="panel"><h3>How routing works</h3>
<div class="steps">
<div class="step"><div class="t">Single</div>
<div class="d">One model, straight to the answer. Fastest when you already
know which model you want.</div></div><div class="arrow">→</div>
<div class="step"><div class="t">Cascade</div>
<div class="d">A cheap draft first. If it is empty, refused, or too weak, the
strong model takes over — and the reason is logged.</div></div>
<div class="arrow">→</div>
<div class="step"><div class="t">Critique</div>
<div class="d">A primary model drafts, a critic scores it, and weak drafts get
revised — up to two rounds, best attempt wins.</div></div>
</div></section>

<section class="panel"><h3>Models</h3>
<div class="tablewrap"><table>
<tr><th>Model</th><th>Endpoint</th><th>Auth</th>
<th class="num">In / out per 1M</th><th>Tier roles</th></tr>
{rows}</table></div></section>

<section class="panel"><h3>Recent routing decisions</h3>
{decisions_html}
<p class="s">Prompt text never appears here — the log stores a hash, not
your words. Keys never appear either: models show "key set" or "local" only.</p></section>

<section class="panel"><h3>Point your agent at it</h3>
<pre>{esc(snippet)}</pre>
<p class="s">Works with any OpenAI-compatible client. See
<span class="mono">docs/AGENT-SETUP.md</span> for OpenCode, Codex CLI, and
Claude Code.</p></section>
"""
    return _page("modelrouter dashboard",
                 "modelrouter<span> ·</span> dashboard",
                 "One OpenAI-compatible endpoint, many models, three routing "
                 "tiers — and every decision accounted for.",
                 body)


def render_cost_report(state: dict, days=None, cap=None,
                       warn_fraction: float = 0.8,
                       stop_fraction: float = 1.0) -> str:
    """Styled HTML cost report in the dashboard's design language."""
    daily = state.get("daily", {})
    keys = sorted(daily, reverse=True)
    if days is not None:
        keys = keys[:days]
    total = sum(daily[d]["cost_usd"] for d in keys)
    drows = "".join(
        f"<tr><td class=\"mono\">{esc(day)}</td>"
        f"<td class=\"num\">{fmt_int(daily[day]['requests'])}</td>"
        f"<td class=\"num\">{fmt_int(daily[day]['prompt_tokens'])}</td>"
        f"<td class=\"num\">{fmt_int(daily[day]['completion_tokens'])}</td>"
        f"<td class=\"num\">{money(daily[day]['cost_usd'])}</td></tr>"
        for day in keys
    ) or "<tr><td colspan=\"5\">No usage recorded yet.</td></tr>"
    mrows = "".join(
        f"<tr><td class=\"mono\">{esc(mid)}</td>"
        f"<td class=\"num\">{fmt_int(b['requests'])}</td>"
        f"<td class=\"num\">{money(b['cost_usd'])}</td></tr>"
        for mid, b in sorted(state.get("by_model", {}).items())
    ) or "<tr><td colspan=\"3\">No usage recorded yet.</td></tr>"
    prows = "".join(
        f"<tr><td class=\"mono\">{esc(proj)}</td>"
        f"<td class=\"num\">{fmt_int(b['requests'])}</td>"
        f"<td class=\"num\">{money(b['cost_usd'])}</td></tr>"
        for proj, b in sorted(state.get("by_project", {}).items())
    ) or "<tr><td colspan=\"3\">No project-tagged requests yet.</td></tr>"
    today = today_str()
    spent_today = daily.get(today, {}).get("cost_usd", 0.0)
    if cap is None or cap <= 0:
        budget_html = ("<p>No daily budget cap configured.</p>")
        budget_label, budget_class = "NO CAP", "neutral"
    else:
        if spent_today >= cap * stop_fraction:
            budget_label, budget_class = "BUDGET STOPPED", "stop"
        elif spent_today >= cap * warn_fraction:
            budget_label, budget_class = "BUDGET WARN", "warn"
        else:
            budget_label, budget_class = "WITHIN BUDGET", "ok"
        budget_html = budget_bar_html(spent_today, cap, warn_fraction,
                                      stop_fraction)
    body = f"""
<div class="kpis">
<div class="kpi"><div class="v">{money(total)}</div>
<div class="l">Total (shown days)</div></div>
<div class="kpi"><div class="v">{money(spent_today)}</div>
<div class="l">Spent today</div></div>
<div class="kpi"><div class="v">{fmt_int(sum(daily[d]['requests'] for d in keys))}</div>
<div class="l">Requests (shown days)</div></div>
<div class="kpi"><div class="v"><span class="pill {budget_class}">{budget_label}</span></div>
<div class="l">Budget status</div></div>
</div>

<section class="panel"><h3>Spend by day</h3>
{daily_bars_html(state, limit=14)}
<div class="tablewrap"><table>
<tr><th>Day</th><th class="num">Requests</th><th class="num">Prompt tokens</th>
<th class="num">Completion tokens</th><th class="num">Cost</th></tr>
{drows}</table></div></section>

<section class="panel"><h3>Today's budget</h3>
{budget_html}</section>

<section class="panel"><h3>Spend by model</h3>
<div class="tablewrap"><table>
<tr><th>Model</th><th class="num">Requests</th><th class="num">Cost</th></tr>
{mrows}</table></div></section>

<section class="panel"><h3>Spend by project</h3>
<div class="tablewrap"><table>
<tr><th>Project</th><th class="num">Requests</th><th class="num">Cost</th></tr>
{prows}</table></div></section>
"""
    return _page("modelrouter costs",
                 "modelrouter<span> ·</span> costs",
                 "Where the money went — by day, by model, by project.",
                 body)
