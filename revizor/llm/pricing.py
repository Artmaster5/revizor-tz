"""Run cost in USD from token counts."""
from __future__ import annotations

import re

from .base import Usage

# USD per 1M tokens: input, output, cache read, cache write.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-fable-5-2": (8.0, 40.0, 0.20, 10.0),
    # OpenAI default model (cached input 50 %; OpenAI has no cache-write charge).
    "gpt-4o-mini": (0.15, 0.60, 0.075, 0.0),
}

FREE_PROVIDERS = {"gemini": "бесплатный тариф", "ollama": "локальная модель"}


def _price(model: str) -> tuple[str, tuple[float, float, float, float]] | None:
    m = (model or "").strip().lower()
    if m in PRICES:
        return m, PRICES[m]
    # Dated snapshot of a known model: "claude-opus-5-5-20261101", "gpt-4o-mini-2024-07-18".
    for name, price in PRICES.items():
        if m.startswith(name + "-") and re.fullmatch(r"[0-9-]+", m[len(name) + 1:]):
            return name, price
    return None


def cost_usd(provider: str, model: str, totals: Usage) -> float | None:
    if provider in FREE_PROVIDERS or provider == "fake":
        return 0.0
    p = _price(model)
    if p is None:
        return None
    pin, pout, pread, pwrite = p[1]
    total = (totals.input_tokens * pin + totals.output_tokens * pout
             + totals.cache_read * pread + totals.cache_write * pwrite) / 1_000_000
    return round(total, 6)


def _fmt(x: float) -> str:
    s = ("%.3f" % x).rstrip("0").rstrip(".")
    return "$" + s


def cost_note(provider: str, model: str) -> str:
    if provider in FREE_PROVIDERS:
        return FREE_PROVIDERS[provider]
    if provider == "fake":
        return "тестовый сценарий, модель не вызывалась"
    p = _price(model)
    if p is None:
        return "цена модели неизвестна"
    name, (pin, pout, _r, _w) = p
    return "по тарифу %s: %s / %s за 1 млн токенов (вход / выход)" % (name, _fmt(pin), _fmt(pout))


def stage_usage_rows(backend) -> list[dict]:
    """Per-stage usage of a finished run, in first-seen order of stage names.

    Keys: stage, calls, input_tokens, output_tokens, cache_read, cache_write, seconds, cost_usd
    (None when the model price is unknown)."""
    with backend._lock:
        items = [(k, Usage(**{f: getattr(v, f) for f in ("input_tokens", "output_tokens", "cache_read",
                                                         "cache_write", "seconds", "calls")}))
                 for k, v in backend.stage_usage.items()]
    rows = []
    for stage, u in items:
        rows.append({"stage": stage, "calls": u.calls, "input_tokens": u.input_tokens,
                     "output_tokens": u.output_tokens, "cache_read": u.cache_read,
                     "cache_write": u.cache_write, "seconds": round(u.seconds, 2),
                     "cost_usd": cost_usd(backend.provider, backend.model, u)})
    return rows
