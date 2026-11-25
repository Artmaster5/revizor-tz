"""Any OpenAI-compatible chat-completions server (OpenAI, Ollama, LM Studio, vLLM…) over urllib."""
from __future__ import annotations

import threading
import time
from typing import Any

from ._http import HttpResult, error_message, request_json
from .base import (Backend, LLMBadOutput, LLMRefused, LLMTransient, LLMUnavailable, Usage, parse_retry_after,
                   register_secret)

OPENAI_BASE_URL = "https://api.openai.com/v1"
OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "gpt-4o-mini"


def normalize_base_url(url: str, *, ollama: bool = False) -> str:
    u = (url or "").strip().rstrip("/")
    for tail in ("/chat/completions", "/completions"):
        if u.endswith(tail):
            u = u[: -len(tail)]
    if ollama and not u.endswith("/v1"):
        u += "/v1"
    return u


def _is_local(url: str) -> bool:
    return any(h in url for h in ("://localhost", "://127.0.0.1", "://[::1]", "://0.0.0.0"))


class OpenAICompatBackend(Backend):
    def __init__(self, *, provider: str = "openai", model: str = DEFAULT_MODEL, base_url: str = OPENAI_BASE_URL,
                 api_key: str = "", timeout: float = 300.0, concurrency: int = 4,
                 max_tokens_override: int | None = None, cache: Any = None, use_cache: bool = True,
                 max_attempts: int = 4) -> None:
        super().__init__(model=model or "", effort="", concurrency=concurrency, cache=cache,
                         use_cache=use_cache, max_attempts=max_attempts)
        self.provider = provider
        self.base_url = normalize_base_url(base_url or OPENAI_BASE_URL, ollama=(provider == "ollama"))
        self._key = api_key or ""
        register_secret(self._key)
        self.timeout = timeout
        self.max_tokens_override = max_tokens_override
        self.local = _is_local(self.base_url)
        self._json_mode = True
        # The official API wants max_completion_tokens; most other servers know only max_tokens.
        self._token_param = "max_completion_tokens" if "api.openai.com" in self.base_url else "max_tokens"
        self._token_cap: int | None = 16384 if self.model.startswith("gpt-4o") else None
        self._model_lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self._key} if self._key else {}

    def _what(self) -> str:
        return "Ollama" if self.provider == "ollama" else ("локальный сервер модели" if self.local else "OpenAI-совместимый сервер")

    def _ensure_model(self) -> None:
        if self.model:
            return
        with self._model_lock:
            if self.model:
                return
            res = request_json("GET", self.base_url + "/models", headers=self._headers(), timeout=min(self.timeout, 30.0))
            ids = []
            if res.status == 200 and isinstance(res.data, dict):
                ids = [str(m.get("id")) for m in res.data.get("data") or [] if isinstance(m, dict) and m.get("id")]
            ids = [i for i in ids if "embed" not in i.lower()]
            if not ids:
                var = "OLLAMA_MODEL" if self.provider == "ollama" else "OPENAI_MODEL"
                raise LLMUnavailable("%s по адресу %s не сообщил ни одной загруженной модели. Загрузите модель "
                                     "(например, `ollama pull qwen3:8b`) и укажите её в %s." % (self._what(), self.base_url, var))
            self.model = ids[0]
            self.add_note("Имя модели не задано — использую первую доступную на сервере: %s." % self.model)

    def build_body(self, system: str, user: str, max_tokens: int) -> dict:
        n = self.max_tokens_override or max_tokens
        if self._token_cap:
            n = min(n, self._token_cap)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            self._token_param: n,
            "stream": False,
        }
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def _raw(self, system: str, user: str, max_tokens: int, *, effort: str | None = None) -> tuple[str, Usage]:
        self._ensure_model()
        for _ in range(4):
            t0 = time.monotonic()
            res = request_json("POST", self.base_url + "/chat/completions", headers=self._headers(),
                               body=self.build_body(system, user, max_tokens), timeout=self.timeout)
            if res.status == 200:
                return self._parse(res, time.monotonic() - t0)
            msg = error_message(res)
            low = msg.lower() + " " + res.text[:2000].lower()
            if res.status in (400, 422):
                if self._json_mode and "response_format" in low:
                    self._json_mode = False
                    self.add_note("Сервер не поддерживает режим JSON (response_format) — форма ответа задаётся "
                                  "только текстом запроса.")
                    continue
                other = "max_tokens" if self._token_param == "max_completion_tokens" else "max_completion_tokens"
                if "unsupported parameter" in low and self._token_param in low:
                    self._token_param = other
                    continue
                if ("max_tokens" in low or "max_completion_tokens" in low or "maximum" in low and "tokens" in low) \
                        and (self._token_cap or 1 << 30) > 4096:
                    self._token_cap = 4096
                    self.add_note("Сервер не принял лимит ответа — лимит снижен до 4096 токенов.")
                    continue
                if "context length" in low or "context_length" in low or "too long" in low:
                    raise LLMUnavailable("Запрос не помещается в контекст модели %s. Возьмите модель с большим "
                                         "контекстом." % self.model, detail=msg)
                raise LLMUnavailable("%s отклонил запрос (%d): %s" % (self._what(), res.status, msg[:200]), detail=msg)
            raise self._map_status(res, msg, low)
        raise LLMUnavailable("%s: не удалось подобрать рабочие параметры запроса." % self._what())

    def _map_status(self, res: HttpResult, msg: str, low: str) -> Exception:
        st = res.status
        if st == 401:
            return LLMUnavailable("Ключ OPENAI_API_KEY не принят (401). Проверьте ключ в .env "
                                  "(platform.openai.com → API keys) или запустите с --provider offline.", detail=msg)
        if st == 403:
            return LLMUnavailable("У ключа нет доступа к модели %s (403). Проверьте права ключа или укажите другую "
                                  "модель." % self.model, detail=msg)
        if st == 404:
            if self.provider == "ollama":
                return LLMUnavailable("Ollama не знает модель «%s». Выполните `ollama pull %s` или укажите "
                                      "OLLAMA_MODEL." % (self.model, self.model), detail=msg)
            return LLMUnavailable("Модель «%s» не найдена на %s (404). Укажите правильное имя в OPENAI_MODEL "
                                  "или адрес в OPENAI_BASE_URL." % (self.model, self.base_url), detail=msg)
        if st == 429:
            if "insufficient_quota" in low or "billing" in low:
                return LLMUnavailable("На счёте OpenAI закончились средства или не подключена оплата (429 "
                                      "insufficient_quota). Пополните баланс или используйте другой ключ.", detail=msg)
            return LLMTransient("%s: превышен лимит запросов (429)." % self._what(),
                                retry_after=parse_retry_after(res.headers), kind="rate", detail=msg)
        if st in (408, 409) or st >= 500:
            return LLMTransient("%s: временная ошибка сервера (%d)." % (self._what(), st),
                                retry_after=parse_retry_after(res.headers), kind="server", detail=msg)
        return LLMUnavailable("%s ответил ошибкой %d: %s" % (self._what(), st, msg[:200]), detail=msg)

    def _parse(self, res: HttpResult, seconds: float) -> tuple[str, Usage]:
        d = res.data if isinstance(res.data, dict) else {}
        u = d.get("usage") or {}
        cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        usage = Usage(input_tokens=max(0, int(u.get("prompt_tokens") or 0) - cached),
                      output_tokens=int(u.get("completion_tokens") or 0), cache_read=cached, seconds=seconds)
        try:
            choice = d["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError, AttributeError):
            e = LLMBadOutput("%s вернул ответ непонятной формы (нет choices)." % self._what())
            e.usage = usage
            raise e from None
        finish = choice.get("finish_reason")
        if message.get("refusal") or finish == "content_filter":
            e2 = LLMRefused("Модель %s отказалась отвечать на запрос." % self.model,
                            detail=str(message.get("refusal") or finish))
            e2.usage = usage
            raise e2
        if finish == "length":
            e3 = LLMBadOutput("Ответ модели %s обрезан по лимиту токенов. Уменьшите пакет или задайте больший "
                              "LLM_MAX_TOKENS." % self.model)
            e3.kind = "truncated"
            e3.usage = usage
            raise e3
        text = message.get("content") or ""
        if isinstance(text, list):  # some servers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        if not str(text).strip():
            e4 = LLMBadOutput("Модель %s вернула пустой ответ." % self.model)
            e4.usage = usage
            raise e4
        return str(text), usage
