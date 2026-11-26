"""Claude through the official ``anthropic`` SDK (imported lazily).

Request shape (checked against anthropic 2.14.0 and the claude-api skill reference):
  client.beta.messages.stream(model, max_tokens, system=[one text block with cache_control
  ephemeral], messages=[user], thinking={"type": "adaptive"} for families that support it,
  output_config={"effort": …}, and for opus-5 / fable-5 / sonnet-5-5 the server-side refusal
  fallback: betas=["server-side-fallback-2026-07-01"], fallbacks="default").
No temperature / top_p / top_k (rejected by current models) and no per-stage
output_config.format: every stage shares one cached system prefix.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .base import (EFFORT_VALUES, STAGE_EFFORT_NOTE, Backend, LLMBadOutput, LLMRefused, LLMTransient, LLMUnavailable,
                   Usage, parse_retry_after, parse_stage_overrides, register_secret)

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_BASE_URL = "https://api.anthropic.com"
EFFORTS = EFFORT_VALUES
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_FAMILIES = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")  # not Haiku 5.5: no fallback there
ADAPTIVE_FAMILIES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5", "claude-sonnet-5", "claude-haiku-5",
                     "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-4-6")
KEY_HELP = ("Проверьте ANTHROPIC_API_KEY в .env (ключ берётся в console.anthropic.com → API Keys) "
            "или запустите агент без модели: --provider offline.")
_REFUSAL_RU = {"cyber": "кибербезопасность", "bio": "биология", "frontier_llm": "разработка ИИ-моделей",
               "reasoning_extraction": "раскрытие рассуждений", "general_harms": "общие риски"}


def _refused_connection(exc: BaseException | None) -> bool:
    seen = 0
    while exc is not None and seen < 8:
        if isinstance(exc, ConnectionRefusedError) or "connection refused" in str(exc).lower() \
                or "errno 61" in str(exc).lower() or "errno 111" in str(exc).lower():
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


class AnthropicBackend(Backend):
    provider = "anthropic"

    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, effort: str = "",
                 stage_overrides: str = "", base_url: str | None = None, timeout: float = 600.0, concurrency: int = 4,
                 max_tokens_floor: int = 32000, max_tokens_override: int | None = None,
                 sdk_max_retries: int = 2, http_client: Any = None, cache: Any = None,
                 use_cache: bool = True, max_attempts: int = 4) -> None:
        try:
            import anthropic
        except ImportError:
            raise LLMUnavailable("Не установлен пакет anthropic, нужный для Claude. Установите зависимости: "
                                 "pip install -r requirements.txt") from None
        if not api_key:
            raise LLMUnavailable("Не задан ключ ANTHROPIC_API_KEY. " + KEY_HELP)
        eff = (effort or "").strip().lower()
        super().__init__(model=model or DEFAULT_MODEL, effort=eff, concurrency=concurrency, cache=cache,
                         use_cache=use_cache, max_attempts=max_attempts)
        # An explicit effort is locked for every stage; empty means «per stage» (self.effort = fallback).
        self.effort_locked = bool(eff)
        if eff and eff not in EFFORTS:
            self.add_note("ANTHROPIC_EFFORT=%s не распознан (допустимо: %s) — использую усилие по этапам."
                          % (eff[:20], ", ".join(EFFORTS)))
            self.effort_locked = False
        if not self.effort_locked:
            self.effort = DEFAULT_EFFORT
            if stage_overrides and stage_overrides.strip():
                self.stage_overrides, bad = parse_stage_overrides(stage_overrides)
                if bad:
                    self.add_note("ANTHROPIC_EFFORT_STAGES: не распознано «%s» — нужен вид review=low,set=high "
                                  "(значения: %s)." % (", ".join(bad[:5]), ", ".join(EFFORTS)))
            self.add_note(STAGE_EFFORT_NOTE)
        register_secret(api_key)
        self._sdk = anthropic
        self.max_tokens_floor = max_tokens_floor
        self.max_tokens_override = max_tokens_override
        self._use_fallback = self.model.startswith(FALLBACK_FAMILIES)
        self._use_thinking = self.model.startswith(ADAPTIVE_FAMILIES)
        self._use_effort = True
        self._feature_lock = threading.Lock()
        kwargs: dict[str, Any] = dict(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL, timeout=timeout,
                                      max_retries=sdk_max_retries)
        if http_client is not None:
            kwargs["http_client"] = http_client
        self.client = anthropic.Anthropic(**kwargs)

    # -- request -------------------------------------------------------------
    def build_request(self, system: str, user: str, max_tokens: int, effort: str | None = None) -> dict[str, Any]:
        n = self.max_tokens_override or max(max_tokens, self.max_tokens_floor)
        kw: dict[str, Any] = {
            "model": self.model,
            "max_tokens": n,
            # One block, one breakpoint: the whole system prompt (rules + document) is the shared prefix.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if self._use_thinking:
            kw["thinking"] = {"type": "adaptive"}
        eff = effort or self.effort
        if self._use_effort and eff:
            kw["output_config"] = {"effort": eff}
        if self._use_fallback:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    def effort_applies(self) -> bool:
        return self._use_effort and self._raw_takes_effort

    def _droppable(self, err: Any, kw: dict[str, Any]) -> str:
        """Which optional feature a 400 complains about (it is then switched off and retried once)."""
        text = ("%s %s" % (getattr(err, "message", ""), getattr(err, "body", ""))).lower()
        if "fallbacks" in kw and ("anthropic-beta" in text or "fallback" in text):
            return "fallback"
        if "output_config" in kw and "effort" in text:
            return "effort"
        if "thinking" in kw and "thinking" in text:
            return "thinking"
        return ""

    def _drop(self, feature: str) -> None:
        with self._feature_lock:
            if feature == "fallback" and self._use_fallback:
                self._use_fallback = False
                self.add_note("API не принял серверную запасную модель при отказе (beta %s) — запросы идут "
                              "без неё." % FALLBACK_BETA)
            elif feature == "effort" and self._use_effort:
                self._use_effort = False
                self.add_note("Модель %s не принимает параметр effort — он отключён." % self.model)
            elif feature == "thinking" and self._use_thinking:
                self._use_thinking = False
                self.add_note("Модель %s не принимает адаптивное мышление — параметр отключён." % self.model)

    # -- call ----------------------------------------------------------------
    def _raw(self, system: str, user: str, max_tokens: int, *, effort: str | None = None) -> tuple[str, Usage]:
        a = self._sdk
        msg = None
        for _ in range(4):
            kw = self.build_request(system, user, max_tokens, effort)
            t0 = time.monotonic()
            try:
                # Streaming + the final-message helper: no HTTP timeout on long answers.
                with self.client.beta.messages.stream(**kw) as stream:
                    msg = stream.get_final_message()
                break
            except a.BadRequestError as e:
                feature = self._droppable(e, kw)
                if feature:
                    self._drop(feature)
                    continue
                raise self._map_error(e) from None
            except a.AnthropicError as e:
                raise self._map_error(e) from None
        if msg is None:
            raise LLMUnavailable("Claude API отклонил запрос даже без необязательных параметров.")
        seconds = time.monotonic() - t0
        return self._read(msg, kw["max_tokens"], seconds)

    def _read(self, msg: Any, max_tokens: int, seconds: float) -> tuple[str, Usage]:
        u = getattr(msg, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            cache_read=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_write=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
            seconds=seconds,
        )
        iterations = getattr(u, "iterations", None) or []
        if any(getattr(it, "type", "") == "fallback_message" for it in iterations):
            self.add_note("Часть ответов дала запасная модель %s: основная модель отказалась отвечать."
                          % getattr(msg, "model", "?"))
        stop = getattr(msg, "stop_reason", None)
        if stop == "refusal":
            details = getattr(msg, "stop_details", None)
            cat = getattr(details, "category", None) if details is not None else None
            e = LLMRefused("Модель %s отказалась отвечать на запрос%s. Этот пакет останется непроверенным моделью."
                           % (self.model, " (категория: %s)" % _REFUSAL_RU.get(cat, cat) if cat else ""),
                           detail=str(getattr(details, "explanation", "") or ""))
            e.usage = usage
            raise e
        if stop in ("max_tokens", "model_context_window_exceeded"):
            what = ("не хватило лимита ответа (%d токенов)" % max_tokens if stop == "max_tokens"
                    else "переполнено окно контекста модели")
            e2 = LLMBadOutput("Ответ модели обрезан: %s. Уменьшите пакет или задайте LLM_MAX_TOKENS." % what)
            if stop == "max_tokens":
                e2.kind = "truncated"
                e2.limit = max_tokens
            e2.usage = usage
            raise e2
        text = "".join(getattr(b, "text", "") or "" for b in (getattr(msg, "content", None) or [])
                       if getattr(b, "type", "") == "text")
        if not text.strip():
            e3 = LLMBadOutput("Модель вернула ответ без текста (stop_reason=%s)." % stop)
            e3.usage = usage
            raise e3
        return text, usage

    # -- errors: most specific first (shared/error-codes.md) -------------------
    def _map_error(self, e: Any) -> Exception:
        a = self._sdk
        detail = str(getattr(e, "message", "") or e)
        status = getattr(e, "status_code", None)
        spend_caps = tuple(c for c in (getattr(a, "EnforcedSpendLimitReachedError", None),
                                       getattr(a, "ContractSpendLimitReachedError", None)) if c is not None)
        if isinstance(e, a.AuthenticationError):
            return LLMUnavailable("Ключ ANTHROPIC_API_KEY не принят (401). " + KEY_HELP, detail=detail)
        if isinstance(e, a.PermissionDeniedError):
            return LLMUnavailable("У ключа нет доступа к модели %s (403). Проверьте настройки организации в "
                                  "console.anthropic.com или укажите другую модель в ANTHROPIC_MODEL." % self.model,
                                  detail=detail)
        if isinstance(e, a.NotFoundError):
            return LLMUnavailable("Модель %s не найдена или недоступна вашему ключу (404). Укажите в .env "
                                  "ANTHROPIC_MODEL=%s." % (self.model, DEFAULT_MODEL), detail=detail)
        payment = getattr(a, "PaymentRequiredError", None)
        if payment is not None and isinstance(e, payment):
            return LLMUnavailable("Claude API: проблема с оплатой аккаунта (402). Пополните баланс в "
                                  "console.anthropic.com → Billing.", detail=detail)
        if spend_caps and isinstance(e, spend_caps):
            return LLMUnavailable("Достигнут лимит расходов аккаунта Claude API: повтор не поможет до сброса "
                                  "лимита. Поднимите лимит в console.anthropic.com.", detail=detail)
        if isinstance(e, a.RateLimitError):
            return LLMTransient("Claude API: превышен лимит запросов (429).",
                                retry_after=parse_retry_after(getattr(getattr(e, "response", None), "headers", None)),
                                kind="rate", detail=detail)
        if isinstance(e, a.BadRequestError):
            if "prompt is too long" in detail.lower():
                return LLMUnavailable("Документ слишком длинный для модели %s: запрос не помещается в контекст."
                                      % self.model, detail=detail)
            return LLMUnavailable("Claude API отклонил запрос (400): %s" % detail[:200], detail=detail)
        if isinstance(e, a.InternalServerError):  # 500, 529 overloaded, 503, 504
            return LLMTransient("Claude API: временная ошибка или перегрузка сервера (%s)." % status,
                                retry_after=parse_retry_after(getattr(getattr(e, "response", None), "headers", None)),
                                kind="server", detail=detail)
        if isinstance(e, a.APIStatusError):
            if status in (408, 409) or (status or 0) >= 500:
                return LLMTransient("Claude API: временная ошибка (%s)." % status, kind="server", detail=detail)
            return LLMUnavailable("Claude API ответил ошибкой %s: %s" % (status, detail[:200]), detail=detail)
        if isinstance(e, a.APITimeoutError):  # subclass of APIConnectionError: check first
            return LLMTransient("Claude API не ответил вовремя (тайм-аут).", kind="timeout", detail=detail)
        if isinstance(e, a.APIConnectionError):
            if _refused_connection(e):
                return LLMUnavailable("Не удалось подключиться к Claude API: соединение отклонено. Проверьте "
                                      "интернет и ANTHROPIC_BASE_URL.", kind="network", detail=detail)
            return LLMTransient("Нет связи с Claude API.", kind="network", detail=detail)
        return LLMUnavailable("Ошибка Claude SDK (%s): %s" % (type(e).__name__, detail[:200]), detail=detail)
