"""Token cost estimation for harnesses that report tokens but not money.

Only Claude Code reports `total_cost_usd` directly. Codex and opencode report token counts, and
opencode reports `cost: 0` outright when authenticated against a subscription rather than an API
key — because for a subscription the marginal cost of a turn genuinely *is* zero.

So we do not ship invented per-model prices. Token counts are always recorded exactly; dollar
figures appear only when the harness reports one, or when you supply rates yourself:

    # ~/.vorflux/config.toml
    [pricing."gpt-5-codex"]
    input = 1.25        # USD per million input tokens
    output = 10.0
    cache_read = 0.125

A missing rate yields `cost_usd = None`, which the UI shows as "—" rather than a confident zero.
Silently guessing would be worse than admitting we don't know.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from vorflux.config import HOME


@dataclass(frozen=True)
class Rate:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


_cache: dict[str, Rate] | None = None


def rates() -> dict[str, Rate]:
    global _cache
    if _cache is not None:
        return _cache
    table: dict[str, Rate] = {}
    path = HOME / "config.toml"
    if path.exists():
        data = tomllib.loads(path.read_text()).get("pricing", {}) or {}
        for model, spec in data.items():
            if isinstance(spec, dict):
                table[model] = Rate(
                    input=float(spec.get("input", 0.0)),
                    output=float(spec.get("output", 0.0)),
                    cache_read=float(spec.get("cache_read", 0.0)),
                    cache_write=float(spec.get("cache_write", 0.0)),
                )
    _cache = table
    return table


def estimate(
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """USD for these token counts, or None when no rate is configured for the model."""
    if not model:
        return None
    rate = rates().get(model)
    if rate is None:
        return None
    per_million = (
        input_tokens * rate.input
        + output_tokens * rate.output
        + cache_read_tokens * rate.cache_read
        + cache_write_tokens * rate.cache_write
    )
    return per_million / 1_000_000
