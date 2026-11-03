"""Command line: ``modelrouter serve | route --explain | costs``."""

import argparse
import json
import os
import sys

from . import __version__, server
from .config import ConfigError, find_config, load_config
from .costs import CostTracker
from .router import Router


def _data_dir(args, config_path: str) -> str:
    if getattr(args, "data_dir", None):
        return args.data_dir
    env = os.environ.get("MODELROUTER_DATA_DIR")
    if env:
        return env
    return os.path.expanduser("~/.local/share/modelrouter")


def _build(args):
    config_path = find_config(getattr(args, "config", None))
    config = load_config(config_path)
    data_dir = _data_dir(args, config_path)
    tracker = CostTracker(os.path.join(data_dir, "costs.json"))
    router = Router(config, tracker, os.path.join(data_dir, "routing.jsonl"))
    return config, tracker, router, data_dir


def _cmd_serve(args):
    config, tracker, router, data_dir = _build(args)
    missing_keys = [
        f"{m.id} (set ${m.key_env})"
        for m in config.models.values()
        if m.key_env and not m.api_key()
    ]
    if missing_keys:
        print("warning: no key in environment for: " + ", ".join(missing_keys),
              file=sys.stderr)
    httpd = server.serve(config, tracker, router, args.host, args.port)
    print(f"modelrouter {__version__} listening on http://{args.host}:{args.port}")
    print(f"config: {config.path}  data: {data_dir}")
    print("OpenAI-compatible base URL: "
          f"http://{args.host}:{args.port}/v1  (set your agent's base_url here)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nmodelrouter stopped.")
    return 0


def _cmd_route(args):
    config, _tracker, router, _data = _build(args)
    tier = args.tier or config.policies.default_tier
    effort = args.effort or config.policies.default_effort
    try:
        plan = router.explain(tier, effort)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"prompt: {args.prompt!r}  (dry run — no backend was called)")
    print(f"tier:   {plan['tier']}  (effort: {plan['effort']})")
    print(f"flow:   {' -> '.join(plan['flow'])}")
    print(f"why:    {plan['notes']}")
    print(f"roles:  {json.dumps(plan['roles'], sort_keys=True)}")
    return 0


def _cmd_costs(args):
    config, tracker, _router, _data = _build(args)
    if args.json:
        print(json.dumps(tracker.json_report(), indent=2, sort_keys=True))
    elif args.html:
        cap = config.policies.budget.daily_usd
        print(tracker.html_report(
            days=args.days, cap=cap,
            warn_fraction=config.policies.budget.warn_fraction,
            stop_fraction=config.policies.budget.stop_fraction))
    else:
        print(tracker.markdown_report(days=args.days))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="modelrouter",
        description="Fusion-style model router: one OpenAI-compatible "
                    "endpoint, many models, three routing tiers.",
    )
    parser.add_argument("--version", action="version",
                        version=f"modelrouter {__version__}")
    parser.add_argument("--config", default=None,
                        help="config file (default: ./modelrouter.yaml or "
                             "~/.config/modelrouter/config.yaml)")
    parser.add_argument("--data-dir", default=None,
                        help="data dir for routing.jsonl + costs.json "
                             "(default: ~/.local/share/modelrouter)")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the OpenAI-compatible proxy")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=4000)
    serve.set_defaults(func=_cmd_serve)

    route = sub.add_parser("route", help="dry-run: show which model would be picked")
    route.add_argument("--explain", dest="prompt", required=True,
                       help="prompt to explain routing for (never logged)")
    route.add_argument("--tier", default=None,
                       help="tier to explain (default: policies.default_tier)")
    route.add_argument("--effort", default=None,
                       help="effort level: low, medium, high")
    route.set_defaults(func=_cmd_route)

    costs = sub.add_parser("costs", help="spend report from recorded usage")
    costs.add_argument("--json", action="store_true", help="JSON instead of Markdown")
    costs.add_argument("--html", action="store_true",
                       help="styled HTML report instead of Markdown")
    costs.add_argument("--days", type=int, default=None,
                       help="limit the by-day table to the last N days")
    costs.set_defaults(func=_cmd_costs)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
