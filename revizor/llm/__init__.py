"""Model layer: one interface, several providers, offline-safe failures.

    backend = make_backend(choose_provider(env), env)
    data, usage = backend.complete_json(system, user, schema=..., stage="review")

Environment values are read from the ``env`` mapping only (never from os.environ directly).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from .base import Backend, LLMBadOutput, LLMError, LLMRefused, LLMTransient, LLMUnavailable, Usage
from .cache import DiskCache
from .fake import FakeBackend
from .base import STAGE_EFFORT
from .pricing import stage_usage_rows

__all__ = [
    "Backend", "Usage", "LLMError", "LLMUnavailable", "LLMTransient", "LLMBadOutput", "LLMRefused",
    "FakeBackend", "STAGE_EFFORT", "stage_usage_rows", "choose_provider", "make_backend", "is_placeholder", "PROVIDERS", "DEFAULT_CACHE_DIR",
    "review_batch_size_for", "BATCH_SIZE_RANGE",
]

PROVIDERS = ("anthropic", "gemini", "openai", "ollama", "offline")
_ALIASES = {
    "claude": "anthropic", "google": "gemini", "openai-compatible": "openai", "openai_compat": "openai",
    "lmstudio": "openai", "lm-studio": "openai", "local": "ollama", "none": "offline", "rules": "offline",
    "off": "offline",
}
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "llm"

_PLACEHOLDER_RE = re.compile(
    r"^(?:your[_\-\s].*|.*[_\-]here|<.*>|\.\.\.+|…|x{3,}|changeme|change[_\-]me|placeholder|none|null|todo|"
    r"sk-\.\.\.|sk-x+|insert.*|paste.*|вставьте.*|ваш.*ключ.*)$",
    re.IGNORECASE,
)


def is_placeholder(value: str | None) -> bool:
    """Empty values and template values like ``your_api_key_here`` count as absent."""
    v = (value or "").strip().strip("'\"").strip()
    return not v or bool(_PLACEHOLDER_RE.match(v))


def _get(env: Mapping[str, str], *names: str) -> str:
    for n in names:
        v = env.get(n)
        if v is not None and not is_placeholder(v):
            return str(v).strip().strip("'\"").strip()
    return ""


def _int(env: Mapping[str, str], name: str, default: int | None, lo: int, hi: int) -> int | None:
    v = _get(env, name)
    if not v:
        return default
    try:
        return max(lo, min(hi, int(float(v))))
    except ValueError:
        return default


BATCH_SIZE_RANGE = (1, 200)


def _batch_size(env: Mapping[str, str]) -> int | None:
    """``LLM_BATCH_SIZE`` (1–200, clamped) or None when not set / not a number."""
    lo, hi = BATCH_SIZE_RANGE
    return _int(env, "LLM_BATCH_SIZE", None, lo, hi)


def review_batch_size_for(provider: str, env: Mapping[str, str]) -> int | None:
    """Requirements per review request for ``provider`` without building a backend (for the estimate).
    None means the agent default (prompts.BATCH_SIZE)."""
    bs = _batch_size(env)
    if bs:
        return bs
    p = normalize_provider(provider)
    if p in ("", "auto"):
        p = choose_provider(env)
    if p == "gemini":
        from .gemini_backend import REVIEW_BATCH_SIZE

        return REVIEW_BATCH_SIZE
    return None


def _fallback_model(env: Mapping[str, str], default: str) -> str:
    """``GEMINI_FALLBACK_MODEL``: absent → default; set but empty (or none/off) → feature off."""
    raw = env.get("GEMINI_FALLBACK_MODEL")
    if raw is None:
        return default
    v = str(raw).strip().strip("'\"").strip()
    if not v or v.lower() in ("off", "no", "0", "false") or is_placeholder(v):
        return ""
    return v


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    v = _get(env, name)
    try:
        return max(5.0, float(v)) if v else default
    except ValueError:
        return default


def normalize_provider(name: str) -> str:
    p = (name or "").strip().lower()
    return _ALIASES.get(p, p)


def choose_provider(env: Mapping[str, str]) -> str:
    """``LLM_PROVIDER`` if set (``auto`` means auto), else the first key found:
    ANTHROPIC_API_KEY → GEMINI_API_KEY / GOOGLE_API_KEY → OPENAI_API_KEY → OLLAMA_BASE_URL → offline.
    An unknown LLM_PROVIDER value is returned as is (make_backend then explains the mistake)."""
    forced = normalize_provider(_get(env, "LLM_PROVIDER"))
    if forced and forced != "auto":
        return forced
    if _get(env, "ANTHROPIC_API_KEY"):
        return "anthropic"
    if _get(env, "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        return "gemini"
    if _get(env, "OPENAI_API_KEY"):
        return "openai"
    if _get(env, "OLLAMA_BASE_URL"):
        return "ollama"
    return "offline"


def make_backend(provider: str, env: Mapping[str, str], *, model: str | None = None,
                 cache_dir: str | None = None, use_cache: bool = True) -> Backend:
    """Build the backend for ``provider``. Raises LLMUnavailable with a Russian reason.

    ``cache_dir=None`` uses ``<agent>/.cache/llm``; ``use_cache=False`` skips reading but still writes.
    No network call is made here: model discovery and key checks happen at the first call.
    """
    p = normalize_provider(provider)
    if p in ("", "auto"):
        p = choose_provider(env)
    if p == "offline":
        raise LLMUnavailable("Выбран режим без модели (offline).")
    if p not in PROVIDERS:
        raise LLMUnavailable("Неизвестный провайдер «%s». Допустимо: auto, %s." % (provider[:30], ", ".join(PROVIDERS)))

    cache = DiskCache(cache_dir if cache_dir else DEFAULT_CACHE_DIR)
    timeout_default = 900.0 if p == "ollama" else (600.0 if p == "anthropic" else 300.0)
    timeout = _float(env, "LLM_TIMEOUT", timeout_default)
    max_tokens_override = _int(env, "LLM_MAX_TOKENS", None, 256, 200000)

    backend = _make(p, provider, env, model=model, cache=cache, use_cache=use_cache, timeout=timeout,
                    max_tokens_override=max_tokens_override)
    bs = _batch_size(env)
    if bs:
        backend.review_batch_size = bs
    return backend


def _make(p: str, provider: str, env: Mapping[str, str], *, model: str | None, cache: DiskCache, use_cache: bool,
          timeout: float, max_tokens_override: int | None) -> Backend:
    common = dict(cache=cache, use_cache=use_cache)
    if p == "anthropic":
        from .anthropic_backend import DEFAULT_MODEL, AnthropicBackend

        key = _get(env, "ANTHROPIC_API_KEY")
        if not key:
            raise LLMUnavailable("Не задан ключ ANTHROPIC_API_KEY (или в .env осталось значение-шаблон). "
                                 "Вставьте ключ из console.anthropic.com → API Keys или запустите с --provider offline.")
        return AnthropicBackend(
            key, model=model or _get(env, "ANTHROPIC_MODEL") or DEFAULT_MODEL,
            effort=_get(env, "ANTHROPIC_EFFORT"),
            stage_overrides=_get(env, "ANTHROPIC_EFFORT_STAGES"),
            base_url=_get(env, "ANTHROPIC_BASE_URL") or None, timeout=timeout,
            concurrency=_int(env, "LLM_CONCURRENCY", 4, 1, 32) or 4,
            max_tokens_override=max_tokens_override, **common)

    if p == "gemini":
        from .gemini_backend import (DEFAULT_BASE_URL, DEFAULT_MODEL as GM, FALLBACK_MODEL, KEY_HELP, OVERLOAD_WAIT_S,
                                     GeminiBackend)

        key = _get(env, "GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not key:
            raise LLMUnavailable("Не задан ключ GEMINI_API_KEY (или в .env осталось значение-шаблон). " + KEY_HELP)
        return GeminiBackend(
            key, model=model or _get(env, "GEMINI_MODEL") or GM,
            base_url=_get(env, "GEMINI_BASE_URL") or DEFAULT_BASE_URL, timeout=timeout,
            concurrency=_int(env, "LLM_CONCURRENCY", 1, 1, 32) or 1,
            max_tokens_override=max_tokens_override, fallback_model=_fallback_model(env, FALLBACK_MODEL),
            overload_wait_s=_int(env, "GEMINI_OVERLOAD_WAIT_S", int(OVERLOAD_WAIT_S), 0, 3600), **common)

    from .openai_compat_backend import DEFAULT_MODEL as OM, OLLAMA_BASE_URL, OPENAI_BASE_URL, OpenAICompatBackend

    if p == "openai":
        base = _get(env, "OPENAI_BASE_URL") or OPENAI_BASE_URL
        key = _get(env, "OPENAI_API_KEY")
        if not key and "api.openai.com" in base:
            raise LLMUnavailable("Не задан ключ OPENAI_API_KEY (или в .env осталось значение-шаблон). Вставьте ключ "
                                 "или укажите OPENAI_BASE_URL локального сервера.")
        return OpenAICompatBackend(
            provider="openai", model=model or _get(env, "OPENAI_MODEL") or OM, base_url=base, api_key=key,
            timeout=timeout, concurrency=_int(env, "LLM_CONCURRENCY", 4, 1, 32) or 4,
            max_tokens_override=max_tokens_override, **common)

    # ollama
    return OpenAICompatBackend(
        provider="ollama", model=model or _get(env, "OLLAMA_MODEL"),
        base_url=_get(env, "OLLAMA_BASE_URL") or OLLAMA_BASE_URL, api_key="",
        timeout=timeout, concurrency=_int(env, "LLM_CONCURRENCY", 1, 1, 32) or 1,
        max_tokens_override=max_tokens_override, **common)
