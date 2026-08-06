"""Estimate USD cost from Cursor token buckets using published list rates."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

PRICING_FILENAMES = ("pricing.json",)
SEARCH_DIRS = (
    Path(__file__).resolve().parent,
    Path.home() / ".cursor" / "plugins" / "token-usage",
    Path.home() / ".cursor" / "hooks",
)


def pricing_enabled() -> bool:
    raw = os.environ.get("TOKEN_USAGE_PRICING", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def cursor_token_rate_enabled() -> bool:
    raw = os.environ.get("TOKEN_USAGE_CURSOR_TOKEN_RATE", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def default_rate_key() -> str:
    raw = os.environ.get("TOKEN_USAGE_DEFAULT_RATE", "auto_cost").strip().lower()
    if raw in {"0", "false", "no", "off", "none", ""}:
        return ""
    return raw


def _find_pricing_path() -> Optional[Path]:
    env = os.environ.get("TOKEN_USAGE_PRICING_PATH", "").strip()
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return path
    for directory in SEARCH_DIRS:
        for name in PRICING_FILENAMES:
            path = directory / name
            if path.is_file():
                return path
    return None


@lru_cache(maxsize=1)
def load_pricing() -> dict[str, Any]:
    path = _find_pricing_path()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def clear_pricing_cache() -> None:
    load_pricing.cache_clear()


def _norm(value: Optional[str]) -> str:
    if not value:
        return ""
    text = value.strip().lower().replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    return text


def resolve_model_key(model: Optional[str], model_id: Optional[str] = None) -> str:
    pricing = load_pricing()
    models = pricing.get("models") if isinstance(pricing.get("models"), dict) else {}
    aliases = pricing.get("aliases") if isinstance(pricing.get("aliases"), dict) else {}

    for candidate in (model_id, model):
        key = _norm(candidate)
        if not key:
            continue
        if key in models:
            return key
        if key in aliases:
            return str(aliases[key])
        # Strip common prefixes / suffixes for fuzzy match.
        stripped = key
        for prefix in ("cursor-", "anthropic-", "openai-", "google-"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :]
        for suffix in ("-thinking", "-high", "-medium", "-low", "-max"):
            if stripped.endswith(suffix) and stripped[: -len(suffix)] in models:
                return stripped[: -len(suffix)]
        if stripped in models:
            return stripped
        if stripped in aliases:
            return str(aliases[stripped])

    fallback = default_rate_key()
    if fallback and (fallback in models or fallback in aliases):
        return str(aliases.get(fallback, fallback))
    return ""


def _model_rate_entry(model_key: str) -> Optional[dict[str, Any]]:
    pricing = load_pricing()
    models = pricing.get("models") if isinstance(pricing.get("models"), dict) else {}
    if model_key in models and isinstance(models[model_key], dict):
        return models[model_key]
    if model_key == "auto_cost" and isinstance(pricing.get("auto_cost"), dict):
        return pricing["auto_cost"]
    return None


def _as_rate(value: Any, *, fallback: Optional[float] = None) -> Optional[float]:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _bucket_cost(tokens: int, rate_per_m: Optional[float]) -> float:
    if not tokens or rate_per_m is None:
        return 0.0
    return (int(tokens) / 1_000_000.0) * float(rate_per_m)


def estimate_turn_cost(
    turn: dict[str, Any],
    *,
    model: Optional[str] = None,
    model_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return estimate dict for a turn, or None if pricing is disabled/unknown."""
    if not pricing_enabled():
        return None
    if not load_pricing():
        return None

    model_name = model if model is not None else turn.get("model")
    model_id_name = model_id if model_id is not None else turn.get("model_id")
    model_key = resolve_model_key(
        model_name if isinstance(model_name, str) else None,
        model_id_name if isinstance(model_id_name, str) else None,
    )
    if not model_key:
        return {
            "usd": None,
            "known": False,
            "model_key": None,
            "label": "unknown",
            "reason": "no matching rate",
        }

    rates = _model_rate_entry(model_key)
    if not rates:
        return {
            "usd": None,
            "known": False,
            "model_key": model_key,
            "label": model_key,
            "reason": "missing rate entry",
        }

    if rates.get("public_list_rate") is False or rates.get("pool") == "cursor_models":
        return {
            "usd": None,
            "known": False,
            "model_key": model_key,
            "label": rates.get("label") or model_key,
            "pool": rates.get("pool"),
            "reason": "Cursor Models pool (no public $/token list rate)",
        }

    input_tokens = int(turn.get("input_tokens") or 0)
    output_tokens = int(turn.get("output_tokens") or 0)
    cache_read = int(turn.get("cache_read_tokens") or 0)
    cache_write = int(turn.get("cache_write_tokens") or 0)

    # Some turns only store prompt_tokens + output.
    if input_tokens == 0 and cache_read == 0 and cache_write == 0:
        prompt = int(turn.get("prompt_tokens") or 0)
        if prompt:
            input_tokens = prompt

    input_rate = _as_rate(rates.get("input"))
    cache_write_rate = _as_rate(rates.get("cache_write"), fallback=None)
    cache_read_rate = _as_rate(rates.get("cache_read"), fallback=None)
    output_rate = _as_rate(rates.get("output"))

    if input_rate is None and output_rate is None:
        return {
            "usd": None,
            "known": False,
            "model_key": model_key,
            "label": rates.get("label") or model_key,
            "reason": "incomplete rates",
        }

    parts = {
        "input": _bucket_cost(input_tokens, input_rate),
        "cache_write": _bucket_cost(cache_write, cache_write_rate),
        "cache_read": _bucket_cost(cache_read, cache_read_rate),
        "output": _bucket_cost(output_tokens, output_rate),
    }
    subtotal = sum(parts.values())

    ctr = 0.0
    pool = rates.get("pool")
    if cursor_token_rate_enabled() and pool == "other":
        pricing = load_pricing()
        ctr_rate = _as_rate(pricing.get("cursor_token_rate_per_m"), fallback=0.25) or 0.0
        token_sum = input_tokens + output_tokens + cache_read + cache_write
        ctr = _bucket_cost(token_sum, ctr_rate)
    total = subtotal + ctr

    return {
        "usd": round(total, 6),
        "subtotal_usd": round(subtotal, 6),
        "cursor_token_rate_usd": round(ctr, 6),
        "parts_usd": {k: round(v, 6) for k, v in parts.items()},
        "known": True,
        "model_key": model_key,
        "label": rates.get("label") or model_key,
        "pool": pool,
        "rates_per_m": {
            "input": input_rate,
            "cache_write": cache_write_rate,
            "cache_read": cache_read_rate,
            "output": output_rate,
        },
        "assumed_default": _norm(model_name) in {"", "default", "auto"}
        or _norm(model_id_name) in {"", "default", "auto"},
    }


def estimate_turns_cost(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate turn estimates. Unknown turns are skipped for USD total."""
    total = 0.0
    known_turns = 0
    unknown_turns = 0
    last_meta: Optional[dict[str, Any]] = None
    for turn in turns:
        est = estimate_turn_cost(turn)
        if est is None:
            continue
        last_meta = est
        if est.get("known") and est.get("usd") is not None:
            total += float(est["usd"])
            known_turns += 1
        else:
            unknown_turns += 1
    known = known_turns > 0
    return {
        "usd": round(total, 6) if known else None,
        "known": known,
        "known_turns": known_turns,
        "unknown_turns": unknown_turns,
        "label": (last_meta or {}).get("label"),
        "model_key": (last_meta or {}).get("model_key"),
    }


def fmt_usd(value: Optional[float], *, compact: bool = False) -> str:
    if value is None:
        return "-"
    amount = float(value)
    if compact:
        if abs(amount) >= 100:
            return f"${amount:,.0f}"
        if abs(amount) >= 10:
            return f"${amount:,.1f}"
        if abs(amount) >= 1:
            return f"${amount:,.2f}"
        if abs(amount) >= 0.01:
            return f"${amount:,.2f}"
        if abs(amount) > 0:
            return f"${amount:,.3f}"
        return "$0"
    return f"${amount:,.4f}".rstrip("0").rstrip(".") if abs(amount) < 0.01 else f"${amount:,.2f}"


def cost_suffix(est: Optional[dict[str, Any]], *, compact: bool = False) -> str:
    if not est or not est.get("known") or est.get("usd") is None:
        return ""
    prefix = "~" if est.get("assumed_default") else "~"
    return f" · {prefix}{fmt_usd(float(est['usd']), compact=compact)}"
