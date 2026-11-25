"""Google Gemini through the REST API (stdlib urllib). The key goes in ``x-goog-api-key``."""
from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import quote

from ._http import HttpResult, error_message, request_json
from .base import (Backend, LLMBadOutput, LLMRefused, LLMTransient, LLMUnavailable, Usage, parse_retry_after,
                   register_secret)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-flash-latest"
KEY_HELP = ("Получите бесплатный ключ: aistudio.google.com → Get API key, и вставьте его в .env "
            "как GEMINI_API_KEY=… Без ключа запустите агент с --provider offline.")
_EXCLUDE = ("lite", "image", "tts", "live", "embedding", "audio", "8b", "computer-use", "robotics", "learnlm")
_REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}


def _strip_models(name: str) -> str:
    return name[len("models/"):] if name.startswith("models/") else name


def rank_model(name: str) -> tuple | None:
    """Sort key for a replacement model (bigger is better) or None when unsuitable."""
    low = name.lower()
    if "flash" not in low or any(x in low for x in _EXCLUDE):
        return None
    alias = 1 if low == "gemini-flash-latest" else 0
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", low)
    ver = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    stable = 0 if any(x in low for x in ("preview", "exp")) else 1
    return (alias, ver, stable, -len(low))


def pick_model(models: list[dict]) -> str | None:
    best, best_key = None, None
    for m in models:
        if not isinstance(m, dict):
            continue
        methods = m.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        name = _strip_models(str(m.get("name", "")))
        k = rank_model(name)
        if k is not None and (best_key is None or k > best_key):
            best, best_key = name, k
    return best


def _retry_delay(res: HttpResult) -> float | None:
    ra = parse_retry_after(res.headers)
    if ra is not None:
        return ra
    err = res.data.get("error") if isinstance(res.data, dict) else None
    details = (err.get("details") or []) if isinstance(err, dict) else []
    for d in details if isinstance(details, list) else []:
        if isinstance(d, dict) and d.get("retryDelay"):
            m = re.match(r"^\s*([0-9.]+)\s*s?\s*$", str(d["retryDelay"]))
            if m:
                return float(m.group(1))
    return None


def _daily_quota(res: HttpResult) -> bool:
    t = res.text
    return "PerDay" in t and "PerMinute" not in t


class GeminiBackend(Backend):
    provider = "gemini"

    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 300.0, concurrency: int = 1, max_tokens_override: int | None = None,
                 cache: Any = None, use_cache: bool = True, max_attempts: int = 4) -> None:
        super().__init__(model=_strip_models(model or DEFAULT_MODEL), effort="", concurrency=concurrency,
                         cache=cache, use_cache=use_cache, max_attempts=max_attempts)
        if not api_key:
            raise LLMUnavailable("Не задан ключ GEMINI_API_KEY. " + KEY_HELP)
        register_secret(api_key)
        self._key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_tokens_override = max_tokens_override
        self._out_cap: int | None = None
        self._discovered = False
        self._model_lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._key}

    def build_body(self, system: str, user: str, max_tokens: int) -> dict:
        n = self.max_tokens_override or max(max_tokens, 32768)
        if self._out_cap:
            n = min(n, self._out_cap)
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": n},
        }

    def _discover(self, failed_model: str) -> bool:
        """Pick a current flash model after a 404. True when the model was changed."""
        with self._model_lock:
            if self.model != failed_model:  # another thread already switched
                return True
            if self._discovered:
                return False
            self._discovered = True
            models: list[dict] = []
            token = ""
            for _ in range(5):
                url = self.base_url + "/v1beta/models?pageSize=1000" + ("&pageToken=" + quote(token) if token else "")
                res = request_json("GET", url, headers=self._headers(), timeout=min(self.timeout, 60.0))
                if res.status != 200 or not isinstance(res.data, dict):
                    return False
                models.extend(res.data.get("models") or [])
                token = res.data.get("nextPageToken") or ""
                if not token:
                    break
            new = pick_model(models)
            if not new or new == failed_model:
                return False
            self.model = new
            self.add_note("Модель Gemini «%s» недоступна (снята с обслуживания или нет доступа) — "
                          "автоматически выбрана «%s»." % (failed_model, new))
            return True

    def _raw(self, system: str, user: str, max_tokens: int, *, effort: str | None = None) -> tuple[str, Usage]:
        for _ in range(3):
            model = self.model
            url = "%s/v1beta/models/%s:generateContent" % (self.base_url, quote(model, safe="-._"))
            t0 = time.monotonic()
            res = request_json("POST", url, headers=self._headers(), body=self.build_body(system, user, max_tokens),
                               timeout=self.timeout)
            if res.status == 200:
                return self._parse(res, max_tokens, time.monotonic() - t0)
            msg = error_message(res)
            low = (msg + " " + res.text[:2000]).lower()
            if res.status == 404 or (res.status == 400 and "not found" in low and "model" in low):
                if self._discover(model):
                    continue
                raise LLMUnavailable("Модель Gemini «%s» не найдена, и подходящую замену подобрать не удалось. "
                                     "Укажите в .env GEMINI_MODEL=gemini-flash-latest." % model, detail=msg)
            if res.status == 400 and ("api_key_invalid" in low or "api key not valid" in low or "api key expired" in low):
                raise LLMUnavailable("Ключ GEMINI_API_KEY не принят (неверный или просрочен). " + KEY_HELP, detail=msg)
            if res.status == 400 and "location is not supported" in low:
                raise LLMUnavailable("Gemini API недоступен из вашего региона. Используйте другой провайдер "
                                     "(ANTHROPIC_API_KEY, OPENAI_API_KEY) или --provider offline.", detail=msg)
            if res.status == 400 and ("maxoutputtokens" in low or "max_output_tokens" in low) and not self._out_cap:
                self._out_cap = 8192
                self.add_note("Модель %s не принимает большой лимит ответа — лимит снижен до 8192 токенов." % model)
                continue
            if res.status in (401, 403):
                raise LLMUnavailable("Ключ GEMINI_API_KEY не принят или у него нет доступа к модели %s (%d). %s"
                                     % (model, res.status, KEY_HELP), detail=msg)
            if res.status == 429:
                if _daily_quota(res):
                    raise LLMUnavailable("Дневная квота бесплатного тарифа Gemini исчерпана. Повторите завтра "
                                         "или используйте другой ключ.", detail=msg)
                raise LLMTransient("Gemini: превышен лимит запросов (429).", retry_after=_retry_delay(res),
                                   kind="rate", detail=msg)
            if res.status in (408,) or res.status >= 500:
                raise LLMTransient("Gemini: временная ошибка сервера (%d)." % res.status,
                                   retry_after=parse_retry_after(res.headers), kind="server", detail=msg)
            raise LLMUnavailable("Gemini отклонил запрос (%d): %s" % (res.status, msg[:200]), detail=msg)
        raise LLMUnavailable("Gemini: не удалось подобрать рабочие параметры запроса.")

    def _parse(self, res: HttpResult, max_tokens: int, seconds: float) -> tuple[str, Usage]:
        d = res.data if isinstance(res.data, dict) else {}
        um = d.get("usageMetadata") or {}
        prompt = int(um.get("promptTokenCount") or 0)
        cached = int(um.get("cachedContentTokenCount") or 0)
        usage = Usage(input_tokens=max(0, prompt - cached),
                      output_tokens=int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0),
                      cache_read=cached, seconds=seconds)
        pf = d.get("promptFeedback") or {}
        if pf.get("blockReason"):
            e = LLMRefused("Gemini отказался обрабатывать запрос (причина: %s)." % pf.get("blockReason"))
            e.usage = usage
            raise e
        cands = d.get("candidates") or []
        if not cands:
            e2 = LLMBadOutput("Gemini вернул пустой ответ без вариантов.")
            e2.usage = usage
            raise e2
        c = cands[0] if isinstance(cands[0], dict) else {}
        reason = str(c.get("finishReason") or "")
        parts = ((c.get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if reason in _REFUSAL_REASONS:
            e3 = LLMRefused("Gemini прервал ответ по правилам безопасности (%s)." % reason)
            e3.usage = usage
            raise e3
        if reason == "MAX_TOKENS":
            e4 = LLMBadOutput("Ответ Gemini обрезан: не хватило лимита токенов ответа. Уменьшите пакет "
                              "или задайте больший LLM_MAX_TOKENS.")
            e4.kind = "truncated"
            e4.usage = usage
            raise e4
        if not text.strip():
            e5 = LLMBadOutput("Gemini вернул пустой текст (finishReason=%s)." % (reason or "?"))
            e5.usage = usage
            raise e5
        return text, usage
