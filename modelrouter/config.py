"""Configuration: one YAML file, validated on load.

Schema (see ``config.example.yaml`` for a full example)::

    models:                       # the catalog the router may call
      - id: qwen3-local           # name backends see in the `model` field
        endpoint: http://localhost:11434/v1
        key_env: ""               # env var holding the key; "" = no auth
        input_price: 0.0          # USD per 1M input tokens
        output_price: 0.0         # USD per 1M output tokens
        context_window: 32768
    tiers:                        # routing patterns, in terms of roles
      single:   {model: qwen3-local}
      cascade:  {draft: qwen3-local, strong: gpt-cheap, min_length: 40}
      critique: {primary: qwen3-local, critic: gpt-cheap,
                 threshold: 7, max_rounds: 2}
    effort:                       # low/medium/high override tier roles
      default: medium
      low:    {cascade: {strong: qwen3-local}}
      medium: {}
      high:   {single: {model: gpt-strong}}
    policies:
      default_tier: cascade
      request_timeout: 120
      budget: {daily_usd: 5.0, warn_fraction: 0.8, stop_fraction: 1.0}

API keys are NEVER read from this file: ``key_env`` names an environment
variable, and only the variable's presence (never its value) is logged.
"""

import os
from dataclasses import dataclass, field

from . import yamlmini

DEFAULT_TIERS = ("single", "cascade", "critique")
DEFAULT_EFFORTS = ("low", "medium", "high")


class ConfigError(ValueError):
    """Raised when the config file is missing, malformed, or inconsistent."""


@dataclass
class Model:
    id: str
    endpoint: str
    key_env: str = ""
    input_price: float = 0.0
    output_price: float = 0.0
    context_window: int = 0

    def api_key(self):
        """Resolve the key from the environment. Returns "" when unset."""
        if not self.key_env:
            return ""
        return os.environ.get(self.key_env, "")


@dataclass
class Budget:
    daily_usd: float | None = None
    warn_fraction: float = 0.8
    stop_fraction: float = 1.0


@dataclass
class Policies:
    default_tier: str = "cascade"
    default_effort: str = "medium"
    request_timeout: float = 120.0
    budget: Budget = field(default_factory=Budget)


@dataclass
class Config:
    models: dict  # id -> Model
    tiers: dict  # tier name -> role dict
    effort: dict  # effort level -> {tier name -> role overrides}
    default_effort: str
    policies: Policies
    path: str

    # -- resolution ----------------------------------------------------
    def resolve(self, tier: str, effort: str) -> dict:
        """Merge tier defaults with effort overrides -> role -> model id."""
        if tier not in self.tiers:
            raise ConfigError(f"unknown tier {tier!r}; known tiers: {sorted(self.tiers)}")
        if effort not in DEFAULT_EFFORTS:
            raise ConfigError(f"unknown effort {effort!r}; use low, medium, or high")
        roles = dict(self.tiers[tier])
        overrides = (self.effort.get(effort) or {}).get(tier) or {}
        roles.update(overrides)
        return roles

    def model_for_role(self, tier: str, effort: str, role: str) -> Model:
        roles = self.resolve(tier, effort)
        model_id = roles.get(role)
        if not model_id:
            raise ConfigError(f"tier {tier!r} has no model for role {role!r}")
        return self.require_model(model_id)

    def require_model(self, model_id: str) -> Model:
        try:
            return self.models[model_id]
        except KeyError:
            raise ConfigError(
                f"unknown model {model_id!r}; configured models: {sorted(self.models)}"
            ) from None

    def virtual_model_names(self):
        """Names clients may put in the `model` field, beyond real ids."""
        names = sorted(self.models)
        names += [f"{tier}:{eff}" for tier in self.tiers for eff in DEFAULT_EFFORTS]
        names += sorted(self.tiers)
        return names


def _as_float(value, what):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{what} must be a number, got {value!r}") from None


def _as_int(value, what):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{what} must be an integer, got {value!r}") from None


def load_config(path: str) -> Config:
    """Load and validate a router config file."""
    if not os.path.exists(path):
        raise ConfigError(
            f"config file not found: {path} "
            f"(copy config.example.yaml to get started)"
        )
    raw = yamlmini.load_file(path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    model_list = raw.get("models")
    if not isinstance(model_list, list) or not model_list:
        raise ConfigError(f"{path}: 'models' must be a non-empty list")
    models = {}
    for entry in model_list:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("endpoint"):
            raise ConfigError(f"{path}: each model needs 'id' and 'endpoint': {entry!r}")
        model = Model(
            id=str(entry["id"]),
            endpoint=str(entry["endpoint"]).rstrip("/"),
            key_env=str(entry.get("key_env") or ""),
            input_price=_as_float(entry.get("input_price", 0.0), "input_price"),
            output_price=_as_float(entry.get("output_price", 0.0), "output_price"),
            context_window=_as_int(entry.get("context_window", 0), "context_window"),
        )
        if model.id in models:
            raise ConfigError(f"{path}: duplicate model id {model.id!r}")
        models[model.id] = model

    tiers_raw = raw.get("tiers") or {}
    if not isinstance(tiers_raw, dict):
        raise ConfigError(f"{path}: 'tiers' must be a mapping")
    tiers = {}
    for name, definition in tiers_raw.items():
        if not isinstance(definition, dict):
            raise ConfigError(f"{path}: tier {name!r} must be a mapping")
        tiers[str(name)] = {str(k): v for k, v in definition.items()}
    for required in ("single", "cascade", "critique"):
        if required not in tiers:
            raise ConfigError(
                f"{path}: tier {required!r} is required; define it under 'tiers'"
            )
    # Every role that names a model must name a configured model.
    for tier_name, definition in tiers.items():
        for role in ("model", "draft", "strong", "primary", "critic"):
            if role in definition:
                models.get(str(definition[role])) or _missing_model(
                    path, tier_name, role, definition[role]
                )

    effort_raw = raw.get("effort") or {}
    if not isinstance(effort_raw, dict):
        raise ConfigError(f"{path}: 'effort' must be a mapping")
    default_effort = str(effort_raw.get("default", "medium"))
    if default_effort not in DEFAULT_EFFORTS:
        raise ConfigError(f"{path}: effort.default must be low, medium, or high")
    effort = {}
    for level, per_tier in effort_raw.items():
        if level == "default":
            continue
        if level not in DEFAULT_EFFORTS:
            raise ConfigError(f"{path}: unknown effort level {level!r}")
        if not isinstance(per_tier, dict):
            raise ConfigError(f"{path}: effort.{level} must be a mapping")
        effort[level] = {}
        for tier_name, overrides in per_tier.items():
            if tier_name not in tiers:
                raise ConfigError(f"{path}: effort.{level} names unknown tier {tier_name!r}")
            if not isinstance(overrides, dict):
                raise ConfigError(f"{path}: effort.{level}.{tier_name} must be a mapping")
            for role, model_id in overrides.items():
                if str(model_id) not in models:
                    raise ConfigError(
                        f"{path}: effort.{level}.{tier_name}.{role} names unknown "
                        f"model {model_id!r}"
                    )
            effort[level][tier_name] = {str(k): str(v) for k, v in overrides.items()}

    policies_raw = raw.get("policies") or {}
    if not isinstance(policies_raw, dict):
        raise ConfigError(f"{path}: 'policies' must be a mapping")
    budget_raw = policies_raw.get("budget") or {}
    if not isinstance(budget_raw, dict):
        raise ConfigError(f"{path}: policies.budget must be a mapping")
    daily = budget_raw.get("daily_usd")
    budget = Budget(
        daily_usd=None if daily is None else _as_float(daily, "budget.daily_usd"),
        warn_fraction=_as_float(
            budget_raw.get("warn_fraction", 0.8), "budget.warn_fraction"
        ),
        stop_fraction=_as_float(
            budget_raw.get("stop_fraction", 1.0), "budget.stop_fraction"
        ),
    )
    policies = Policies(
        default_tier=str(policies_raw.get("default_tier", "cascade")),
        default_effort=default_effort,
        request_timeout=_as_float(
            policies_raw.get("request_timeout", 120), "request_timeout"
        ),
        budget=budget,
    )
    if policies.default_tier not in tiers:
        raise ConfigError(
            f"{path}: policies.default_tier names unknown tier "
            f"{policies.default_tier!r}"
        )
    return Config(
        models=models,
        tiers=tiers,
        effort=effort,
        default_effort=default_effort,
        policies=policies,
        path=path,
    )


def _missing_model(path, tier, role, model_id):
    raise ConfigError(
        f"{path}: tier {tier!r} role {role!r} names unknown model {model_id!r}"
    )


def find_config(explicit: str | None) -> str:
    """Resolve the config path: flag, ./modelrouter.yaml, or ~/.config."""
    if explicit:
        return explicit
    for candidate in (
        "modelrouter.yaml",
        os.path.expanduser("~/.config/modelrouter/config.yaml"),
    ):
        if os.path.exists(candidate):
            return candidate
    return "modelrouter.yaml"  # load_config will report it missing
